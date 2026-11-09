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


# ------------------------------------------------------------------------- PAYPAL_MODE validation
def test_misspelled_paypal_mode_is_rejected_instead_of_going_live():
    with pytest.raises(ValueError, match="PAYPAL_MODE"):
        settings(PAYPAL_MODE="sandbx")


