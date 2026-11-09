"""
tests/test_websocket_and_detail.py
Comprehensive test suite verifying:
1. Real-Time WebSocket Ledger Broadcaster (/api/v1/ws/ledger, /api/v1/ws/dashboard)
2. Initial state snapshot delivery & heartbeat ping/pong keepalive
3. Event stream broadcast on FSM transitions, arbiter evaluations, and ledger transactions
4. Master-Detail AG Grid schema endpoint (/api/v1/agreements/{id}/detail)
"""

from typing import Any, Dict, List
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import Repository
from backend.app.main import create_app
from backend.app.schemas import AgreementDetailResponse
from backend.app.services.broadcaster import LedgerConnectionManager
from backend.app.services.paypal_mock import MockPayPalEscrowEngine


@pytest.fixture
def test_context():
    """Isolated in-memory test environment."""
    settings = Settings(
        DATABASE_URL="sqlite:///:memory:",
        PAYPAL_MODE="mock",
        SANDBOX_MODE="subprocess",
    )
    repo = Repository.from_url(settings.DATABASE_URL)
    engine = MockPayPalEscrowEngine()
    broadcaster = LedgerConnectionManager()
    app = create_app(settings=settings, engine=engine, repo=repo, broadcaster=broadcaster)
    client = TestClient(app)
    return {
        "app": app,
        "client": client,
        "repo": repo,
        "engine": engine,
        "broadcaster": broadcaster,
    }


def test_websocket_connection_and_initial_snapshot(test_context):
    """Verifies WebSocket handshake and instantaneous snapshot delivery upon connection."""
    client: TestClient = test_context["client"]

    with client.websocket_connect("/api/v1/ws/ledger") as ws:
        snapshot = ws.receive_json()

        assert snapshot["event_type"] == "INITIAL_SNAPSHOT"
        assert snapshot["agreement_id"] == "ALL"
        assert "timestamp" in snapshot

        data = snapshot["data"]
        assert "agreements" in data
        assert "contracts" in data
        assert "balances" in data
        assert "ledger_entries" in data
        assert "balanced" in data
        assert isinstance(data["agreements"], list)
        assert isinstance(data["balances"], dict)
        assert data["balanced"] is True


def test_websocket_dashboard_alias_routes(test_context):
    """Verifies that /api/v1/ws/dashboard and /ws/dashboard connect cleanly."""
    client: TestClient = test_context["client"]

    with client.websocket_connect("/api/v1/ws/dashboard") as ws1:
        msg1 = ws1.receive_json()
        assert msg1["event_type"] == "INITIAL_SNAPSHOT"

    with client.websocket_connect("/ws/dashboard") as ws2:
        msg2 = ws2.receive_json()
        assert msg2["event_type"] == "INITIAL_SNAPSHOT"


def test_websocket_heartbeat_ping_pong(test_context):
    """Verifies bidirectional ping/pong keepalive prevents proxy connection drops."""
    client: TestClient = test_context["client"]

    with client.websocket_connect("/api/v1/ws/ledger") as ws:
        init = ws.receive_json()
        assert init["event_type"] == "INITIAL_SNAPSHOT"

        # Send raw string ping
        ws.send_text("ping")
        pong1 = ws.receive_json()
        assert pong1["event_type"] == "HEARTBEAT"
        assert pong1["data"]["type"] == "pong"
        assert pong1["data"]["status"] == "alive"

        # Send structured json ping
        ws.send_text('{"type": "ping"}')
        pong2 = ws.receive_json()
        assert pong2["event_type"] == "HEARTBEAT"
        assert pong2["data"]["type"] == "pong"


def test_websocket_broadcasts_on_clean_pass_scenario(test_context):
    """Verifies real-time event streaming across FSM transitions, arbiter evaluation, and ledger updates."""
    client: TestClient = test_context["client"]

    with client.websocket_connect("/api/v1/ws/ledger") as ws:
        init = ws.receive_json()
        assert init["event_type"] == "INITIAL_SNAPSHOT"

        # Trigger clean pass scenario via REST API
        resp = client.post("/api/v1/demo/scenarios/clean_pass")
        assert resp.status_code == 200
        agreement_id = resp.json()["agreement_id"]
        # Fail fast: if the scenario did not settle, the loop below would wait forever for an
        # event that never comes (receive_json has no timeout).
        assert resp.json()["fsm_state"] == "SETTLED_COMPLETE", resp.json().get("arbitration_report")

        # Collect streamed events
        events: List[Dict[str, Any]] = []
        settled = False
        while not settled:
            assert len(events) < 500, "SETTLED_COMPLETE was never broadcast"
            msg = ws.receive_json()
            events.append(msg)
            if (
                msg["event_type"] == "FSM_TRANSITION"
                and msg["data"].get("to_state") == "SETTLED_COMPLETE"
            ):
                settled = True

        event_types = [e["event_type"] for e in events]
        assert "FSM_TRANSITION" in event_types
        assert "ARBITER_EVALUATED" in event_types
        assert "LEDGER_TRANSACTION" in event_types

        # Verify arbiter event details
        arb_events = [e for e in events if e["event_type"] == "ARBITER_EVALUATED"]
        assert len(arb_events) >= 1
        arb = arb_events[0]
        assert arb["agreement_id"] == agreement_id
        assert arb["data"]["decision"] == "FULL_CAPTURE"
        assert arb["data"]["passed_tests"] == 10
        assert arb["data"]["ast_safe"] is True

        # Verify FSM transitions flow
        fsm_states = [
            e["data"]["to_state"] for e in events if e["event_type"] == "FSM_TRANSITION"
        ]
        assert "AGREEMENT_DRAFTED" in fsm_states
        assert "PAYPAL_AUTH_LOCKED" in fsm_states
        assert "SANDBOX_EXECUTING" in fsm_states
        assert "ARBITRATION_AUDITING" in fsm_states
        assert "SETTLED_COMPLETE" in fsm_states


