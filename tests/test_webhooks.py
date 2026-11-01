"""PayPal webhook listener: signature verification, per-event FSM + ledger effects, sandbox check."""

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.schemas import ArbitrationReport, FsmState
from backend.app.services import fsm
from backend.app.services.orchestrator import EscrowOrchestrator
from backend.app.services.paypal_escrow import (
    PayPalEscrowEngine,
    compute_settlement_split,
    usd_amount_to_micros,
)
from backend.app.services.paypal_mock import MockPayPalEscrowEngine
from backend.app.services.sandbox_check import REQUIRED_WEBHOOK_EVENTS, verify_sandbox_credentials

URL = "/api/v1/webhooks/paypal"
BUDGET = 15_000_000
AID = "agr_wh"
AUTH_ID = "AUTH-1"
CAPTURE_ID = "CAP-1"
SIGNATURE_HEADERS = {
    "paypal-transmission-id": "t1",
    "paypal-transmission-time": "2026-11-01T00:00:00Z",
    "paypal-cert-url": "https://api.sandbox.paypal.com/cert",
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-transmission-sig": "sig",
}


def amount(value):
    return {"currency_code": "USD", "value": value}


def authorization_created(event_id="WH-AUTH", value="15.00", auth_id=AUTH_ID, custom_id=AID):
    return {
        "id": event_id, "event_type": "PAYMENT.AUTHORIZATION.CREATED",
        "resource": {"id": auth_id, "status": "CREATED", "custom_id": custom_id, "amount": amount(value),
                     "supplementary_data": {"related_ids": {"order_id": "ORDER-1"}}},
    }


def capture_completed(event_id="WH-CAP", value="15.00", capture_id=CAPTURE_ID, auth_id=AUTH_ID):
    return {
        "id": event_id, "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {"id": capture_id, "status": "COMPLETED", "custom_id": AID, "amount": amount(value),
                     "supplementary_data": {"related_ids": {"authorization_id": auth_id}}},
    }


def authorization_voided(event_id="WH-VOID", auth_id=AUTH_ID):
    return {
        "id": event_id, "event_type": "PAYMENT.AUTHORIZATION.VOIDED",
        "resource": {"id": auth_id, "status": "VOIDED", "custom_id": AID},
    }


def make_app(tmp_path, engine=None, **settings):
    settings.setdefault("PAYPAL_MODE", "mock")
    cfg = Settings(_env_file=None, SANDBOX_MODE="subprocess",
                   DATABASE_URL=f"sqlite:///{tmp_path}/wh.db", **settings)
    return create_app(cfg, engine=engine or MockPayPalEscrowEngine())


