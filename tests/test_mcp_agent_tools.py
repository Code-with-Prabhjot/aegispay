"""
tests/test_mcp_agent_tools.py
Integration test suite for Model Context Protocol (MCP) agent tools and autonomous commerce cycle.

Verifies:
1. test_mcp_manifest_schema: Asserts JSON-RPC tool definitions for propose_contract,
   submit_deliverable, and verify_audit_proof.
2. test_mcp_autonomous_execution_cycle: Executes a full SLA agreement flow via MCP
   schema parameters (Propose Contract -> Auth Hold -> Submit Code -> Dual Arbitration -> Settlement -> Merkle Proof).
3. test_mcp_jsonrpc_protocol_envelope: Tests JSON-RPC 2.0 protocol format tools/list and tools/call.
4. test_mcp_malicious_code_hard_veto: Tests AST security barrier rejection over MCP tool interface.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import Repository
from backend.app.main import create_app
from backend.app.services.mcp_agent_tools import MCP_JSONRPC_TOOLS
from backend.app.services.paypal_mock import MockPayPalEscrowEngine
from backend.app.services.scenario_presets import (
    BUYER_RATE_LIMITER_TEST_SUITE,
    CLEAN_PASS_WORKER_CODE,
    MALICIOUS_INJECTION_WORKER_CODE,
)


@pytest.fixture
def mcp_test_client():
    settings = Settings(
        DATABASE_URL="sqlite:///:memory:",
        PAYPAL_MODE="mock",
        SANDBOX_MODE="subprocess",
    )
    repo = Repository.from_url(settings.DATABASE_URL)
    engine = MockPayPalEscrowEngine()
    app = create_app(settings=settings, engine=engine, repo=repo)
    return TestClient(app)


def test_mcp_manifest_schema(mcp_test_client):
    """
    Asserts JSON-RPC tool definitions for:
    - propose_contract
    - submit_deliverable
    - verify_audit_proof
    Validates parameter schema typing, descriptions, and required constraints.
    """
    # 1. Test schema via GET /api/v1/mcp/tools
    res = mcp_test_client.get("/api/v1/mcp/tools")
    assert res.status_code == 200
    tools = res.json()["tools"]
    tool_map = {t["name"]: t for t in tools}

    assert "propose_contract" in tool_map
    assert "submit_deliverable" in tool_map
    assert "verify_audit_proof" in tool_map

    # 2. Check propose_contract schema
    pc = tool_map["propose_contract"]
    assert "description" in pc
    pc_props = pc["inputSchema"]["properties"]
    assert "buyer_agent_id" in pc_props
    assert "worker_agent_id" in pc_props
    assert "worker_paypal_email" in pc_props
    assert "budget_micros" in pc_props
    assert "task_description" in pc_props
    for req_field in ["buyer_agent_id", "worker_agent_id", "worker_paypal_email", "budget_micros", "task_description"]:
        assert req_field in pc["inputSchema"]["required"]

    # 3. Check submit_deliverable schema
    sd = tool_map["submit_deliverable"]
    assert "description" in sd
    sd_props = sd["inputSchema"]["properties"]
    assert "agreement_id" in sd_props
    assert "source_code" in sd_props
    assert "agreement_id" in sd["inputSchema"]["required"]
    assert "source_code" in sd["inputSchema"]["required"]

    # 4. Check verify_audit_proof schema
    vap = tool_map["verify_audit_proof"]
    assert "description" in vap
    vap_props = vap["inputSchema"]["properties"]
    assert "agreement_id" in vap_props
    assert "leaf_index" in vap_props
    assert "agreement_id" in vap["inputSchema"]["required"]

    # 5. Verify JSON-RPC 2.0 tools/list method
    rpc_res = mcp_test_client.post(
        "/api/v1/mcp/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert rpc_res.status_code == 200
    rpc_data = rpc_res.json()
    assert rpc_data.get("jsonrpc") == "2.0"
    assert rpc_data.get("id") == 1
    assert "tools" in rpc_data["result"]
    rpc_tool_names = [t["name"] for t in rpc_data["result"]["tools"]]
    assert "propose_contract" in rpc_tool_names
    assert "submit_deliverable" in rpc_tool_names
    assert "verify_audit_proof" in rpc_tool_names


def test_mcp_autonomous_execution_cycle(mcp_test_client):
    """
    Executes a complete autonomous agent cycle over the MCP interface:
    1. Buyer agent proposes contract & locks escrow hold.
    2. Arbiter confirms FSM state is locked.
    3. Worker agent submits verified deliverable code.
    4. Arbitration evaluates tests, AST, and semantic quality -> settlement.
    5. Agent queries 7-leaf Merkle proof to cryptographically verify settlement.
    """
    # Step 1: Autonomous Proposal & Hold
    propose_payload = {
        "name": "propose_contract",
        "arguments": {
            "buyer_agent_id": "did:aegis:buyer:ai-agent-001",
            "worker_agent_id": "did:aegis:worker:coder-agent-002",
            "worker_paypal_email": "worker-agent@personal.example.com",
            "budget_micros": 15_000_000,  # $15.00
            "task_description": "Implement TokenBucketRateLimiter for autonomous agent API gateway",
            "test_suite": BUYER_RATE_LIMITER_TEST_SUITE,
        },
    }
    propose_resp = mcp_test_client.post("/api/v1/mcp/execute", json=propose_payload)
    assert propose_resp.status_code == 200
    agreement = propose_resp.json()["result"]
    agreement_id = agreement["agreement_id"]
    assert agreement_id.startswith("agr_")
    assert agreement["fsm_state"] == "PAYPAL_AUTH_LOCKED"
    assert agreement["budget_micros"] == 15_000_000
    assert agreement["paypal_authorization_id"] is not None

    # Step 2: Inspect Escrow Status via MCP
    status_resp = mcp_test_client.post(
        "/api/v1/mcp/execute",
        json={
            "name": "check_escrow_status",
            "arguments": {"agreement_id": agreement_id},
        },
    )
    assert status_resp.status_code == 200
    status_data = status_resp.json()["result"]
    assert status_data["agreement_id"] == agreement_id
    assert status_data["fsm_state"] == "PAYPAL_AUTH_LOCKED"

    # Step 3: Autonomous Worker Deliverable Submission
    submit_payload = {
        "name": "submit_deliverable",
        "arguments": {
            "agreement_id": agreement_id,
            "source_code": CLEAN_PASS_WORKER_CODE,
        },
    }
    submit_resp = mcp_test_client.post("/api/v1/mcp/execute", json=submit_payload)
    assert submit_resp.status_code == 200
    settled = submit_resp.json()["result"]

    assert settled["fsm_state"] in ("SETTLED", "PAYPAL_CAPTURED", "PAYOUT_DISPATCHED")
    report = settled["arbitration_report"]
    assert report is not None
    assert report["ast_safe"] is True
    assert report["composite_score"] >= 0.90
    assert report["settlement"]["decision"] == "FULL_CAPTURE"
    assert report["settlement"]["capture_micros"] == 15_000_000

    # Step 4: Autonomous Non-Repudiation Merkle Audit Proof Verification
    proof_payload = {
        "name": "verify_audit_proof",
        "arguments": {
            "agreement_id": agreement_id,
            "leaf_index": 0,
        },
    }
    proof_resp = mcp_test_client.post("/api/v1/mcp/execute", json=proof_payload)
    assert proof_resp.status_code == 200
    proof_result = proof_resp.json()["result"]

    assert proof_result["agreement_id"] == agreement_id
    assert proof_result["leaf_index"] == 0
    assert "SLA Contract" in proof_result["leaf_name"]
    assert proof_result["verified"] is True
    assert len(proof_result["merkle_root"]) == 64  # SHA-256 hex string
    assert isinstance(proof_result["proof_hashes"], list)
    assert len(proof_result["proof_hashes"]) > 0


def test_mcp_jsonrpc_protocol_envelope(mcp_test_client):
    """Verifies standard JSON-RPC 2.0 tools/call envelope execution."""
    rpc_call = {
        "jsonrpc": "2.0",
        "id": "req-999",
        "method": "tools/call",
        "params": {
            "name": "propose_contract",
            "arguments": {
                "buyer_agent_id": "buyer-jsonrpc",
                "worker_agent_id": "worker-jsonrpc",
                "worker_paypal_email": "jsonrpc@example.com",
                "budget_micros": 10_000_000,
                "task_description": "JSON-RPC 2.0 Autonomous Proposal",
            },
        },
    }
    resp = mcp_test_client.post("/api/v1/mcp/rpc", json=rpc_call)
    assert resp.status_code == 200
    data = resp.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == "req-999"
    assert "result" in data
    assert data["result"]["agreement_id"].startswith("agr_")


def test_mcp_malicious_code_hard_veto(mcp_test_client):
    """Verifies that an agent submitting malicious injection via MCP gets AST hard vetoed."""
    # Propose
    p_resp = mcp_test_client.post(
        "/api/v1/mcp/execute",
        json={
            "name": "propose_contract",
            "arguments": {
                "buyer_agent_id": "buyer-victim",
                "worker_agent_id": "worker-attacker",
                "worker_paypal_email": "attacker@example.com",
                "budget_micros": 20_000_000,
                "task_description": "Rate limiter",
                "test_suite": BUYER_RATE_LIMITER_TEST_SUITE,
            },
        },
    )
    agr_id = p_resp.json()["result"]["agreement_id"]

    # Submit malicious code
    s_resp = mcp_test_client.post(
        "/api/v1/mcp/execute",
        json={
            "name": "submit_deliverable",
            "arguments": {
                "agreement_id": agr_id,
                "source_code": MALICIOUS_INJECTION_WORKER_CODE,
            },
        },
    )
    result = s_resp.json()["result"]
    report = result["arbitration_report"]
    assert report["ast_safe"] is False
    assert report["composite_score"] == 0.0
    assert report["settlement"]["decision"] == "VOID_AUTHORIZATION"
    assert report["settlement"]["capture_micros"] == 0
