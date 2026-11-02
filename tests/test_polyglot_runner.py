"""
tests/test_polyglot_runner.py
Test suite for Day 7 Polyglot Execution Sandbox Deliverable:
- Node.js & Vitest-compatible sandbox execution runner.
- Environment variable scrubbing and secret protection.
- Hard timeout enforcement for infinite loops.
- Language routing in the Autonomous Arbitration Engine.
- End-to-end SLA contract evaluation and settlement across Python, TypeScript, and JavaScript.
"""

import os
import pytest
from unittest.mock import MagicMock

from backend.app.schemas import (
    ArbitrationReport,
    FsmState,
    SettlementDecision,
    SlaContractCreate,
)
from backend.app.services.arbitration_engine import (
    ArbitrationArbiter,
    CodeExecutionRunner,
    SubprocessCodeRunner,
    DockerCodeRunner,
    get_execution_runner,
)
from backend.app.services.node_runner import (
    JsSecurityScanner,
    NodeCodeRunner,
    NodeExecutionRunner,
    NodeExecutionTimeoutError,
)
from backend.app.services.orchestrator import EscrowOrchestrator, Orchestrator
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.paypal_mock import MockPayPalEscrowEngine
from backend.app.db import Repository


# ==============================================================================
# 1. NODE.JS RUNNER - BASIC EXECUTION & SCORING
# ==============================================================================

def test_clean_javascript_function_passing_yields_score_one():
    """
    Verifies that a clean JavaScript function passing all assertions in the test suite
    yields a composite test score of 1.0, passed=True, and 0 failed tests.
    """
    worker_code = """
function calculateDiscount(price, discountPercent) {
    if (price < 0 || discountPercent < 0 || discountPercent > 100) {
        throw new Error("Invalid input");
    }
    return price - (price * (discountPercent / 100));
}

module.exports = { calculateDiscount };
"""
    test_code = """
const test = require('node:test');
const assert = require('node:assert');
const { calculateDiscount } = require('./solution');

test('calculates standard 20% discount', () => {
    assert.strictEqual(calculateDiscount(100, 20), 80);
});

test('handles 0% discount', () => {
    assert.strictEqual(calculateDiscount(50, 0), 50);
});

test('handles 100% discount', () => {
    assert.strictEqual(calculateDiscount(75, 100), 0);
});

test('throws on negative price', () => {
    assert.throws(() => calculateDiscount(-10, 10));
});
"""
    runner = NodeCodeRunner(timeout_seconds=5.0)
    result = runner.run(worker_code, test_code)

    assert result["passed"] is True
    assert result["total_tests"] == 4
    assert result["passed_tests"] == 4
    assert result["failed_tests"] == 0
    assert result["score"] == 1.0
    assert result["duration_ms"] > 0
    assert "calculates standard 20% discount" in result["stdout"]


def test_defective_javascript_function_failing_yields_fractional_score():
    """
    Verifies that a defective JavaScript function that fails certain unit tests
    yields a fractional score proportional to passed / total.
    """
    worker_code = """
function formatUser(name, role) {
    // Defective: lowercase name instead of uppercase
    return `${name.toLowerCase()} [${role}]`;
}

module.exports = { formatUser };
"""
    test_code = """
const test = require('node:test');
const assert = require('node:assert');
const { formatUser } = require('./solution');

test('formats admin correctly (failing test)', () => {
    // Expects uppercase, but function returns lowercase
    assert.strictEqual(formatUser('Alice', 'ADMIN'), 'ALICE [ADMIN]');
});

test('includes role brackets (passing test)', () => {
    assert.ok(formatUser('Bob', 'USER').includes('[USER]'));
});
"""
    runner = NodeCodeRunner(timeout_seconds=5.0)
    result = runner.run(worker_code, test_code)

    assert result["passed"] is False
    assert result["total_tests"] == 2
    assert result["passed_tests"] == 1
    assert result["failed_tests"] == 1
    assert result["score"] == 0.5
    assert result["duration_ms"] > 0


# ==============================================================================
# 2. TIMEOUT ENFORCEMENT (SAFE KILL OF INFINITE LOOPS)
# ==============================================================================