@pytest.fixture()
def env(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        yield client, app


def seed(app, state, score=None):
    """Store a contract in `state` with its hold locked, plus a settlement report if `score` is given."""
    orch = app.state.orchestrator
    now = datetime.now(timezone.utc)
    c = fsm.ContractRecord(
        agreement_id=AID, buyer_agent_id="b", worker_agent_id="w", worker_paypal_email="w@example.com",
        budget_micros=BUDGET, task_description="Build a rate limiter", timeout_seconds=3600,
        fsm_state=state, created_at=now, updated_at=now,
    )
    if state != FsmState.AGREEMENT_DRAFTED:
        c.paypal_authorization_id = AUTH_ID
        c.authorized_at = now
        orch.ledger.deposit("buyer_vault", BUDGET, AID, "seed")
        orch.ledger.record_transfer("paypal_escrow_hold", "buyer_vault", BUDGET, AID, "seed")
    if score is not None:
        split = compute_settlement_split(BUDGET, score)
        c.verification_score = score
        c.report = ArbitrationReport(
            agreement_id=AID, s_pytest=score, s_ast=1.0, s_semantic=score, composite_score=score,
            passed_tests=1, total_tests=1, ast_safe=True, settlement=split,
        ).model_dump(mode="json")
    orch.repo.save_contract(c)
    return c


def contract(app):
    return app.state.orchestrator.get(AID)


def balances(app):
    return app.state.ledger.get_all_balances()


# ------------------------------------------------------------------- signature verification
class PayPalStub:
    """httpx transport standing in for PayPal's OAuth and verify-webhook-signature endpoints."""

    def __init__(self, verification_status="SUCCESS", verify_http=200, oauth_http=200, connect_error=False):
        self.verification_status = verification_status
        self.verify_http = verify_http
        self.oauth_http = oauth_http
        self.connect_error = connect_error
        self.verify_bodies = []

    def __call__(self, request):
        if self.connect_error:
            raise httpx.ConnectError("down")
        if request.url.path == "/v1/oauth2/token":
            if self.oauth_http != 200:
                return httpx.Response(self.oauth_http, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
        self.verify_bodies.append(json.loads(request.content))
        return httpx.Response(self.verify_http, json={"verification_status": self.verification_status})


def real_engine(stub, webhook_id="WH-REGISTERED"):
    return PayPalEscrowEngine("id", "secret", webhook_id=webhook_id,
                              http_client=httpx.AsyncClient(transport=httpx.MockTransport(stub)))


def sandbox_client(tmp_path, stub, webhook_id="WH-REGISTERED", **settings):
    return TestClient(make_app(tmp_path, engine=real_engine(stub, webhook_id), PAYPAL_MODE="sandbox", **settings))


def test_valid_signature_is_checked_with_paypal_then_processed(tmp_path):
    stub = PayPalStub()
    with sandbox_client(tmp_path, stub) as client:
        event = authorization_voided()
        r = client.post(URL, json=event, headers=SIGNATURE_HEADERS)
    assert r.status_code == 200 and r.json()["status"] == "ignored_unknown_contract"
    assert stub.verify_bodies == [{
        "transmission_id": "t1", "transmission_time": "2026-11-01T00:00:00Z",
        "cert_url": "https://api.sandbox.paypal.com/cert", "auth_algo": "SHA256withRSA",
        "transmission_sig": "sig", "webhook_id": "WH-REGISTERED", "webhook_event": event,
    }]


@pytest.mark.parametrize("stub", [PayPalStub("FAILURE"), PayPalStub(verify_http=400)])
def test_rejected_signature_is_401_and_changes_nothing(tmp_path, stub):
    app = make_app(tmp_path, engine=real_engine(stub), PAYPAL_MODE="sandbox")
    with TestClient(app) as client:
        seed(app, FsmState.VOID_PENDING, score=0.1)
        before = balances(app)
        assert client.post(URL, json=authorization_voided(), headers=SIGNATURE_HEADERS).status_code == 401
        assert contract(app).fsm_state == FsmState.VOID_PENDING
        assert balances(app) == before
        assert app.state.repo.get_webhook_event("WH-VOID") is None  # a genuine delivery can still land


@pytest.mark.parametrize("missing", sorted(SIGNATURE_HEADERS))
def test_missing_signature_header_is_401_without_calling_paypal(tmp_path, missing):
    stub = PayPalStub()
    headers = {k: v for k, v in SIGNATURE_HEADERS.items() if k != missing}
    with sandbox_client(tmp_path, stub) as client:
        assert client.post(URL, json=authorization_voided(), headers=headers).status_code == 401
    assert stub.verify_bodies == []


@pytest.mark.parametrize("stub", [PayPalStub(connect_error=True), PayPalStub(oauth_http=500)])
def test_paypal_outage_during_verification_is_503_so_paypal_redelivers(tmp_path, stub):
    with sandbox_client(tmp_path, stub) as client:
        assert client.post(URL, json=authorization_voided(), headers=SIGNATURE_HEADERS).status_code == 503


def test_unset_webhook_id_is_503_without_calling_paypal(tmp_path):
    stub = PayPalStub()
    with sandbox_client(tmp_path, stub, webhook_id="") as client:
        assert client.post(URL, json=authorization_voided(), headers=SIGNATURE_HEADERS).status_code == 503
    assert stub.verify_bodies == []


def test_mock_fallback_outside_mock_mode_fails_closed(tmp_path):
    # Sandbox mode with no credentials gets the always-accepting mock engine: forgeries must not pass.
    with TestClient(make_app(tmp_path, PAYPAL_MODE="sandbox")) as client:
        assert client.post(URL, json=authorization_voided()).status_code == 503


def test_bypass_flag_skips_verification(tmp_path):
    stub = PayPalStub("FAILURE")
    with sandbox_client(tmp_path, stub, PAYPAL_WEBHOOK_MOCK_BYPASS=True) as client:
        assert client.post(URL, json=authorization_voided()).status_code == 200
    assert stub.verify_bodies == []


def test_mock_engine_still_verifies_when_bypass_is_off(tmp_path):
    engine = MockPayPalEscrowEngine()
    engine.accept_webhooks = False
    with TestClient(make_app(tmp_path, engine=engine)) as client:
        assert client.post(URL, json=authorization_voided()).status_code == 401


@pytest.mark.parametrize("mode", ["live", "production"])
def test_bypass_flag_rejected_outside_mock_and_sandbox(mode):
    with pytest.raises(ValueError, match="PAYPAL_WEBHOOK_MOCK_BYPASS"):
        Settings(_env_file=None, PAYPAL_MODE=mode, PAYPAL_WEBHOOK_MOCK_BYPASS=True)


def test_bypass_defaults_off():
    assert Settings(_env_file=None).PAYPAL_WEBHOOK_MOCK_BYPASS is False


@pytest.mark.parametrize("body", [
    b"not json", b"[]", b'{"event_type": "X"}', b'{"id": "WH-1"}', b'{"id": 7, "event_type": "X"}',
    b'{"id": "WH-1", "event_type": "X", "resource": "nope"}',
])
def test_malformed_body_is_400(env, body):
    client, _ = env
    assert client.post(URL, content=body).status_code == 400


# ------------------------------------------------------------------------ AUTHORIZATION.CREATED
def test_authorization_created_locks_drafted_contract(env):
    client, app = env
    seed(app, FsmState.AGREEMENT_DRAFTED)
    r = client.post(URL, json=authorization_created())
    assert r.json() == {"status": "processed", "event_id": "WH-AUTH", "agreement_id": AID}
    c = contract(app)
    assert c.fsm_state == FsmState.PAYPAL_AUTH_LOCKED
    assert (c.paypal_authorization_id, c.paypal_order_id) == (AUTH_ID, "ORDER-1")
    assert c.authorized_at is not None
    assert balances(app)["paypal_escrow_hold"] == BUDGET


def test_authorization_created_after_sync_lock_is_a_no_op(env):
    client, app = env
    seed(app, FsmState.PAYPAL_AUTH_LOCKED)
    before = balances(app)
    assert client.post(URL, json=authorization_created()).json()["status"] == "already_applied"
    assert balances(app) == before


@pytest.mark.parametrize("value", ["14.99", "15.01", "abc", None])
def test_authorization_created_wrong_amount_does_not_lock(env, value):
    client, app = env
    seed(app, FsmState.AGREEMENT_DRAFTED)
    assert client.post(URL, json=authorization_created(value=value)).json()["status"] == "ignored_amount_mismatch"
    assert contract(app).fsm_state == FsmState.AGREEMENT_DRAFTED
    assert balances(app)["paypal_escrow_hold"] == 0


def test_authorization_created_for_other_authorization_is_ignored(env):
    client, app = env
    seed(app, FsmState.PAYPAL_AUTH_LOCKED)
    r = client.post(URL, json=authorization_created(auth_id="AUTH-OTHER"))
    assert r.json()["status"] == "ignored_unknown_contract"
    assert contract(app).paypal_authorization_id == AUTH_ID


def test_authorization_created_unknown_contract_is_ignored(env):
    client, _ = env
    r = client.post(URL, json=authorization_created(custom_id="agr_nope"))
    assert r.status_code == 200 and r.json()["status"] == "ignored_unknown_contract"


# ---------------------------------------------------------------------------- CAPTURE.COMPLETED
@pytest.mark.parametrize("pending,captured_seen,score,value,worker,buyer", [
    (FsmState.FULL_CAPTURE_PENDING, FsmState.PAYPAL_CAPTURED, 0.95, "15.00", 14_000_000, 0),
    (FsmState.PARTIAL_CAPTURE_PENDING, FsmState.PARTIAL_CAPTURED, 0.75, "10.50", 9_730_000, 4_500_000),
])
def test_capture_completed_recovers_lost_capture_response(env, pending, captured_seen, score, value, worker, buyer):
    client, app = env
    seed(app, pending, score=score)
    assert client.post(URL, json=capture_completed(value=value)).json()["status"] == "processed"
    c = contract(app)
    assert c.fsm_state == FsmState.PAYOUT_DISPATCHED
    assert c.paypal_capture_id == CAPTURE_ID and c.paypal_payout_batch_id
    assert captured_seen in [t.to_state for t in c.transitions]
    bal = balances(app)
    assert (bal["worker_payable"], bal["buyer_vault"], bal["paypal_escrow_hold"]) == (worker, buyer, 0)


def test_capture_completed_after_sync_capture_is_a_no_op(env):
    client, app = env
    c = seed(app, FsmState.PAYOUT_DISPATCHED, score=0.95)
    c.paypal_capture_id = CAPTURE_ID
    app.state.repo.save_contract(c)
    before = balances(app)
    assert client.post(URL, json=capture_completed()).json()["status"] == "already_applied"
    assert contract(app).fsm_state == FsmState.PAYOUT_DISPATCHED
    assert balances(app) == before


def test_capture_completed_wrong_amount_does_not_settle(env):
    client, app = env
    seed(app, FsmState.FULL_CAPTURE_PENDING, score=0.95)
    assert client.post(URL, json=capture_completed(value="1.00")).json()["status"] == "ignored_amount_mismatch"
    assert contract(app).fsm_state == FsmState.FULL_CAPTURE_PENDING
    assert balances(app)["worker_payable"] == 0


@pytest.mark.parametrize("state", [FsmState.PAYPAL_AUTH_LOCKED, FsmState.CAPTURE_DECLINED, FsmState.VOID_PENDING])
def test_capture_completed_in_wrong_state_never_pays_worker(env, state):
    client, app = env
    seed(app, state, score=0.95)
    assert client.post(URL, json=capture_completed()).json()["status"] == "ignored_illegal_state"
    assert contract(app).fsm_state == state
    assert balances(app)["worker_payable"] == 0


def test_payout_failure_after_capture_webhook_resumes_on_redelivery(env):
    client, app = env
    seed(app, FsmState.FULL_CAPTURE_PENDING, score=0.95)
    engine = app.state.orchestrator.engine
    real_dispatch = engine.dispatch_worker_payout

    async def down(*a, **k):
        raise httpx.ConnectError("down")

    engine.dispatch_worker_payout = down
    assert client.post(URL, json=capture_completed()).status_code == 502
    assert contract(app).fsm_state == FsmState.PAYPAL_CAPTURED
    settled = balances(app)

    engine.dispatch_worker_payout = real_dispatch
    r = client.post(URL, json=capture_completed())  # same event ID: PayPal's redelivery
    assert r.status_code == 200 and r.json()["status"] == "already_applied"
    assert contract(app).fsm_state == FsmState.PAYOUT_DISPATCHED
    assert balances(app) == settled  # settlement is not recorded twice


# ------------------------------------------------------------------------- AUTHORIZATION.VOIDED
def test_authorization_voided_recovers_lost_void_response(env):
    client, app = env
    seed(app, FsmState.VOID_PENDING, score=0.1)
    assert client.post(URL, json=authorization_voided()).json()["status"] == "processed"
    assert contract(app).fsm_state == FsmState.ESCROW_VOIDED_REFUNDED
    bal = balances(app)
    assert (bal["buyer_vault"], bal["paypal_escrow_hold"]) == (BUDGET, 0)


def test_authorization_voided_after_sync_void_is_a_no_op(env):
    client, app = env
    seed(app, FsmState.ESCROW_VOIDED_REFUNDED, score=0.1)
    before = balances(app)
    assert client.post(URL, json=authorization_voided()).json()["status"] == "already_applied"
    assert balances(app) == before


@pytest.mark.parametrize("state", [FsmState.PAYPAL_AUTH_LOCKED, FsmState.FULL_CAPTURE_PENDING, FsmState.PAYOUT_DISPATCHED])
def test_authorization_voided_in_wrong_state_is_ignored(env, state):
    client, app = env
    seed(app, state, score=0.95)
    before = balances(app)
    assert client.post(URL, json=authorization_voided()).json()["status"] == "ignored_illegal_state"
    assert contract(app).fsm_state == state
    assert balances(app) == before


def test_authorization_voided_unknown_authorization_is_ignored(env):
    client, app = env
    seed(app, FsmState.VOID_PENDING, score=0.1)
    r = client.post(URL, json=authorization_voided(auth_id="AUTH-OTHER"))
    assert r.json()["status"] == "ignored_unknown_contract"
    assert contract(app).fsm_state == FsmState.VOID_PENDING


# ------------------------------------------------------------------------------- PAYOUTS-ITEM.*
def dispatched(app, score=0.95):
    orch = app.state.orchestrator
    c = seed(app, FsmState.PAYOUT_DISPATCHED, score=score)
    split = ArbitrationReport(**c.report).settlement
    orch.ledger.record_settlement(AID, split)
    c.paypal_payout_batch_id = "BATCH-1"
    app.state.repo.save_contract(c)
    app.state.repo.save_payout(f"{AID}:payout", AID, "BATCH-1", split.worker_net_payout_micros,
                               "DISPATCHED", 1, datetime.now(timezone.utc))


def test_payout_item_succeeded_settles(env):
    client, app = env
    dispatched(app)
    r = client.post(URL, json=EscrowOrchestrator.build_payout_event("BATCH-1", "succeeded", "WH-OK"))
    assert r.json()["status"] == "processed"
    assert contract(app).fsm_state == FsmState.SETTLED_COMPLETE
    assert app.state.repo.get_payout_for_agreement(AID)["status"] == "SUCCEEDED"


@pytest.mark.parametrize("outcome,status", [("failed", "FAILED"), ("unclaimed", "UNCLAIMED")])
def test_payout_item_failure_reverses_to_escrow(env, outcome, status):
    client, app = env
    dispatched(app)
    assert client.post(URL, json=EscrowOrchestrator.build_payout_event("BATCH-1", outcome, "WH-F")).json()["status"] == "processed"
    assert contract(app).fsm_state == FsmState.PAYOUT_FAILED_REVERSED
    bal = balances(app)
    assert (bal["worker_payable"], bal["paypal_escrow_hold"]) == (0, 14_000_000)
    assert app.state.repo.get_payout_for_agreement(AID)["status"] == status


def test_payout_item_batch_id_nested_in_payout_item(env):
    client, app = env
    dispatched(app)
    event = {"id": "WH-N", "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
             "resource": {"payout_item": {"payout_batch_id": "BATCH-1"}}}
    assert client.post(URL, json=event).json()["status"] == "processed"


# ---------------------------------------------------------------------------------- idempotency
@pytest.mark.parametrize("state,score,event", [
    (FsmState.AGREEMENT_DRAFTED, None, authorization_created()),
    (FsmState.FULL_CAPTURE_PENDING, 0.95, capture_completed()),
    (FsmState.VOID_PENDING, 0.1, authorization_voided()),
])
def test_replayed_event_has_no_second_effect(env, state, score, event):
    client, app = env
    seed(app, state, score=score)
    assert client.post(URL, json=event).json()["status"] == "processed"
    n_transitions = len(contract(app).transitions)
    n_entries = len(app.state.ledger.entries)
    bal = balances(app)
    r = client.post(URL, json=event)
    assert r.status_code == 200 and r.json()["status"] == "duplicate"
    assert len(contract(app).transitions) == n_transitions
    assert len(app.state.ledger.entries) == n_entries
    assert balances(app) == bal


def test_unhandled_event_type_is_acknowledged_and_stored(env):
    client, app = env
    event = {"id": "WH-Z", "event_type": "PAYMENT.CAPTURE.REFUNDED", "resource": {"id": "R1"}}
    r = client.post(URL, json=event)
    assert r.status_code == 200 and r.json()["status"] == "ignored_event_type"
    stored = app.state.repo.get_webhook_event("WH-Z")
    assert (stored["resource_id"], stored["outcome"]) == ("R1", "ignored_event_type")


# -------------------------------------------------------------------------------- usd amount parse
@pytest.mark.parametrize("raw,expected", [
    (amount("10.50"), 10_500_000), (amount("0.01"), 10_000), (amount("15"), 15_000_000),
    ({"currency_code": "EUR", "value": "1.00"}, None), (amount("abc"), None), (amount("NaN"), None),
    (amount("Infinity"), None), (amount(None), None), (None, None), ("10.50", None),
])
def test_usd_amount_to_micros(raw, expected):
    assert usd_amount_to_micros(raw) == expected


# --------------------------------------------------------------------- sandbox credential check
GOOD = {"PAYPAL_MODE": "sandbox", "PAYPAL_CLIENT_ID": "AbC123", "PAYPAL_CLIENT_SECRET": "EsEcReT987",
        "PAYPAL_WEBHOOK_ID": "WH-REGISTERED"}


def registered(events=REQUIRED_WEBHOOK_EVENTS):
    return {"id": "WH-REGISTERED", "url": "https://aegis.test/api/v1/webhooks/paypal",
            "event_types": [{"name": e} for e in events]}


def paypal_api(oauth=200, webhook=200, body=None):
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(oauth, json={"access_token": "SECRET-TOKEN", "expires_in": 3600})
        assert request.url.path == "/v1/notifications/webhooks/WH-REGISTERED"
        assert request.url.host == "api-m.sandbox.paypal.com"
        return httpx.Response(webhook, json=body if body is not None else registered())
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def run_check(client=None, **over):
    return await verify_sandbox_credentials(Settings(_env_file=None, **{**GOOD, **over}), client)


async def test_sandbox_check_all_pass():
    results = await run_check(paypal_api())
    assert [(r.name, r.ok) for r in results] == [("mode", True), ("credentials", True), ("oauth", True), ("webhook", True)]


async def test_sandbox_check_accepts_wildcard_subscription():
    assert all(r.ok for r in await run_check(paypal_api(body=registered(["*"]))))


@pytest.mark.parametrize("mode", ["mock", "live"])
async def test_sandbox_check_refuses_other_modes(mode):
    results = await run_check(PAYPAL_MODE=mode)
    assert [(r.name, r.ok) for r in results] == [("mode", False)]


@pytest.mark.parametrize("over", [{"PAYPAL_CLIENT_ID": ""}, {"PAYPAL_CLIENT_SECRET": "your_paypal_sandbox_client_secret_here"}])
async def test_sandbox_check_flags_missing_credentials_without_calling_paypal(over):
    results = await run_check(**over)
    assert results[-1].name == "credentials" and not results[-1].ok


async def test_sandbox_check_reports_rejected_credentials():
    results = await run_check(paypal_api(oauth=401))
    assert results[-1].name == "oauth" and not results[-1].ok and "401" in results[-1].detail


async def test_sandbox_check_reports_unreachable_paypal():
    def down(request):
        raise httpx.ConnectError("down")
    results = await run_check(httpx.AsyncClient(transport=httpx.MockTransport(down)))
    assert results[-1].name == "oauth" and not results[-1].ok


async def test_sandbox_check_flags_placeholder_webhook_id():
    results = await run_check(paypal_api(), PAYPAL_WEBHOOK_ID="your_paypal_webhook_id_here")
    assert results[-1].name == "webhook" and not results[-1].ok


async def test_sandbox_check_flags_unregistered_webhook():
    results = await run_check(paypal_api(webhook=404))
    assert results[-1].name == "webhook" and "not registered" in results[-1].detail


async def test_sandbox_check_names_unsubscribed_events():
    results = await run_check(paypal_api(body=registered(REQUIRED_WEBHOOK_EVENTS[:-1])))
    assert not results[-1].ok and REQUIRED_WEBHOOK_EVENTS[-1] in results[-1].detail


async def test_sandbox_check_never_reveals_secrets():
    for client in (paypal_api(), paypal_api(oauth=401), paypal_api(webhook=500)):
        text = " ".join(r.detail for r in await run_check(client))
        for secret in (GOOD["PAYPAL_CLIENT_ID"], GOOD["PAYPAL_CLIENT_SECRET"], "SECRET-TOKEN"):
            assert secret not in text
