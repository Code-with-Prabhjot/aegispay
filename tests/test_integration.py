"""Integration tests: arbiter factories, frontend-facing API shapes, payout retry idempotency."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.services.arbitration_engine import CodeExecutionRunner
from backend.app.services.llm_judge import HeuristicJudge
from backend.app.services.paypal_escrow import PayPalEscrowEngine
from backend.app.services.paypal_mock import MockPayPalEscrowEngine

P = "/api/v1"


def make_cfg(tmp_path, **over):
    return Settings(_env_file=None, PAYPAL_MODE="mock", SANDBOX_MODE="subprocess",
                    DATABASE_URL=f"sqlite:///{tmp_path}/i.db", **over)


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(make_cfg(tmp_path), engine=MockPayPalEscrowEngine())) as c:
        yield c


# ---------------------------------------------------------------- arbiter factories
def test_orchestrator_uses_arbiter_factories(tmp_path, monkeypatch):
    from backend.app.services import orchestrator as orch_mod

    sentinel_runner, sentinel_judge = object(), object()
    seen = {}
    monkeypatch.setattr(orch_mod, "get_execution_runner",
                        lambda s: seen.setdefault("runner", sentinel_runner))
    monkeypatch.setattr(orch_mod, "get_semantic_judge",
                        lambda s: seen.setdefault("judge", sentinel_judge))
    app = create_app(make_cfg(tmp_path), engine=MockPayPalEscrowEngine())
    arbiter = app.state.orchestrator.arbiter
    assert arbiter.runner is sentinel_runner and arbiter.judge is sentinel_judge
    app.state.repo.db.close()


def test_default_arbiter_is_subprocess_and_heuristic(tmp_path):
    app = create_app(make_cfg(tmp_path), engine=MockPayPalEscrowEngine())
    arbiter = app.state.orchestrator.arbiter
    assert isinstance(arbiter.runner, CodeExecutionRunner)
    assert isinstance(arbiter.judge, HeuristicJudge)
    app.state.repo.db.close()


# ---------------------------------------------------------- frontend contract shapes
@pytest.mark.parametrize("name,state", [
    ("clean_pass", "SETTLED_COMPLETE"),
    ("PARTIAL_DEFECT", "SETTLED_COMPLETE"),
    ("Malicious_Injection", "ESCROW_VOIDED_REFUNDED"),
])
def test_scenario_names_case_insensitive(client, name, state):
    r = client.post(f"{P}/demo/scenarios/{name}")
    assert r.status_code == 200, r.text
    assert r.json()["fsm_state"] == state


def test_frontend_contract_shapes(client):
    d = client.post(f"{P}/demo/scenarios/partial_defect").json()
    aid = d["agreement_id"]

    detail = client.get(f"{P}/contracts/{aid}").json()
    for k in ("agreement_id", "fsm_state", "budget_micros", "budget_usd", "created_at", "updated_at",
              "paypal_order_id", "paypal_authorization_id", "paypal_capture_id",
              "paypal_payout_batch_id", "verification_score", "arbitration_report", "transitions"):
        assert k in detail, k
    t = detail["transitions"][0]
    assert set(t) >= {"from_state", "to_state", "created_at"} and t["from_state"] is None
    rep = detail["arbitration_report"]
    for k in ("s_pytest", "s_ast", "s_semantic", "composite_score", "passed_tests", "total_tests",
              "ast_safe", "ast_violations", "stdout", "settlement"):
        assert k in rep, k
    for k in ("decision", "capture_micros", "buyer_released_micros", "platform_fee_micros",
              "paypal_rail_fee_micros", "worker_net_payout_micros"):
        assert k in rep["settlement"], k

    lst = client.get(f"{P}/contracts").json()
    assert isinstance(lst["contracts"], list) and lst["count"] == 1
    assert lst["contracts"][0]["agreement_id"] == aid

    led = client.get(f"{P}/ledger").json()
    assert isinstance(led["entries"], list) and isinstance(led["balances"], dict)
    assert all(isinstance(v, int) for v in led["balances"].values())
    assert led["balanced"] is True
    for k in ("entry_id", "debit_account", "credit_account", "amount_micros", "reference_id",
              "currency", "created_at", "description"):
        assert k in led["entries"][0], k
    assert d["balances"] == led["balances"] and d["ledger_entries"]

    h = client.get(f"{P}/health").json()
    assert h["status"] == "ok"


def test_submit_body_shape(client):
    r = client.post(f"{P}/contracts", json={
        "buyer_agent_id": "b", "worker_agent_id": "w", "worker_paypal_email": "w@example.com",
        "budget_micros": 5_000_000, "task_description": "do the thing"})
    aid = r.json()["agreement_id"]
    assert client.post(f"{P}/contracts/{aid}/submit", json={}).status_code == 422
    ok = client.post(f"{P}/contracts/{aid}/submit",
                     json={"source_code": "def f():\n    return 1\n",
                           "test_suite": "from deliverable import f\ndef test_f():\n    assert f() == 1\n"})
    assert ok.status_code == 200


# ---------------------------------------------------- payout retry idempotency (real engine)
async def _payout(attempt):
    bodies = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2/token" in request.url.path:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        bodies.append((json.loads(request.content), request.headers["PayPal-Request-Id"]))
        return httpx.Response(201, json={"batch_header": {"payout_batch_id": f"B{attempt}"}})

    eng = PayPalEscrowEngine("id", "secret", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        await eng.dispatch_worker_payout("agr_1", "w@example.com", 14_000_000, 0.96, attempt=attempt)
    finally:
        await eng.close()
    body, req_id = bodies[0]
    return (body["sender_batch_header"]["sender_batch_id"], body["items"][0]["sender_item_id"], req_id)


async def test_payout_keys_vary_per_attempt_but_are_stable_within_one():
    a1, a1_again, a2 = await _payout(1), await _payout(1), await _payout(2)
    assert a1 == a1_again
    assert a1[0] != a2[0] and a1[1] != a2[1] and a1[2] != a2[2]


async def test_orchestrator_retry_passes_incremented_attempt(tmp_path):
    cfg = make_cfg(tmp_path)

    class Spy(MockPayPalEscrowEngine):
        attempts = []

        async def dispatch_worker_payout(self, *a, attempt=1, **kw):
            self.attempts.append(attempt)
            return await super().dispatch_worker_payout(*a, attempt=attempt, **kw)

    with TestClient(create_app(cfg, engine=Spy())) as c:
        d = c.post(f"{P}/demo/scenarios/clean_pass?payout_outcome=failed").json()
        first_batch = d["paypal_payout_batch_id"]
        r = c.post(f"{P}/contracts/{d['agreement_id']}/payout/retry").json()
    assert Spy.attempts == [1, 2]
    assert r["paypal_payout_batch_id"] != first_batch