def test_infinite_loop_triggers_timeout_exception_safely():
    """
    Verifies that worker code containing an infinite loop (while(true)) is safely killed
    and triggers a timeout exception within the configured threshold (< 5s).
    """
    infinite_loop_code = """
function processData() {
    while (true) {
        // Infinite CPU loop
    }
}

module.exports = { processData };
"""
    test_code = """
const test = require('node:test');
const { processData } = require('./solution');

test('executes long task', () => {
    processData();
});
"""
    runner = NodeCodeRunner(timeout_seconds=1.5)

    # 1. Direct runner with raise_on_timeout=True triggers timeout exception
    with pytest.raises((TimeoutError, NodeExecutionTimeoutError)):
        runner.run(infinite_loop_code, test_code, timeout=1.5, raise_on_timeout=True)

    # 2. Direct runner with raise_on_timeout=False returns structured failure result
    safe_res = runner.run(infinite_loop_code, test_code, timeout=1.5, raise_on_timeout=False)
    assert safe_res["passed"] is False
    assert safe_res["score"] == 0.0
    assert safe_res["failed_tests"] >= 1
    assert "Execution timed out" in safe_res["stderr"]

    # 3. run_tests compatibility method handles timeout without throwing
    passed, total, output = runner.run_tests(infinite_loop_code, test_code, timeout=1.5)
    assert passed == 0
    assert total >= 1
    assert "Execution timed out" in output


# ==============================================================================
# 3. ENVIRONMENT SCRUBBING (ZERO SECRET LEAKAGE)
# ==============================================================================

def test_environment_scrubbing_node_runner():
    """
    Verifies that sensitive environment variables in the parent process (PayPal credentials,
    LLM keys, system tokens) are scrubbed and completely inaccessible inside the Node process.
    """
    secrets_to_inject = {
        "PAYPAL_CLIENT_SECRET": "SK_LIVE_PAYPAL_SECRET_KEY_9999",
        "PAYPAL_CLIENT_ID": "PAYPAL_CLIENT_ID_SECRET_VALUE",
        "GEMINI_API_KEY": "AI_GEMINI_SECRET_KEY_8888",
        "LLM_API_KEY": "LLM_SECRET_TOKEN_7777",
        "OPENAI_API_KEY": "OPENAI_SECRET_TOKEN_6666",
        "ANTHROPIC_API_KEY": "ANTHROPIC_SECRET_TOKEN_5555",
        "AWS_SECRET_ACCESS_KEY": "AWS_SECRET_KEY_4444",
        "STRIPE_API_KEY": "STRIPE_SECRET_KEY_3333",
    }

    for k, v in secrets_to_inject.items():
        os.environ[k] = v

    try:
        worker_code = """
function getEnvKeys() {
    return Object.keys(process.env);
}

module.exports = { getEnvKeys };
"""
        test_code = """
const test = require('node:test');
const assert = require('node:assert');
const { getEnvKeys } = require('./solution');

test('verifies zero PayPal or LLM keys leaked to process.env', () => {
    const forbidden = [
        'PAYPAL_CLIENT_SECRET',
        'PAYPAL_CLIENT_ID',
        'GEMINI_API_KEY',
        'LLM_API_KEY',
        'OPENAI_API_KEY',
        'ANTHROPIC_API_KEY',
        'AWS_SECRET_ACCESS_KEY',
        'STRIPE_API_KEY'
    ];

    for (const key of forbidden) {
        assert.strictEqual(
            process.env[key],
            undefined,
            `SECURITY VIOLATION: Secret ${key} leaked into Node sandbox: ${process.env[key]}`
        );
    }
});
"""
        runner = NodeCodeRunner(timeout_seconds=5.0)
        result = runner.run(worker_code, test_code)

        assert result["passed"] is True
        assert result["score"] == 1.0
        assert result["total_tests"] == 1
        assert result["passed_tests"] == 1
    finally:
        for k in secrets_to_inject:
            os.environ.pop(k, None)


# ==============================================================================
# 4. TYPESCRIPT EXECUTION & VITEST SYNTAX COMPATIBILITY
# ==============================================================================

