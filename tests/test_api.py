"""End-to-end API tests: FastAPI TestClient, mock PayPal, temp sqlite DB."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.services.paypal_mock import MockPayPalEscrowEngine, get_paypal_engine
from backend.app.services.scenario_presets import SCENARIO_CLEAN_PASS

P = "/api/v1"


@pytest.fixture()
def env(tmp_path):
    cfg = Settings(_env_file=None, PAYPAL_MODE="mock", DATABASE_URL=f"sqlite:///{tmp_path}/api.db")
    engine = MockPayPalEscrowEngine()
    app = create_app(cfg, engine=engine)
    with TestClient(app) as client:
        yield client, app, engine


def new_contract(client, **over):
    body = {
        "buyer_agent_id": "buyer", "worker_agent_id": "worker",
        "worker_paypal_email": "worker@example.com", "budget_micros": 15_000_000,
        "task_description": "Build a token bucket rate limiter",
    }
    body.update(over)
    r = client.post(f"{P}/contracts", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def submit_preset(client, aid, scenario=SCENARIO_CLEAN_PASS):
    return client.post(f"{P}/contracts/{aid}/submit",
                       json={"source_code": scenario.worker_code, "test_suite": scenario.buyer_test_code})


def webhook(client, batch_id, outcome, event_id):
    from backend.app.services.orchestrator import EscrowOrchestrator
    return client.post(f"{P}/webhooks/paypal",
                       json=EscrowOrchestrator.build_payout_event(batch_id, outcome, event_id))


def test_health_and_cors(env):
    client, _, _ = env
    r = client.get(f"{P}/health", headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 200 and r.json()["mock"] is True
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_factory_picks_mock():
    assert get_paypal_engine(Settings(_env_file=None, PAYPAL_MODE="mock")).is_mock
    assert get_paypal_engine(Settings(_env_file=None, PAYPAL_MODE="sandbox")).is_mock
    assert get_paypal_engine(Settings(_env_file=None, PAYPAL_MODE="sandbox",
                                      PAYPAL_CLIENT_ID="your_paypal_sandbox_client_id_here",
                                      PAYPAL_CLIENT_SECRET="your_paypal_sandbox_client_secret_here")).is_mock
    real = get_paypal_engine(Settings(_env_file=None, PAYPAL_MODE="sandbox",
                                      PAYPAL_CLIENT_ID="AbC123realid", PAYPAL_CLIENT_SECRET="EsEcReT987"))
    assert not getattr(real, "is_mock", False)


def test_mock_ids_deterministic():
    import asyncio
    a, b = MockPayPalEscrowEngine(), MockPayPalEscrowEngine()
    o1 = asyncio.run(a.create_escrow_authorization_order("agr", 1_000_000, "d"))
    o2 = asyncio.run(b.create_escrow_authorization_order("agr", 1_000_000, "d"))
    assert o1 == o2 and o1["id"].startswith("MOCKORDER-")


def test_create_contract_locks_authorization(env):
    client, app, _ = env
    c = new_contract(client)
    assert c["fsm_state"] == "PAYPAL_AUTH_LOCKED"
    assert c["paypal_authorization_id"].startswith("MOCKAUTH-")
    assert [t["to_state"] for t in c["transitions"]] == ["AGREEMENT_DRAFTED", "PAYPAL_AUTH_LOCKED"]
    led = client.get(f"{P}/ledger").json()
    assert led["balances"]["paypal_escrow_hold"] == 15_000_000
    assert len(client.get(f"{P}/contracts").json()["contracts"]) == 1
    assert client.get(f"{P}/contracts/{c['agreement_id']}").json()["agreement_id"] == c["agreement_id"]
    assert client.get(f"{P}/contracts/missing").status_code == 404


def test_validation_error(env):
    client, _, _ = env
    assert client.post(f"{P}/contracts", json={"buyer_agent_id": "b"}).status_code == 422


def test_manual_flow_full_capture_then_webhook_settles(env):
    client, _, _ = env
    c = new_contract(client)
    aid = c["agreement_id"]
    r = submit_preset(client, aid)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["fsm_state"] == "PAYOUT_DISPATCHED"
    assert d["arbitration_report"]["settlement"]["decision"] == "FULL_CAPTURE"
    states = [t["to_state"] for t in d["transitions"]]
    assert states == ["AGREEMENT_DRAFTED", "PAYPAL_AUTH_LOCKED", "SANDBOX_EXECUTING",
                      "ARBITRATION_AUDITING", "FULL_CAPTURE_PENDING", "PAYPAL_CAPTURED", "PAYOUT_DISPATCHED"]
    assert webhook(client, d["paypal_payout_batch_id"], "succeeded", "WH-A").json()["status"] == "processed"
    assert client.get(f"{P}/contracts/{aid}").json()["fsm_state"] == "SETTLED_COMPLETE"
    # cannot submit again
    assert submit_preset(client, aid).status_code == 409


@pytest.mark.parametrize("name,state,decision,bal", [
    ("clean_pass", "SETTLED_COMPLETE", "FULL_CAPTURE",
     {"worker_payable": 14_000_000, "platform_fee_revenue": 750_000, "paypal_rail_expense": 250_000,
      "paypal_escrow_hold": 0}),
    ("partial_defect", "SETTLED_COMPLETE", "PARTIAL_CAPTURE",
     {"worker_payable": 9_730_000, "platform_fee_revenue": 520_000, "paypal_rail_expense": 250_000,
      "buyer_vault": 4_500_000, "paypal_escrow_hold": 0}),
    ("malicious_injection", "ESCROW_VOIDED_REFUNDED", "VOID_AUTHORIZATION",
     {"worker_payable": 0, "buyer_vault": 15_000_000, "paypal_escrow_hold": 0}),
])
def test_demo_scenarios(env, name, state, decision, bal):
    client, _, _ = env
    r = client.post(f"{P}/demo/scenarios/{name}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["fsm_state"] == state
    assert d["arbitration_report"]["settlement"]["decision"] == decision
    for k, v in bal.items():
        assert d["balances"][k] == v, (k, d["balances"])
    assert d["ledger_entries"]
    led = client.get(f"{P}/ledger").json()
    assert led["balanced"] is True
    assert sum(led["balances"].values()) == 15_000_000


def test_demo_unknown_scenario_and_bad_outcome(env):
    client, _, _ = env
    assert client.post(f"{P}/demo/scenarios/nope").status_code == 404
    assert client.post(f"{P}/demo/scenarios/clean_pass?payout_outcome=zzz").status_code == 422


def test_capture_declined_halts_payout(env):
    client, _, engine = env
    c = new_contract(client, agreement_id="agr_decline")
    engine.decline_captures_for.add("agr_decline")
    d = submit_preset(client, c["agreement_id"]).json()
    assert d["fsm_state"] == "CAPTURE_DECLINED"
    assert d["paypal_payout_batch_id"] is None
    assert not any(t["to_state"] == "PAYOUT_DISPATCHED" for t in d["transitions"])
    assert client.post(f"{P}/contracts/agr_decline/payout/retry").status_code == 409


def test_auth_expiry(env):
    client, app, _ = env
    orch = app.state.orchestrator
    c = new_contract(client)
    future = datetime.now(timezone.utc) + timedelta(hours=73)
    orch.clock = lambda: future
    r = submit_preset(client, c["agreement_id"])
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    d = client.get(f"{P}/contracts/{c['agreement_id']}").json()
    assert d["fsm_state"] == "AUTH_EXPIRED"
    led = client.get(f"{P}/ledger").json()["balances"]
    assert led["paypal_escrow_hold"] == 0 and led["buyer_vault"] == 15_000_000


def test_check_expiry_endpoint(env):
    client, app, _ = env
    c = new_contract(client)
    assert client.post(f"{P}/contracts/{c['agreement_id']}/check-expiry").json()["fsm_state"] == "PAYPAL_AUTH_LOCKED"
    app.state.orchestrator.clock = lambda: datetime.now(timezone.utc) + timedelta(hours=80)
    assert client.post(f"{P}/contracts/{c['agreement_id']}/check-expiry").json()["fsm_state"] == "AUTH_EXPIRED"


def test_payout_failed_then_retry_then_settled(env):
    client, _, _ = env
    d = client.post(f"{P}/demo/scenarios/clean_pass?payout_outcome=failed").json()
    aid = d["agreement_id"]
    assert d["fsm_state"] == "PAYOUT_FAILED_REVERSED"
    assert d["balances"]["worker_payable"] == 0
    assert d["balances"]["paypal_escrow_hold"] == 14_000_000  # reserve re-credited
    r = client.post(f"{P}/contracts/{aid}/payout/retry")
    assert r.status_code == 200 and r.json()["fsm_state"] == "PAYOUT_DISPATCHED"
    assert client.get(f"{P}/ledger").json()["balances"]["worker_payable"] == 14_000_000
    assert webhook(client, r.json()["paypal_payout_batch_id"], "succeeded", "WH-RETRY-OK").status_code == 200
    assert client.get(f"{P}/contracts/{aid}").json()["fsm_state"] == "SETTLED_COMPLETE"
    # cannot retry a settled contract
    assert client.post(f"{P}/contracts/{aid}/payout/retry").status_code == 409


def test_unclaimed_treated_as_failure(env):
    client, _, _ = env
    d = client.post(f"{P}/demo/scenarios/clean_pass?payout_outcome=unclaimed").json()
    assert d["fsm_state"] == "PAYOUT_FAILED_REVERSED"


def test_webhook_dedupe(env):
    client, _, _ = env
    c = new_contract(client)
    d = submit_preset(client, c["agreement_id"]).json()
    batch = d["paypal_payout_batch_id"]
    assert webhook(client, batch, "failed", "WH-DUP").json()["status"] == "processed"
    bal = client.get(f"{P}/ledger").json()["balances"]
    again = webhook(client, batch, "failed", "WH-DUP")
    assert again.status_code == 200 and again.json()["status"] == "duplicate"
    assert client.get(f"{P}/ledger").json()["balances"] == bal  # no double reversal
    n = len(client.get(f"{P}/contracts/{c['agreement_id']}").json()["transitions"])
    webhook(client, batch, "failed", "WH-DUP")
    assert len(client.get(f"{P}/contracts/{c['agreement_id']}").json()["transitions"]) == n


def test_webhook_illegal_state_and_unknown_batch_are_ignored(env):
    client, _, _ = env
    d = client.post(f"{P}/demo/scenarios/clean_pass").json()  # already SETTLED_COMPLETE
    r = webhook(client, d["paypal_payout_batch_id"], "failed", "WH-LATE")
    assert r.status_code == 200 and r.json()["status"] == "ignored_illegal_state"
    assert client.get(f"{P}/contracts/{d['agreement_id']}").json()["fsm_state"] == "SETTLED_COMPLETE"
    assert webhook(client, "NOPE", "succeeded", "WH-X").json()["status"] == "ignored_unknown_payout"


def test_webhook_bad_requests_and_signature(env):
    client, _, engine = env
    assert client.post(f"{P}/webhooks/paypal", json={"event_type": "X"}).status_code == 400
    assert client.post(f"{P}/webhooks/paypal", content=b"not json").status_code == 400
    engine.accept_webhooks = False
    r = client.post(f"{P}/webhooks/paypal", json={"id": "WH-1", "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"})
    assert r.status_code == 401


def test_state_survives_restart(tmp_path):
    cfg = Settings(_env_file=None, PAYPAL_MODE="mock", DATABASE_URL=f"sqlite:///{tmp_path}/r.db")
    with TestClient(create_app(cfg)) as c1:
        aid = c1.post(f"{P}/demo/scenarios/clean_pass").json()["agreement_id"]
    with TestClient(create_app(cfg)) as c2:
        assert c2.get(f"{P}/contracts/{aid}").json()["fsm_state"] == "SETTLED_COMPLETE"
        led = c2.get(f"{P}/ledger").json()
        assert led["balances"]["platform_fee_revenue"] == 750_000
