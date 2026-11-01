"""Contract drift tests: what paypal_escrow.py sends / the mock returns vs paypal_subset.openapi.yaml.

Needs requirements-dev.txt (jsonschema, openapi-spec-validator, PyYAML); skipped otherwise.
"""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

yaml = pytest.importorskip("yaml")
jsonschema = pytest.importorskip("jsonschema")
openapi_spec_validator = pytest.importorskip("openapi_spec_validator")

from backend.app.services.orchestrator import EscrowOrchestrator  # noqa: E402
from backend.app.services.paypal_escrow import PayPalEscrowEngine  # noqa: E402
from backend.app.services.paypal_mock import MockPayPalEscrowEngine  # noqa: E402
from backend.apimatic_validation import export_openapi  # noqa: E402

DIR = Path(export_openapi.__file__).parent
SPEC = yaml.safe_load((DIR / "paypal_subset.openapi.yaml").read_text())


# --------------------------------------------------------------------------- helpers
def _resolve(schema):
    while "$ref" in schema:
        node = SPEC
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def validate(name, instance):
    """JSON-schema validation of `instance` against components.schemas[name]."""
    root = {"$ref": f"#/components/schemas/{name}", "components": SPEC["components"]}
    jsonschema.Draft202012Validator(root).validate(instance)


def unknown_fields(schema, value, path="$"):
    """Fields present in `value` that the spec does not declare (catches renamed/invented fields)."""
    schema = _resolve(schema)
    out = []
    if isinstance(value, dict) and "properties" in schema:
        for k, v in value.items():
            if k not in schema["properties"]:
                out.append(f"{path}.{k}")
            else:
                out += unknown_fields(schema["properties"][k], v, f"{path}.{k}")
    elif isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            out += unknown_fields(schema["items"], v, f"{path}[{i}]")
    return out


def check(name, instance):
    validate(name, instance)
    extra = unknown_fields({"$ref": f"#/components/schemas/{name}"}, instance)
    assert not extra, f"fields not in PayPal spec ({name}): {extra}"


def spec_has(method, path):
    for tmpl, item in SPEC["paths"].items():
        if method.lower() in item and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", tmpl), path):
            return item[method.lower()]
    return None


class Recorder:
    def __init__(self):
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        p = request.url.path
        if p == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
        if p.endswith("/void"):
            return httpx.Response(204)
        if p.endswith("/capture"):
            return httpx.Response(201, json={"id": "CAP1", "status": "COMPLETED"})
        if p == "/v1/payments/payouts":
            return httpx.Response(201, json={"batch_header": {
                "payout_batch_id": "B1", "batch_status": "PENDING", "sender_batch_header": {}}})
        if p.endswith("verify-webhook-signature"):
            return httpx.Response(200, json={"verification_status": "SUCCESS"})
        return httpx.Response(201, json={"id": "O1", "status": "CREATED"})

    def last(self):
        return self.requests[-1]

    def body(self):
        return json.loads(self.last().content)


@pytest.fixture
def rec_engine():
    rec = Recorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec.handler))
    return rec, PayPalEscrowEngine("id", "secret", webhook_id="WH1", http_client=client)


# --------------------------------------------------------------------------- spec sanity
def test_paypal_subset_spec_is_valid_openapi():
    openapi_spec_validator.validate(SPEC)


def test_unverified_markers_are_explicit():
    # Guard: unverified fields must be flagged, never silently asserted.
    text = (DIR / "paypal_subset.openapi.yaml").read_text()
    assert "x-aegispay-unverified: true" in text


# --------------------------------------------------------------------------- requests we build
async def test_oauth_token_request(rec_engine):
    rec, eng = rec_engine
    await eng.get_access_token()
    req = rec.last()
    assert spec_has("post", req.url.path)
    assert req.headers["authorization"].startswith("Basic ")
    form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
    assert form == {"grant_type": "client_credentials"}


async def test_create_order_request_matches_spec(rec_engine):
    rec, eng = rec_engine
    await eng.create_escrow_authorization_order("agr_1", 5_000_000, "x" * 300)
    req = rec.last()
    assert spec_has("post", req.url.path) and req.headers["paypal-request-id"]
    body = rec.body()
    check("OrderRequest", body)
    assert body["intent"] == "AUTHORIZE"
    assert body["payment_source"]["paypal"]["attributes"]["vault"] == {
        "store_in_vault": "ON_SUCCESS", "usage_type": "MERCHANT"}


async def test_create_order_without_vault_matches_spec(rec_engine):
    rec, eng = rec_engine
    await eng.create_escrow_authorization_order("agr_1", 10_000, "d", enable_vault=False)
    check("OrderRequest", rec.body())
    assert "payment_source" not in rec.body()


@pytest.mark.xfail(strict=True, reason=(
    "REAL DRIFT: paypal_escrow.capture_authorization sends `is_final_capture`; PayPal's Payments v2 "
    "capture_request field is `final_capture` (developer.paypal.com/api/payments/v2/schema.json). "
    "PayPal ignores the unknown field, so every capture silently uses the default final_capture=false "
    "and the authorization stays open. Fix in paypal_escrow.py (and drop this xfail)."))