def test_typescript_clean_function_passing_yields_score_one():
    """
    Verifies that TypeScript worker code with static types, interfaces, and ESM exports
    executes cleanly and passes TypeScript unit tests.
    """
    worker_code = """
interface Transaction {
    id: string;
    amountMicros: number;
    feeMicros: number;
}

export function computeNetPayout(tx: Transaction): number {
    if (tx.amountMicros < tx.feeMicros) {
        throw new Error("Fee exceeds transaction amount");
    }
    return tx.amountMicros - tx.feeMicros;
}
"""
    test_code = """
import test from 'node:test';
import assert from 'node:assert';
import { computeNetPayout } from './solution.ts';

test('computes net payout with TypeScript types', () => {
    const net = computeNetPayout({
        id: "tx_001",
        amountMicros: 15000000,
        feeMicros: 500000
    });
    assert.strictEqual(net, 14500000);
});

test('throws error when fee exceeds amount', () => {
    assert.throws(() => {
        computeNetPayout({
            id: "tx_002",
            amountMicros: 100000,
            feeMicros: 200000
        });
    });
});
"""
    runner = NodeCodeRunner(timeout_seconds=5.0)
    result = runner.run(worker_code, test_code)

    assert result["passed"] is True
    assert result["total_tests"] == 2
    assert result["passed_tests"] == 2
    assert result["score"] == 1.0


def test_vitest_mock_and_expect_matchers():
    """
    Verifies that test suites written with Vitest / Jest style describe, it, expect
    run seamlessly without external npm dependencies.
    """
    worker_code = """
function getStats(values) {
    const sum = values.reduce((a, b) => a + b, 0);
    const avg = values.length ? sum / values.length : 0;
    return {
        count: values.length,
        sum: sum,
        avg: avg,
        values: [...values]
    };
}

module.exports = { getStats };
"""
    test_code = """
const { describe, it, expect } = require('vitest');
const { getStats } = require('./solution');

describe('Stats Service (Vitest style)', () => {
    it('computes count and sum correctly', () => {
        const stats = getStats([10, 20, 30]);
        expect(stats.count).toBe(3);
        expect(stats.sum).toBe(60);
        expect(stats.avg).toBe(20);
    });

    it('validates array containment and length', () => {
        const stats = getStats([5, 15, 25]);
        expect(stats.values).toHaveLength(3);
        expect(stats.values).toContain(15);
        expect(stats.values).not.toContain(99);
    });
});
"""
    runner = NodeCodeRunner(timeout_seconds=5.0)
    result = runner.run(worker_code, test_code)

    assert result["passed"] is True
    assert result["score"] == 1.0
    assert result["total_tests"] == 2
    assert result["passed_tests"] == 2


def test_node_runner_parses_individual_assertions():
    """
    Verifies that NodeCodeRunner._parse_test_assertions extracts individual test
    assertion objects with names, status, and duration for master-detail UI inspection.
    """
    raw_node_output = """
▶ Calculator Suite
  ✔ adds numbers (0.75ms)
  ✖ subtracts numbers (1.20ms)
    AssertionError [ERR_ASSERTION]: Expected 10 === 5
  ✔ multiplies numbers (0.45ms)
✖ Calculator Suite (2.80ms)
ℹ tests 3
ℹ suites 1
ℹ pass 2
ℹ fail 1
ℹ duration_ms 55.40
"""
    assertions = NodeCodeRunner._parse_test_assertions(raw_node_output)

    assert len(assertions) == 3
    names = [a.name for a in assertions]
    assert "adds numbers" in names
    assert "subtracts numbers" in names
    assert "multiplies numbers" in names

    failing = next(a for a in assertions if a.name == "subtracts numbers")
    assert failing.status == "FAILED"
    assert failing.error_message is not None

    passing = next(a for a in assertions if a.name == "adds numbers")
    assert passing.status == "PASSED"


# ==============================================================================
# 5. LANGUAGE ROUTER IN ARBITRATION ENGINE
# ==============================================================================

