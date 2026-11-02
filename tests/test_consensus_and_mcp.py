"""
tests/test_consensus_and_mcp.py
Comprehensive test suite verifying:
1. GeminiJudge parsing, JSON code fence handling, clamping, and heuristic fallback.
2. DualConsensusArbiter convergence averaging (|S_A - S_B| <= 0.15).
3. DualConsensusArbiter divergence conservative penalty (|S_A - S_B| > 0.15 => min(S_A, S_B) * 0.90).
4. ArbitrationEngine.arbitrate() composite score: S = S_AST * (0.60 * S_test + 0.40 * S_semantic).
5. AST Security Hard Veto forcing S = 0.00 and bypassing LLM evaluation.
6. MCP Agent discovery manifest (/api/v1/mcp/manifest) and tool execution.
"""

from types import SimpleNamespace
from unittest import mock
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import Repository
from backend.app.main import create_app
from backend.app.services.arbitration_engine import (
    ArbitrationArbiter,
    ArbitrationEngine,
    AstSecurityScanner,
    CodeExecutionRunner,
)
from backend.app.services.llm_judge import (
    ClaudeJudge,
    DualConsensusArbiter,
    GeminiJudge,
    HeuristicJudge,
    JudgeResult,
)
from backend.app.services.paypal_mock import MockPayPalEscrowEngine
from backend.app.services.scenario_presets import (
    BUYER_RATE_LIMITER_TEST_SUITE,
    CLEAN_PASS_WORKER_CODE,
    MALICIOUS_INJECTION_WORKER_CODE,
)

SAMPLE_CODE = """
def calculate_payout(amount: float, fee_bps: int = 500) -> float:
    \"\"\"Calculates net payout after basis-points deduction.\"\"\"
    return amount * (1.0 - (fee_bps / 10000.0))
"""


def _mock_gemini_client(text: str = "", exc: Exception = None):
    client = mock.Mock()
    if exc:
        client.generate_content.side_effect = exc
    else:
        client.generate_content.return_value = SimpleNamespace(text=text)
    return client


# ==============================================================================
# 1. GEMINI JUDGE TESTS
# ==============================================================================

def test_gemini_judge_parsing_valid_json():
    """Verifies that GeminiJudge parses standard JSON output with score and rationale."""
    client = _mock_gemini_client('{"score": 0.88, "rationale": "High quality rate limiter."}')
    judge = GeminiJudge(client=client)
    res = judge.judge(SAMPLE_CODE)

    assert res.score == 0.88
    assert res.rationale == "High quality rate limiter."


def test_gemini_judge_handles_code_fences():
    """Verifies that markdown json code fences are stripped cleanly."""
    fenced_text = '```json\n{"score": 0.95, "rationale": "Clean code with full types."}\n```'
    client = _mock_gemini_client(fenced_text)
    judge = GeminiJudge(client=client)
    res = judge.judge(SAMPLE_CODE)

    assert res.score == 0.95
    assert "Clean code" in res.rationale


@pytest.mark.parametrize("raw_score,expected", [(1.8, 1.0), (-0.5, 0.0), (0.75, 0.75)])
def test_gemini_judge_clamping(raw_score, expected):
    """Verifies that scores outside [0.0, 1.0] are strictly clamped."""
    client = _mock_gemini_client(f'{{"score": {raw_score}, "rationale": "Test clamp"}}')
    judge = GeminiJudge(client=client)
    res = judge.judge(SAMPLE_CODE)

    assert res.score == expected


@pytest.mark.parametrize(
    "bad_text",
    [
        "not json at all",
        '{"score": "A+"}',
        '{"rationale": "no score"}',
        '{"score": true}',
        "[1, 2, 3]",
        "",
    ],
)
def test_gemini_judge_malformed_falls_back_to_heuristic(bad_text):
    """Verifies that unparseable or malformed LLM responses degrade gracefully to heuristic judge."""
    client = _mock_gemini_client(bad_text)
    judge = GeminiJudge(client=client)
    res = judge.judge(SAMPLE_CODE)

    heuristic_expected = ArbitrationArbiter.compute_semantic_rubric(SAMPLE_CODE)
    assert res.score == heuristic_expected
    assert res.rationale is None


def test_gemini_judge_network_exception_falls_back():
    """Verifies that network errors or API timeouts fall back gracefully to heuristics."""
    client = _mock_gemini_client(exc=TimeoutError("Gemini API call timed out"))
    judge = GeminiJudge(client=client)
    res = judge.judge(SAMPLE_CODE)

    heuristic_expected = ArbitrationArbiter.compute_semantic_rubric(SAMPLE_CODE)
    assert res.score == heuristic_expected