async def test_capture_request_matches_spec(rec_engine):
    rec, eng = rec_engine
    await eng.capture_authorization("agr_1", "AUTH1", 4_500_000)
    req = rec.last()
    assert spec_has("post", req.url.path)
    check("CaptureRequest", rec.body())


async def test_capture_request_known_good_parts(rec_engine):
    """Non-drift parts of the capture body (path, amount, note length) still validated."""
    rec, eng = rec_engine
    await eng.capture_authorization("agr_1", "AUTH1", 4_500_000)
    body = rec.body()
    body.pop("is_final_capture")  # the drifted field, tracked by the xfail above
    check("CaptureRequest", body)
    assert body["amount"] == {"currency_code": "USD", "value": "4.50"}


async def test_void_request_has_no_body(rec_engine):
    rec, eng = rec_engine
    out = await eng.void_authorization("agr_1", "AUTH1")
    req = rec.last()
    assert spec_has("post", req.url.path).get("operationId") == "authorizationsVoid"
    assert req.content == b""
    assert out["ok"] is True


async def test_payout_request_matches_spec(rec_engine):
    rec, eng = rec_engine
    await eng.dispatch_worker_payout("agr_1", "w@example.com", 3_000_000, 0.95)
    req = rec.last()
    assert spec_has("post", req.url.path) and req.headers["paypal-request-id"]
    body = rec.body()
    check("CreatePayoutRequest", body)
    assert body["items"][0]["amount"] == {"value": "3.00", "currency": "USD"}  # Payouts uses `currency`, not currency_code


async def test_verify_webhook_request_matches_spec(rec_engine):
    rec, eng = rec_engine
    ok = await eng.verify_webhook_signature("t1", "2026-11-01T00:00:00Z", "https://c", "SHA256withRSA", "sig", {"id": "WH-1"})
    assert ok is True
    assert spec_has("post", rec.last().url.path)
    body = rec.body()
    check("VerifyWebhookSignatureRequest", body)
    assert body["webhook_id"] == "WH1"


# --------------------------------------------------------------------------- mock engine responses
async def test_mock_order_response_matches_spec():
    out = await MockPayPalEscrowEngine().create_escrow_authorization_order("agr_1", 5_000_000, "d")
    check("Order", out)


async def test_mock_capture_response_matches_spec():
    out = await MockPayPalEscrowEngine().capture_authorization("agr_1", "A", 4_500_000)
    check("Capture", out["capture_data"])


async def test_mock_capture_decline_matches_error_shape():
    eng = MockPayPalEscrowEngine()
    eng.decline_captures_for.add("agr_1")
    out = await eng.capture_authorization("agr_1", "A", 1_000_000)
    check("Error", out["error_details"])


@pytest.mark.xfail(strict=True, reason=(
    "Mock drift: MockPayPalEscrowEngine payout_response includes an `items` array; PayPal's "
    "POST /v1/payments/payouts 201 response only has batch_header + links "
    "(payouts-batch/v1 schema `payout`). Items are only available via GET /v1/payments/payouts/{id}."))
async def test_mock_payout_response_matches_spec():
    out = await MockPayPalEscrowEngine().dispatch_worker_payout("agr_1", "w@example.com", 3_000_000, 0.9)
    check("CreatePayoutResponse", out["payout_response"])


async def test_mock_payout_response_core_fields_valid():
    out = await MockPayPalEscrowEngine().dispatch_worker_payout("agr_1", "w@example.com", 3_000_000, 0.9)
    validate("CreatePayoutResponse", out["payout_response"])


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "unclaimed"])
def test_webhook_event_envelope_matches_spec(outcome):
    ev = EscrowOrchestrator.build_payout_event("B1", outcome)
    jsonschema.Draft202012Validator(
        {"$ref": "#/components/schemas/PayoutItemWebhookEvent", "components": SPEC["components"]}
    ).validate({**ev, "resource": {"payout_batch_id": ev["resource"]["payout_batch_id"]}})
    assert ev["event_type"] in _resolve(SPEC["components"]["schemas"]["PayoutItemWebhookEvent"]["properties"]["event_type"])["enum"]


@pytest.mark.xfail(strict=True, reason=(
    "Unverified-enum drift: EscrowOrchestrator.build_payout_event sets resource.transaction_status to "
    "SUCCEEDED/FAILED/UNCLAIMED; PayPal payout-item transaction_status values are SUCCESS/FAILED/UNCLAIMED/... "
    "(see x-aegispay-unverified in the spec). Only affects demo-generated events; the handler keys off event_type."))
def test_webhook_event_transaction_status_matches_spec():
    ev = EscrowOrchestrator.build_payout_event("B1", "succeeded")
    validate("PayoutItemWebhookEvent", ev)


# --------------------------------------------------------------------------- our own API spec
def test_aegispay_api_spec_is_valid_and_fresh():
    spec = yaml.safe_load((DIR / "aegispay_api.openapi.yaml").read_text())
    openapi_spec_validator.validate(spec)
    assert export_openapi.render() == (DIR / "aegispay_api.openapi.yaml").read_text(), (
        "aegispay_api.openapi.yaml is stale: run `python -m backend.apimatic_validation.export_openapi`")
    assert "/api/v1/webhooks/paypal" in spec["paths"]