def test_arbitration_arbiter_polyglot_routing_javascript():
    """
    Verifies that ArbitrationArbiter detects or accepts runtime="javascript" and
    properly routes code to NodeCodeRunner, producing a verified ArbitrationReport.
    """
    worker_code = """
/**
 * Computes escrow fee split in micro-dollars.
 * @param {number} budgetMicros
 * @param {number} feeBps
 * @returns {number}
 */
function computeEscrowFee(budgetMicros, feeBps) {
    return Math.floor((budgetMicros * feeBps) / 10000);
}

module.exports = { computeEscrowFee };
"""
    test_code = """
const test = require('node:test');
const assert = require('node:assert');
const { computeEscrowFee } = require('./solution');

test('computes 5% platform fee on $15 budget', () => {
    assert.strictEqual(computeEscrowFee(15000000, 500), 750000);
});

test('computes zero fee on zero budget', () => {
    assert.strictEqual(computeEscrowFee(0, 500), 0);
});
"""
    arbiter = ArbitrationArbiter()
    report: ArbitrationReport = arbiter.evaluate(
        agreement_id="sla_polyglot_js_001",
        worker_code=worker_code,
        buyer_test_code=test_code,
        budget_micros=15_000_000,
        semantic_score=1.0,
        runtime="javascript",
    )

    assert report.ast_safe is True
    assert report.s_ast == 1.0
    assert report.s_pytest == 1.0
    assert report.composite_score == 1.0
    assert report.passed_tests == 2
    assert report.total_tests == 2
    assert report.settlement.decision == SettlementDecision.FULL_CAPTURE
    assert report.settlement.worker_net_payout_micros > 0


def test_arbitration_arbiter_polyglot_routing_typescript():
    """
    Verifies that ArbitrationArbiter detects or accepts runtime="typescript" and
    properly executes TypeScript deliverables and tests.
    """
    worker_code = """
/**
 * Interface representing currency conversion query.
 */
interface CurrencyQuote {
    from: string;
    to: string;
    rate: number;
}

export function convertAmount(amount: number, quote: CurrencyQuote): number {
    return amount * quote.rate;
}
"""
    test_code = """
import test from 'node:test';
import assert from 'node:assert';
import { convertAmount } from './solution.ts';

test('converts USD to EUR', () => {
    const result = convertAmount(100, { from: 'USD', to: 'EUR', rate: 0.92 });
    assert.strictEqual(result, 92);
});
"""
    arbiter = ArbitrationArbiter()
    report: ArbitrationReport = arbiter.arbitrate(
        agreement_id="sla_polyglot_ts_001",
        worker_code=worker_code,
        buyer_test_code=test_code,
        budget_micros=15_000_000,
        semantic_score=1.0,
        runtime="typescript",
    )

    assert report.ast_safe is True
    assert report.s_ast == 1.0
    assert report.s_pytest == 1.0
    assert report.composite_score == 1.0
    assert report.settlement.decision == SettlementDecision.FULL_CAPTURE


def test_arbitration_arbiter_auto_detects_runtime():
    """
    Verifies that ArbitrationArbiter auto-detects JavaScript and TypeScript runtime
    when runtime parameter is omitted or set to None.
    """
    js_code = "function greet(name) { return 'Hello ' + name; }\nmodule.exports = { greet };"
    js_test = "const test = require('node:test'); const { greet } = require('./solution'); test('g', () => {});"

    ts_code = "export function add(a: number, b: number): number { return a + b; }"
    ts_test = "import test from 'node:test'; import { add } from './solution.ts';"

    py_code = "def add(a: int, b: int) -> int:\n    return a + b"
    py_test = "def test_add():\n    assert True"

    assert ArbitrationArbiter.detect_runtime(js_code, js_test) == "javascript"
    assert ArbitrationArbiter.detect_runtime(ts_code, ts_test) == "typescript"
    assert ArbitrationArbiter.detect_runtime(py_code, py_test) == "python"


def test_arbitration_arbiter_js_malicious_security_hard_veto():
    """
    Verifies that untrusted JavaScript attempting forbidden system imports
    (e.g., child_process, fs, eval) is immediately caught by the AST security perimeter,
    triggering the Hard Veto (S = 0.00) without executing on host.
    """
    malicious_js = """
const cp = require('child_process');
cp.execSync('whoami');

function stealSecret() {
    return process.env.PAYPAL_CLIENT_SECRET;
}

module.exports = { stealSecret };
"""
    test_code = """
const test = require('node:test');
test('test', () => {});
"""
    arbiter = ArbitrationArbiter()
    report: ArbitrationReport = arbiter.evaluate(
        agreement_id="sla_polyglot_malicious_001",
        worker_code=malicious_js,
        buyer_test_code=test_code,
        budget_micros=15_000_000,
        runtime="javascript",
    )

    assert report.ast_safe is False
    assert report.s_ast == 0.0
    assert report.composite_score == 0.0
    assert "Security Perimeter Violation Detected" in report.stdout
    assert any("child_process" in v for v in report.ast_violations)
    assert report.settlement.decision == SettlementDecision.VOID_AUTHORIZATION
    assert report.settlement.worker_net_payout_micros == 0