def test_websocket_broadcasts_on_malicious_injection(test_context):
    """Verifies AST security hard veto triggers immediate refund and broadcast over WebSocket."""
    client: TestClient = test_context["client"]

    with client.websocket_connect("/api/v1/ws/ledger") as ws:
        ws.receive_json()  # Consume initial snapshot

        resp = client.post("/api/v1/demo/scenarios/malicious_injection")
        assert resp.status_code == 200
        agreement_id = resp.json()["agreement_id"]

        events: List[Dict[str, Any]] = []
        settled_refund = False
        while not settled_refund:
            msg = ws.receive_json()
            events.append(msg)
            # Full cycle completes when void settlement ledger transaction is posted
            if (
                msg["event_type"] == "LEDGER_TRANSACTION"
                and msg["data"].get("debit_account") == "buyer_vault"
                and msg["data"].get("credit_account") == "paypal_escrow_hold"
            ):
                settled_refund = True

        # Check arbiter event
        arb_events = [e for e in events if e["event_type"] == "ARBITER_EVALUATED"]
        assert len(arb_events) >= 1
        arb = arb_events[0]
        assert arb["data"]["ast_safe"] is False
        assert arb["data"]["decision"] == "VOID_AUTHORIZATION"

        # Check that hold was released in ledger
        ledger_events = [e for e in events if e["event_type"] == "LEDGER_TRANSACTION"]
        assert any(
            le["data"]["debit_account"] == "buyer_vault"
            and le["data"]["credit_account"] == "paypal_escrow_hold"
            for le in ledger_events
        )


def test_master_detail_endpoint_clean_pass(test_context):
    """Verifies GET /api/v1/agreements/{agreement_id}/detail provides all master-detail collections."""
    client: TestClient = test_context["client"]

    resp = client.post("/api/v1/demo/scenarios/clean_pass")
    assert resp.status_code == 200
    agreement_id = resp.json()["agreement_id"]

    # 1. Test primary endpoint
    detail_resp = client.get(f"/api/v1/agreements/{agreement_id}/detail")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()

    # Validate schema adherence
    parsed = AgreementDetailResponse(**detail)
    assert parsed.agreement_id == agreement_id
    assert parsed.fsm_state.value == "SETTLED_COMPLETE"

    # Collection 1: Test Assertions (10 unit tests executed)
    assert len(detail["test_assertions"]) == 10
    for assertion in detail["test_assertions"]:
        assert assertion["status"] == "PASSED"
        assert assertion["name"].startswith("test_")
        assert assertion["duration_ms"] >= 0
        assert assertion["error_message"] is None

    # Collection 2: AST Security Audit
    ast_audit = detail["ast_security_audit"]
    assert ast_audit["is_clean"] is True
    assert ast_audit["score"] == 1.0
    assert "time" in ast_audit["scanned_imports"]
    assert len(ast_audit["blocked_calls"]) == 0

    # Collection 3: Balanced Double-Entry Journal Legs
    journal = detail["ledger_journal"]
    assert len(journal) == 5
    for leg in journal:
        assert leg["debit_account"]
        assert leg["credit_account"]
        assert leg["amount_micros"] > 0
        assert leg["amount_usd"].startswith("$")
        assert "created_at" in leg

    # 2. Test contracts alias endpoint
    alias_resp = client.get(f"/api/v1/contracts/{agreement_id}/detail")
    assert alias_resp.status_code == 200
    assert alias_resp.json()["agreement_id"] == agreement_id


def test_master_detail_endpoint_malicious_injection(test_context):
    """Verifies master-detail endpoint rehydrates AST security violations and failed assertions."""
    client: TestClient = test_context["client"]

    resp = client.post("/api/v1/demo/scenarios/malicious_injection")
    assert resp.status_code == 200
    agreement_id = resp.json()["agreement_id"]

    detail_resp = client.get(f"/api/v1/agreements/{agreement_id}/detail")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()

    # Security check failed
    ast_audit = detail["ast_security_audit"]
    assert ast_audit["is_clean"] is False
    assert ast_audit["score"] == 0.0
    assert any("socket" in call for call in ast_audit["blocked_calls"])

    # Test assertions reflect the security veto
    assert len(detail["test_assertions"]) >= 1
    assert any(a["status"] == "FAILED" for a in detail["test_assertions"])


def test_master_detail_404_for_unknown_agreement(test_context):
    """Verifies 404 response for non-existent agreement ID."""
    client: TestClient = test_context["client"]

    resp = client.get("/api/v1/agreements/agr_nonexistent_000000000/detail")
    assert resp.status_code == 404
    assert "Not found" in resp.json()["detail"]


def test_websocket_multiple_concurrent_clients(test_context):
    """Verifies that multiple subscribers receive broadcast events simultaneously."""
    client: TestClient = test_context["client"]

    with client.websocket_connect("/api/v1/ws/ledger") as ws1:
        with client.websocket_connect("/api/v1/ws/ledger") as ws2:
            snap1 = ws1.receive_json()
            snap2 = ws2.receive_json()
            assert snap1["event_type"] == "INITIAL_SNAPSHOT"
            assert snap2["event_type"] == "INITIAL_SNAPSHOT"

            # Trigger scenario
            client.post("/api/v1/demo/scenarios/clean_pass")

            msg1 = ws1.receive_json()
            msg2 = ws2.receive_json()
            assert msg1["event_type"] == "FSM_TRANSITION"
            assert msg2["event_type"] == "FSM_TRANSITION"
            assert msg1["agreement_id"] == msg2["agreement_id"]