# ==============================================================================
# 2. DUAL-MODEL CONSENSUS ARBITER TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_dual_consensus_convergence_agreement():
    """
    Verifies that when |S_Gemini - S_Claude| <= 0.15:
    Convergence rule applies: S_semantic = (S_Gemini + S_Claude) / 2
    No divergence penalty is applied.
    """
    class MockJudge:
        def __init__(self, score, rationale):
            self.score = score
            self.rationale = rationale

        def judge(self, _):
            return JudgeResult(self.score, self.rationale)

    gemini = MockJudge(0.85, "Gemini approves.")
    claude = MockJudge(0.90, "Claude approves.")

    arbiter = DualConsensusArbiter(gemini_judge=gemini, claude_judge=claude)
    audit = await arbiter.evaluate_consensus(SAMPLE_CODE)

    assert audit["divergence_penalty_applied"] is False
    assert audit["divergence"] == pytest.approx(0.05)
    assert audit["consensus_score"] == pytest.approx(0.875)
    assert audit["gemini_score"] == 0.85
    assert audit["claude_score"] == 0.90


@pytest.mark.asyncio
async def test_dual_consensus_divergence_penalty():
    """
    Verifies that when |S_Gemini - S_Claude| > 0.15:
    Divergence penalty applies: S_semantic = min(S_Gemini, S_Claude) * 0.90
    Conservative bias protects buyer collateral.
    """
    class MockJudge:
        def __init__(self, score, rationale):
            self.score = score
            self.rationale = rationale

        def judge(self, _):
            return JudgeResult(self.score, self.rationale)

    # Gemini gives 0.95, Claude gives 0.60 (divergence = 0.35 > 0.15)
    gemini = MockJudge(0.95, "Gemini is very impressed.")
    claude = MockJudge(0.60, "Claude found missing tests.")

    arbiter = DualConsensusArbiter(gemini_judge=gemini, claude_judge=claude)
    audit = await arbiter.evaluate_consensus(SAMPLE_CODE)

    assert audit["divergence_penalty_applied"] is True
    assert audit["divergence"] == pytest.approx(0.35)
    # Expected: min(0.95, 0.60) * 0.90 = 0.60 * 0.90 = 0.54
    assert audit["consensus_score"] == pytest.approx(0.54)


@pytest.mark.asyncio
async def test_dual_consensus_divergence_reversed():
    """Verifies divergence rule when Claude is higher than Gemini."""
    class MockJudge:
        def __init__(self, score):
            self.score = score

        def judge(self, _):
            return JudgeResult(self.score, "ok")

    gemini = MockJudge(0.50)
    claude = MockJudge(0.80)

    arbiter = DualConsensusArbiter(gemini_judge=gemini, claude_judge=claude)
    audit = await arbiter.evaluate_consensus(SAMPLE_CODE)

    assert audit["divergence_penalty_applied"] is True
    # Expected: min(0.50, 0.80) * 0.90 = 0.50 * 0.90 = 0.45
    assert audit["consensus_score"] == pytest.approx(0.45)


def test_dual_consensus_sync_judge_interface():
    """Verifies that DualConsensusArbiter.judge() can be called synchronously."""
    class MockJudge:
        def __init__(self, score):
            self.score = score

        def judge(self, _):
            return JudgeResult(self.score, "eval")

    arbiter = DualConsensusArbiter(gemini_judge=MockJudge(0.80), claude_judge=MockJudge(0.80))
    res = arbiter.judge(SAMPLE_CODE)

    assert isinstance(res, JudgeResult)
    assert res.score == 0.80
    assert "Consensus S=0.8000" in res.rationale


# ==============================================================================
# 3. ARBITRATION ENGINE ARBITRATE() & COMPOSITE FORMULA
# ==============================================================================

def test_arbitration_engine_composite_formula_clean():
    """
    Verifies ArbitrationEngine.arbitrate() uses Day 5/6 formula:
    S = S_AST * (0.60 * S_test + 0.40 * S_semantic)
    For clean pass: S_AST = 1.0, S_test = 1.0, S_semantic = 0.85
    Expected S = 1.0 * (0.60 * 1.0 + 0.40 * 0.85) = 0.60 + 0.34 = 0.94
    """
    engine = ArbitrationEngine()
    report = engine.arbitrate(
        agreement_id="agr_test_consensus_1",
        worker_code=CLEAN_PASS_WORKER_CODE,
        buyer_test_code=BUYER_RATE_LIMITER_TEST_SUITE,
        semantic_score=0.85,
    )

    assert report.s_ast == 1.0
    assert report.s_pytest == 1.0
    assert report.s_semantic == 0.85
    assert report.composite_score == 0.94
    assert report.settlement.decision == "FULL_CAPTURE"
    assert report.settlement.capture_micros == 15_000_000