def test_get_execution_runner_routing():
    """
    Verifies get_execution_runner routes to SubprocessCodeRunner / DockerCodeRunner
    for Python and NodeCodeRunner for JS / TS.
    """
    mock_settings = MagicMock()
    mock_settings.SANDBOX_MODE = "subprocess"

    py_runner = get_execution_runner(mock_settings, runtime="python")
    assert isinstance(py_runner, CodeExecutionRunner)

    js_runner = get_execution_runner(mock_settings, runtime="javascript")
    assert isinstance(js_runner, NodeCodeRunner)

    ts_runner = get_execution_runner(mock_settings, runtime="typescript")
    assert isinstance(ts_runner, NodeCodeRunner)


def test_semantic_rubric_polyglot():
    """
    Verifies compute_semantic_rubric computes quality scores for JavaScript and
    TypeScript deliverables based on JSDoc comments and typing annotations.
    """
    clean_documented_ts = """
/**
 * Computes linear regression slope.
 * @param {number[]} x
 * @param {number[]} y
 * @returns {number}
 */
export function linearSlope(x: number[], y: number[]): number {
    return 1.0;
}
"""
    score = ArbitrationArbiter.compute_semantic_rubric(clean_documented_ts, runtime="typescript")
    assert score >= 0.80

    undocumented_js = "function f(x) { return x; }"
    low_score = ArbitrationArbiter.compute_semantic_rubric(undocumented_js, runtime="javascript")
    assert low_score <= 0.60


# ==============================================================================
# 6. END-TO-END ORCHESTRATOR SUBMISSION (POLYGLOT)
# ==============================================================================

@pytest.mark.asyncio
async def test_orchestrator_submits_polyglot_javascript_work():
    """
    Verifies end-to-end SLA workflow:
    1. Contract creation (AGREEMENT_DRAFTED -> PAYPAL_AUTH_LOCKED).
    2. Submitting JavaScript source code deliverable with runtime="javascript".
    3. FSM advances through SANDBOX_EXECUTING -> ARBITRATION_AUDITING -> SETTLED_COMPLETE.
    4. Financial settlement performs PayPal capture and worker payout.
    """
    repo = Repository.from_url("sqlite:///:memory:")
    ledger = DoubleEntryLedgerEngine()
    paypal = MockPayPalEscrowEngine()
    settings = MagicMock()
    settings.PROTOCOL_FEE_BPS = 500
    settings.PAYPAL_PAYOUT_FEE_MICROS = 250_000
    settings.SANDBOX_MODE = "subprocess"

    orch = Orchestrator(
        repo=repo,
        ledger=ledger,
        paypal=paypal,
        settings=settings,
    )

    contract = await orch.create_contract(
        SlaContractCreate(
            buyer_agent_id="buyer_polyglot_001",
            worker_agent_id="worker_polyglot_001",
            worker_paypal_email="worker-polyglot@example.com",
            budget_micros=15_000_000,
            task_description="Build currency quote module in JavaScript",
        )
    )

    # 1. Authorize hold is locked upon contract creation
    assert contract.fsm_state == FsmState.PAYPAL_AUTH_LOCKED
    assert orch.get(contract.agreement_id).fsm_state == FsmState.PAYPAL_AUTH_LOCKED

    # 2. Worker submits clean JavaScript deliverable
    worker_code = """
/**
 * Formats currency amount as USD string.
 */
function formatUsd(cents) {
    return '$' + (cents / 100).toFixed(2);
}

module.exports = { formatUsd };
"""
    test_code = """
const test = require('node:test');
const assert = require('node:assert');
const { formatUsd } = require('./solution');

test('formats 1500 cents as $15.00', () => {
    assert.strictEqual(formatUsd(1500), '$15.00');
});

test('formats 0 cents as $0.00', () => {
    assert.strictEqual(formatUsd(0), '$0.00');
});
"""

    settled_contract = await orch.submit_work(
        agreement_id=contract.agreement_id,
        source_code=worker_code,
        test_suite=test_code,
        semantic_score=1.0,
        runtime="javascript",
    )

    # 3. Verify capture and payout dispatch
    assert settled_contract.fsm_state == FsmState.PAYOUT_DISPATCHED
    assert settled_contract.verification_score == 1.0
    report = settled_contract.report
    assert report is not None
    assert report["s_pytest"] == 1.0
    assert report["ast_safe"] is True
    assert report["settlement"]["decision"] == "FULL_CAPTURE"
    assert settled_contract.paypal_capture_id is not None
    assert settled_contract.paypal_payout_batch_id is not None

    # 4. Webhook arrives from PayPal settling the contract
    webhook_res = await orch.handle_webhook(
        orch.build_payout_event(settled_contract.paypal_payout_batch_id, "succeeded")
    )
    assert webhook_res.get("status") in ("processed", "applied")
    final_contract = orch.get(contract.agreement_id)
    assert final_contract.fsm_state == FsmState.SETTLED_COMPLETE


