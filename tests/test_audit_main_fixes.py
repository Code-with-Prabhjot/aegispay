"""Regression tests for the defects found in the audit of main at c1964a9."""

import subprocess
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.db import Repository
from backend.app.main import create_app
from backend.app.schemas import FsmState
from backend.app.services import arbitration_engine as ae
from backend.app.services import fsm
from backend.app.services.arbitration_engine import ArbitrationArbiter, CodeExecutionRunner
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.paypal_mock import MockPayPalEscrowEngine
from backend.app.services.vault_service import (
    register_vault,
    release_vault_funds,
    reserve_vault_funds,
)
from tests.test_settlement_recovery import FlakyEngine
from tests.test_trust_policy import P, SUITE, api_body, make_orch, payload, settings

KEY = "test-api-key"
H = {"X-API-Key": KEY}


def app_client(tmp_path, **over):
    over.setdefault("DATABASE_URL", f"sqlite:///{tmp_path}/audit.db")
    app = create_app(settings(**over), engine=MockPayPalEscrowEngine())
    return TestClient(app), app


# ----------------------------------------------------------------- MCP JSON-RPC requires the API key
def test_root_mcp_rpc_is_gone_and_api_copy_needs_the_key(tmp_path):
    client, _ = app_client(tmp_path, PAYPAL_MODE="sandbox", API_AUTH_KEY=KEY)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "propose_contract", "arguments": api_body()}}
    with client:
        assert client.post("/mcp/rpc", json=call).status_code in (404, 405)
        assert client.post(f"{P}/mcp/rpc", json=call).status_code == 401
        listed = client.post(f"{P}/mcp/rpc", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                             headers=H)
        assert listed.status_code == 200
        assert client.get(f"{P}/contracts", headers=H).json()["count"] == 0


# ---------------------------------------------------------------------- ledger balance is a real audit
def test_ledger_audit_detects_a_tampered_balance():
    ledger = DoubleEntryLedgerEngine()
    ledger.deposit("buyer_vault", 10_000_000, "a1")
    ledger.record_transfer("paypal_escrow_hold", "buyer_vault", 10_000_000, "a1")
    assert ledger.verify_balance_conservation()
    ledger._balances["worker_payable"] += 1  # money appears from nowhere
    audit = ledger.audit_balances()
    assert audit["balanced"] is False
    assert audit["net_imbalance_micros"] == 1
    assert audit["account_drift_micros"] == {"worker_payable": 1}


def test_ledger_audit_survives_a_reload(tmp_path):
    repo = Repository.from_url(f"sqlite:///{tmp_path}/l.db")
    ledger = DoubleEntryLedgerEngine.from_repository(repo)
    ledger.deposit("buyer_vault", 5_000_000, "a1")
    ledger.record_transfer("paypal_escrow_hold", "buyer_vault", 5_000_000, "a1")
    reloaded = DoubleEntryLedgerEngine.from_repository(repo)
    assert reloaded.audit_balances()["balanced"] is True
    assert reloaded.audit_balances()["total_deposited_micros"] == 5_000_000


def test_ledger_balance_endpoint_reports_drift(tmp_path):
    client, app = app_client(tmp_path)
    with client:
        client.post(f"{P}/demo/scenarios/clean_pass")
        ok = client.get(f"{P}/ledger/balance").json()
        assert ok["balanced"] is True and ok["net_imbalance"] == 0
        app.state.ledger._balances["platform_fee_revenue"] += 7
        bad = client.get(f"{P}/ledger/balance").json()
        assert bad["balanced"] is False and bad["net_imbalance"] == 7


# ------------------------------------------------------------------ a2a auto_sign creates a contract
def test_auto_sign_creates_a_locked_contract(tmp_path):
    client, app = app_client(tmp_path)
    body = {"buyer_pref": {"max_budget_micros": 15_000_000, "max_timeout_seconds": 8.0},
            "worker_pref": {"min_budget_micros": 5_000_000, "min_timeout_seconds": 2.0},
            "auto_sign": True}
    with client:
        res = client.post(f"{P}/a2a/negotiate", json=body)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["contract_created"] is True and data["auth_id"]
        stored = client.get(f"{P}/contracts/{data['agreement_id']}").json()
        assert stored["fsm_state"] == "PAYPAL_AUTH_LOCKED"
        assert stored["buyer_signature"] and stored["worker_signature"]
        assert stored["buyer_agent_id"] == data["buyer_did"].strip()


def test_auto_sign_is_refused_outside_mock_mode(tmp_path):
    client, _ = app_client(tmp_path, PAYPAL_MODE="sandbox", API_AUTH_KEY=KEY)
    body = {"buyer_pref": {"max_budget_micros": 15_000_000}, "worker_pref": {}, "auto_sign": True}
    with client:
        assert client.post(f"{P}/a2a/negotiate", json=body, headers=H).status_code == 403


# ------------------------------------------------------------------------- PAYPAL_MODE validation
def test_misspelled_paypal_mode_is_rejected_instead_of_going_live():
    with pytest.raises(ValueError, match="PAYPAL_MODE"):
        settings(PAYPAL_MODE="sandbx")


# --------------------------------------------------------------------------- configurable CORS
def test_cors_origins_are_configurable(tmp_path):
    client, _ = app_client(tmp_path, CORS_ALLOWED_ORIGINS="https://dash.example.com, http://localhost:3000/")
    with client:
        r = client.get(f"{P}/health", headers={"Origin": "https://dash.example.com"})
        assert r.headers["access-control-allow-origin"] == "https://dash.example.com"
        r = client.get(f"{P}/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in r.headers


# ------------------------------------------------------------------------- health checks the DB
def test_health_reports_a_dead_database(tmp_path):
    client, app = app_client(tmp_path)
    with client:
        with mock.patch.object(app.state.repo.db, "query", side_effect=RuntimeError("gone")):
            body = client.get(f"{P}/health").json()
    assert body["database"] == "unavailable" and body["status"] == "degraded"