def test_arbitration_engine_hard_veto_rule_overrides_consensus():
    """
    CRITICAL INVARIANT: If S_AST == 0.0, the Hard Veto locks composite score S to 0.00 immediately,
    regardless of how high the consensus score or test suite pass ratio would be.
    """
    class PerfectJudge:
        def judge(self, _):
            return JudgeResult(1.0, "Flawless semantic score")

    arbiter = ArbitrationEngine(judge=PerfectJudge())

    # Malicious injection deliverable has banned socket/subprocess imports
    report = arbiter.arbitrate(
        agreement_id="agr_test_hard_veto",
        worker_code=MALICIOUS_INJECTION_WORKER_CODE,
        buyer_test_code=BUYER_RATE_LIMITER_TEST_SUITE,
        semantic_score=1.0,  # Even if external semantic score claims 1.0
    )

    # Hard veto verified
    assert report.s_ast == 0.0
    assert report.composite_score == 0.0
    assert report.settlement.decision == "VOID_AUTHORIZATION"
    assert report.settlement.capture_micros == 0
    assert report.settlement.buyer_released_micros == 15_000_000
    assert report.ast_safe is False
    assert len(report.ast_violations) >= 2


# ==============================================================================
# 4. MODEL CONTEXT PROTOCOL (MCP) INTERFACE TESTS
# ==============================================================================

@pytest.fixture
def mcp_client():
    settings = Settings(
        DATABASE_URL="sqlite:///:memory:",
        PAYPAL_MODE="mock",
        SANDBOX_MODE="subprocess",
    )
    repo = Repository.from_url(settings.DATABASE_URL)
    engine = MockPayPalEscrowEngine()
    app = create_app(settings=settings, engine=engine, repo=repo)
    return TestClient(app)


def test_mcp_manifest_endpoint_and_tools(mcp_client):
    """
    Verifies GET /api/v1/mcp/manifest returns the valid MCP tool definitions:
    - aegispay_propose_agreement
    - aegispay_submit_deliverable
    - aegispay_check_escrow_status
    """
    resp = mcp_client.get("/api/v1/mcp/manifest")
    assert resp.status_code == 200
    manifest = resp.json()

    assert manifest["name"] == "aegispay"
    assert manifest["version"] == "2.0.0"
    assert "tools" in manifest
    assert len(manifest["tools"]) == 3

    tool_names = [t["name"] for t in manifest["tools"]]
    assert "aegispay_propose_agreement" in tool_names
    assert "aegispay_submit_deliverable" in tool_names
    assert "aegispay_check_escrow_status" in tool_names

    # Check root alias /mcp/manifest
    root_resp = mcp_client.get("/mcp/manifest")
    assert root_resp.status_code == 200
    assert root_resp.json() == manifest

    # Validate JSON schema structure for each tool
    for tool in manifest["tools"]:
        assert "description" in tool
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema
        assert isinstance(schema["required"], list)


def test_mcp_tool_execution_flow(mcp_client):
    """Verifies autonomous agents can execute the MCP tools via API."""
    # 1. Propose Agreement
    propose_resp = mcp_client.post(
        "/api/v1/mcp/execute",
        json={
            "name": "aegispay_propose_agreement",
            "arguments": {
                "buyer_agent_id": "buyer-mcp-1",
                "worker_agent_id": "worker-mcp-1",
                "worker_paypal_email": "worker-agent@personal.example.com",
                "budget_micros": 15000000,
                "task_description": "Implement TokenBucketLimiter for SLA rate limit",
            },
        },
    )
    assert propose_resp.status_code == 200
    agreement_id = propose_resp.json()["result"]["agreement_id"]
    assert agreement_id.startswith("agr_")

    # 2. Check Escrow Status
    check_resp = mcp_client.post(
        "/api/v1/mcp/execute",
        json={
            "name": "aegispay_check_escrow_status",
            "arguments": {"agreement_id": agreement_id},
        },
    )
    assert check_resp.status_code == 200
    result = check_resp.json()["result"]
    assert result["agreement_id"] == agreement_id
    assert result["fsm_state"] == "PAYPAL_AUTH_LOCKED"
    assert "ledger_journal" in result


def test_mcp_tool_execution_unknown_tool(mcp_client):
    """Verifies 404 for unknown tool calls."""
    resp = mcp_client.post(
        "/api/v1/mcp/execute",
        json={"name": "unknown_tool", "arguments": {}},
    )
    assert resp.status_code == 404