def test_api_submit_polyglot_javascript(tmp_path):
    """
    Verifies HTTP API endpoint /contracts/{agreement_id}/submit with runtime="javascript".
    """
    from fastapi.testclient import TestClient
    from backend.app.config import Settings
    from backend.app.main import create_app

    db_path = str(tmp_path / "polyglot_api.db").replace("\\", "/")
    cfg = Settings(
        DATABASE_URL=f"sqlite:///{db_path}",
        PAYPAL_MODE="mock",
        SANDBOX_MODE="subprocess",
    )
    app = create_app(cfg, engine=MockPayPalEscrowEngine())
    with TestClient(app) as client:
        # 1. Propose SLA contract
        res = client.post("/api/v1/contracts", json={
            "buyer_agent_id": "buyer_http_js",
            "worker_agent_id": "worker_http_js",
            "worker_paypal_email": "worker-js@example.com",
            "budget_micros": 12_000_000,
            "task_description": "Build JS module",
        })
        assert res.status_code == 201
        data = res.json()
        agreement_id = data["agreement_id"]

        # 2. Submit JavaScript deliverable via API
        sub_res = client.post(f"/api/v1/contracts/{agreement_id}/submit", json={
            "source_code": "/**\n * Adds two numbers\n */\nfunction sum(a, b) { return a + b; }\nmodule.exports = { sum };",
            "test_suite": "const test = require('node:test'); const assert = require('node:assert'); const { sum } = require('./solution'); test('sum', () => { assert.strictEqual(sum(10, 5), 15); });",
            "runtime": "javascript",
        })
        assert sub_res.status_code == 200
        sub_data = sub_res.json()
        assert sub_data["fsm_state"] == "PAYOUT_DISPATCHED"
        assert sub_data["verification_score"] == 1.0


def test_mcp_tool_submit_polyglot_javascript(tmp_path):
    """
    Verifies MCP tool aegispay_submit_deliverable with runtime="javascript".
    """
    from fastapi.testclient import TestClient
    from backend.app.config import Settings
    from backend.app.main import create_app

    db_path = str(tmp_path / "polyglot_mcp.db").replace("\\", "/")
    cfg = Settings(
        DATABASE_URL=f"sqlite:///{db_path}",
        PAYPAL_MODE="mock",
        SANDBOX_MODE="subprocess",
    )
    app = create_app(cfg, engine=MockPayPalEscrowEngine())
    with TestClient(app) as client:
        # 1. Create contract via MCP
        prop_res = client.post("/api/v1/mcp/execute", json={
            "name": "aegispay_propose_agreement",
            "arguments": {
                "buyer_agent_id": "buyer_mcp_js",
                "worker_agent_id": "worker_mcp_js",
                "worker_paypal_email": "worker-mcp@example.com",
                "budget_micros": 8_000_000,
                "task_description": "Build JS validator",
            }
        })
        assert prop_res.status_code == 200
        agr_id = prop_res.json()["result"]["agreement_id"]

        # 2. Submit work via MCP
        sub_res = client.post("/api/v1/mcp/execute", json={
            "name": "aegispay_submit_deliverable",
            "arguments": {
                "agreement_id": agr_id,
                "source_code": "/**\n * Checks if number is positive\n */\nfunction check(x) { return x > 0; }\nmodule.exports = { check };",
                "test_suite": "const test = require('node:test'); const assert = require('node:assert'); const { check } = require('./solution'); test('check', () => { assert.ok(check(5)); });",
                "runtime": "javascript",
            }
        })
        assert sub_res.status_code == 200
        contract = sub_res.json()["result"]
        assert contract["fsm_state"] == "PAYOUT_DISPATCHED"
        assert contract["verification_score"] == 1.0

