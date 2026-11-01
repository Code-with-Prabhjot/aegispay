from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_demo_scenarios():
    r = client.post("/demo/CLEAN_PASS").json()
    assert r["contract"]["fsm_state"] == "SETTLED_COMPLETE"
    r = client.post("/demo/MALICIOUS_INJECTION").json()
    assert r["contract"]["fsm_state"] == "ESCROW_VOIDED_REFUNDED"
    assert client.get("/ledger").json()["conserved"] is True


def test_manual_flow_and_errors():
    body = {"buyer_agent_id": "b", "worker_agent_id": "w", "worker_paypal_email": "w@example.com",
            "budget_micros": 5_000_000, "task_description": "write add(a, b)"}
    aid = client.post("/agreements", json=body).json()["agreement_id"]
    assert client.post(f"/agreements/{aid}/run", json={"worker_code": "x=1", "buyer_test_code": "y=1"}).status_code == 409
    assert client.post(f"/agreements/{aid}/lock", json={"fund_buyer_vault": True}).json()["fsm_state"] == "PAYPAL_AUTH_LOCKED"
    assert client.get("/agreements/nope").status_code == 404
    assert client.post("/demo/NOPE").status_code == 404
