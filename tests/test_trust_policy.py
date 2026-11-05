"""Trust policy: who may choose the tests, the score, and what runs where."""

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import Repository
from backend.app.main import create_app
from backend.app.schemas import ArbitrationReport, FsmState, SlaContractCreate
from backend.app.services.arbitration_engine import (
    ArbitrationArbiter,
    CodeExecutionRunner,
    DockerExecutionRunner,
)
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.node_runner import JsSecurityScanner, NodeCodeRunner
from backend.app.services.orchestrator import (
    EscrowOrchestrator,
    PolicyViolationError,
    SandboxUnavailableError,
    sha256_hex,
)
from backend.app.services.paypal_escrow import compute_settlement_split
from backend.app.services.paypal_mock import MockPayPalEscrowEngine

P = "/api/v1"
SUITE = "def test_real_requirement():\n    assert True\n"
ATTACKER_SUITE = "def test_always_passes():\n    assert True  # attacker-chosen\n"


class RecordingArbiter:
    """Stands in for the real arbiter: records what it was asked to grade, runs nothing."""

    def __init__(self, score: float = 1.0, ast_safe: bool = True):
        self.score = score
        self.ast_safe = ast_safe
        self.calls = []

    def is_isolated(self, *args, **kwargs) -> bool:
        return True

    def scan_test_suite(self, test_code, runtime=None):
        return True, []

    def evaluate(self, agreement_id, worker_code, buyer_test_code, budget_micros=15_000_000,
                 semantic_score=None, platform_fee_bps=500, paypal_payout_fee_micros=250_000, runtime=None):
        self.calls.append({"worker_code": worker_code, "test_code": buyer_test_code,
                           "semantic_score": semantic_score})
        score = self.score
        split = compute_settlement_split(budget_micros, score, ast_security_passed=self.ast_safe,
                                         platform_fee_bps=platform_fee_bps,
                                         paypal_payout_fee_micros=paypal_payout_fee_micros)
        return ArbitrationReport(
            agreement_id=agreement_id, s_pytest=score, s_ast=1.0 if self.ast_safe else 0.0,
            s_semantic=score, composite_score=score, passed_tests=1, total_tests=1,
            ast_safe=self.ast_safe, settlement=split,
        )


def settings(**over) -> Settings:
    over.setdefault("PAYPAL_MODE", "mock")
    over.setdefault("SANDBOX_MODE", "subprocess")
    return Settings(_env_file=None, **over)


def make_orch(cfg=None, arbiter=None, engine=None):
    repo = Repository.from_url("sqlite:///:memory:")
    ledger = DoubleEntryLedgerEngine.from_repository(repo)
    orch = EscrowOrchestrator(repo, ledger, engine or MockPayPalEscrowEngine(), cfg or settings(),
                              arbiter=arbiter or RecordingArbiter())
    return orch


def payload(**over) -> SlaContractCreate:
    base = dict(buyer_agent_id="buyer", worker_agent_id="worker", worker_paypal_email="w@example.com",
                budget_micros=15_000_000, task_description="Build a rate limiter")
    base.update(over)
    return SlaContractCreate(**base)


def api_body(**over):
    body = {"buyer_agent_id": "buyer", "worker_agent_id": "worker", "worker_paypal_email": "w@example.com",
            "budget_micros": 15_000_000, "task_description": "Build a rate limiter"}
    body.update(over)
    return body


@pytest.fixture()
def client(tmp_path):
    app = create_app(settings(DATABASE_URL=f"sqlite:///{tmp_path}/t.db"), engine=MockPayPalEscrowEngine())
    app.state.orchestrator.arbiter = RecordingArbiter()
    with TestClient(app) as c:
        yield c, app


# ------------------------------------------------------------- test suite is bound to the agreement
async def test_committed_suite_is_stored_with_its_hash():
    orch = make_orch()
    c = await orch.create_contract(payload(test_suite=SUITE))
    stored = orch.get(c.agreement_id)
    assert stored.test_suite == SUITE
    assert stored.test_suite_sha256 == sha256_hex(SUITE)
    assert stored.to_response().test_suite_sha256 == sha256_hex(SUITE)


async def test_submission_is_graded_against_the_committed_suite():
    arbiter = RecordingArbiter()
    orch = make_orch(arbiter=arbiter)
    c = await orch.create_contract(payload(test_suite=SUITE))
    await orch.submit_work(c.agreement_id, "def f(): ...")
    assert [call["test_code"] for call in arbiter.calls] == [SUITE]


async def test_repeating_the_committed_suite_is_accepted():
    arbiter = RecordingArbiter()
    orch = make_orch(arbiter=arbiter)
    c = await orch.create_contract(payload(test_suite=SUITE))
    await orch.submit_work(c.agreement_id, "def f(): ...", SUITE)
    assert arbiter.calls[0]["test_code"] == SUITE


async def test_submitter_cannot_swap_in_their_own_suite():
    arbiter = RecordingArbiter()
    orch = make_orch(arbiter=arbiter)
    c = await orch.create_contract(payload(test_suite=SUITE))
    with pytest.raises(PolicyViolationError, match="fixed by the agreement"):
        await orch.submit_work(c.agreement_id, "def f(): ...", ATTACKER_SUITE)
    assert arbiter.calls == []
    assert orch.get(c.agreement_id).fsm_state == FsmState.PAYPAL_AUTH_LOCKED


async def test_agreement_without_a_suite_is_refused_outside_mock_mode():
    orch = make_orch(cfg=settings(PAYPAL_MODE="sandbox"))
    c = await orch.create_contract(payload())
    with pytest.raises(PolicyViolationError, match="no committed test suite"):
        await orch.submit_work(c.agreement_id, "def f(): ...", ATTACKER_SUITE)
    assert orch.get(c.agreement_id).fsm_state == FsmState.PAYPAL_AUTH_LOCKED


async def test_unbound_suite_is_allowed_in_mock_mode_and_by_explicit_flag():
    for cfg in (settings(), settings(PAYPAL_MODE="sandbox", ALLOW_UNBOUND_TEST_SUITE=True)):
        arbiter = RecordingArbiter()
        orch = make_orch(cfg=cfg, arbiter=arbiter)
        c = await orch.create_contract(payload())
        await orch.submit_work(c.agreement_id, "def f(): ...", ATTACKER_SUITE)
        assert arbiter.calls[0]["test_code"] == ATTACKER_SUITE


async def test_a_suite_is_required_even_when_unbound_suites_are_allowed():
    orch = make_orch()
    c = await orch.create_contract(payload())
    with pytest.raises(PolicyViolationError, match="required"):
        await orch.submit_work(c.agreement_id, "def f(): ...")


async def test_semantic_score_override_is_refused_outside_mock_mode():
    orch = make_orch(cfg=settings(PAYPAL_MODE="sandbox"))
    c = await orch.create_contract(payload(test_suite=SUITE))
    with pytest.raises(PolicyViolationError, match="semantic_score"):
        await orch.submit_work(c.agreement_id, "def f(): ...", semantic_score=1.0)
    assert orch.get(c.agreement_id).fsm_state == FsmState.PAYPAL_AUTH_LOCKED


async def test_semantic_score_override_still_works_in_mock_mode():
    arbiter = RecordingArbiter()
    orch = make_orch(arbiter=arbiter)
    c = await orch.create_contract(payload(test_suite=SUITE))
    await orch.submit_work(c.agreement_id, "def f(): ...", semantic_score=0.5)
    assert arbiter.calls[0]["semantic_score"] == 0.5


def test_api_maps_policy_violations_to_403_and_leaves_state_alone(client):
    c, app = client
    aid = c.post(f"{P}/contracts", json=api_body(test_suite=SUITE)).json()["agreement_id"]
    r = c.post(f"{P}/contracts/{aid}/submit", json={"source_code": "def f(): ...", "test_suite": ATTACKER_SUITE})
    assert r.status_code == 403 and "fixed by the agreement" in r.json()["detail"]
    assert c.get(f"{P}/contracts/{aid}").json()["fsm_state"] == "PAYPAL_AUTH_LOCKED"
    ok = c.post(f"{P}/contracts/{aid}/submit", json={"source_code": "def f(): ..."})
    assert ok.status_code == 200
    assert app.state.orchestrator.arbiter.calls[0]["test_code"] == SUITE


def test_api_exposes_the_hash_but_not_the_suite_source(client):
    c, _ = client
    created = c.post(f"{P}/contracts", json=api_body(test_suite=SUITE)).json()
    assert created["test_suite_sha256"] == sha256_hex(SUITE)
    assert SUITE not in str(created)


def test_mcp_submit_cannot_swap_the_suite_either(client):
    c, _ = client
    aid = c.post(f"{P}/contracts", json=api_body(test_suite=SUITE)).json()["agreement_id"]
    r = c.post(f"{P}/mcp/execute", json={"name": "aegispay_submit_deliverable", "arguments": {
        "agreement_id": aid, "source_code": "def f(): ...", "test_suite": ATTACKER_SUITE}})
    assert r.status_code == 403


# ------------------------------------------------------------- the test suite is scanned and untrusted
class NeverRunRunner:
    """A runner that fails the test if anything is ever executed."""

    isolated = False

    def run_tests(self, *args, **kwargs):
        raise AssertionError("untrusted code must not run when the scan fails")


class FakeIsolatedRunner:
    isolated = True

    def run_tests(self, worker_code, test_code, **kwargs):
        return 1, 1, "1 passed"


# Same attack as the audit proof of concept: clean-but-wrong worker code, malicious test file.
ESCAPING_TESTS = (
    "import socket\n"
    "def test_always_passes():\n"
    "    open('/tmp/stolen', 'w').write(open('/etc/secret').read())\n"
    "    assert True\n"
)
CLEAN_WRONG_WORKER = "def add(a, b):\n    return a - b\n"


def test_malicious_test_suite_is_vetoed_and_never_executed():
    arb = ArbitrationArbiter(runner=NeverRunRunner())
    rep = arb.evaluate("agr_x", CLEAN_WRONG_WORKER, ESCAPING_TESTS, semantic_score=1.0, runtime="python")
    assert rep.ast_safe is False
    assert rep.composite_score == 0.0
    assert rep.settlement.decision == "VOID_AUTHORIZATION"
    assert any(v.startswith("test suite: Forbidden import: 'socket'") for v in rep.ast_violations)


def test_worker_imports_are_still_reported_without_the_test_suite_prefix():
    arb = ArbitrationArbiter(runner=NeverRunRunner())
    rep = arb.evaluate("agr_x", "import os\n", "def test_x():\n    assert True\n", runtime="python")
    assert rep.ast_violations == ["Forbidden import: 'os' at line 1"]


def test_clean_worker_and_clean_suite_still_run():
    arb = ArbitrationArbiter(runner=FakeIsolatedRunner())
    rep = arb.evaluate("agr_x", "def f():\n    return 1\n", "def test_f():\n    assert True\n", runtime="python")
    assert rep.ast_safe is True and rep.passed_tests == 1


@pytest.mark.parametrize("module", ["importlib", "pickle", "pathlib", "sqlite3", "http.client", "tempfile"])
def test_python_scanner_blocks_modules_that_bypass_the_denylist(module):
    safe, violations = ArbitrationArbiter().scanner.scan(f"import {module}\n")
    assert not safe and violations


@pytest.mark.parametrize("snippet", [
    "const cp = require('child_' + 'process');",
    "const cp = require(`child_process`);",
    "const name = 'child_process'; require(name);",
    "const r = require; r('child_process');",
    "import('child_process').then(m => m);",
    "const e = process.env.PAYPAL_CLIENT_SECRET;",
    "const e = process['env'];",
    "globalThis['pro' + 'cess'];",
    "(function(){}).constructor.constructor('return process')();",
    "const v = require('node:v8');",
])
def test_js_scanner_blocks_known_bypasses(snippet):
    safe, violations = JsSecurityScanner().scan(snippet)
    assert not safe and violations


def test_js_scanner_still_accepts_ordinary_code():
    safe, violations = JsSecurityScanner().scan(
        "const { test } = require('node:test');\nmodule.exports = { add: (a, b) => a + b };\n"
    )
    assert safe, violations


# ------------------------------------------------------------------ no isolation, no real-money runs
def test_only_docker_counts_as_isolated():
    assert CodeExecutionRunner.isolated is False
    assert NodeCodeRunner.isolated is False
    assert DockerExecutionRunner.isolated is True


def test_arbiter_reports_isolation_per_runtime():
    arb = ArbitrationArbiter(runner=FakeIsolatedRunner())
    assert arb.is_isolated("def f(): ...", "def test_f(): ...", "python") is True
    assert arb.is_isolated("const x = 1;", "test('a', () => {});", "javascript") is False


async def test_unisolated_sandbox_is_refused_outside_mock_mode_before_any_state_change():
    arb = ArbitrationArbiter(runner=NeverRunRunner())
    orch = make_orch(cfg=settings(PAYPAL_MODE="sandbox"), arbiter=arb)
    c = await orch.create_contract(payload(test_suite=SUITE))
    with pytest.raises(SandboxUnavailableError, match="isolated sandbox"):
        await orch.submit_work(c.agreement_id, "def f(): ...")
    assert orch.get(c.agreement_id).fsm_state == FsmState.PAYPAL_AUTH_LOCKED


async def test_isolated_sandbox_is_accepted_outside_mock_mode():
    arb = ArbitrationArbiter(runner=FakeIsolatedRunner())
    orch = make_orch(cfg=settings(PAYPAL_MODE="sandbox"), arbiter=arb)
    c = await orch.create_contract(payload(test_suite=SUITE))
    done = await orch.submit_work(c.agreement_id, "def f():\n    return 1\n")
    assert done.fsm_state in (FsmState.PAYOUT_DISPATCHED, FsmState.SETTLED_COMPLETE)


async def test_javascript_is_refused_outside_mock_mode_even_with_a_docker_python_runner():
    arb = ArbitrationArbiter(runner=FakeIsolatedRunner())
    orch = make_orch(cfg=settings(PAYPAL_MODE="sandbox"), arbiter=arb)
    c = await orch.create_contract(payload(test_suite="test('a', () => { expect(1).toBe(1); });"))
    with pytest.raises(SandboxUnavailableError):
        await orch.submit_work(c.agreement_id, "module.exports = { a: 1 };", runtime="javascript")


async def test_explicit_flag_allows_unisolated_runs():
    arb = ArbitrationArbiter(runner=NeverRunRunner())
    orch = make_orch(cfg=settings(PAYPAL_MODE="sandbox", ALLOW_UNISOLATED_SANDBOX=True), arbiter=arb)
    c = await orch.create_contract(payload(test_suite=SUITE))
    done = await orch.submit_work(c.agreement_id, "import os\n")  # vetoed by the scan, so nothing runs
    assert done.fsm_state == FsmState.ESCROW_VOIDED_REFUNDED


async def test_malicious_suite_is_rejected_when_the_agreement_is_created():
    orch = make_orch(arbiter=ArbitrationArbiter(runner=NeverRunRunner()))
    with pytest.raises(PolicyViolationError, match="security scan"):
        await orch.create_contract(payload(test_suite=ESCAPING_TESTS))
    assert orch.repo.list_contracts() == []


def test_api_returns_503_when_no_isolated_sandbox_exists(tmp_path):
    cfg = settings(PAYPAL_MODE="sandbox", API_AUTH_KEY="k", DATABASE_URL=f"sqlite:///{tmp_path}/s.db")
    app = create_app(cfg, engine=MockPayPalEscrowEngine())
    app.state.orchestrator.arbiter = ArbitrationArbiter(runner=NeverRunRunner())
    h = {"X-API-Key": "k"}
    with TestClient(app) as c:
        aid = c.post(f"{P}/contracts", json=api_body(test_suite=SUITE), headers=h).json()["agreement_id"]
        r = c.post(f"{P}/contracts/{aid}/submit", json={"source_code": "def f(): ..."}, headers=h)
        assert r.status_code == 503


# ------------------------------------------------------------------------------ API authentication
KEY = "test-api-key-123"
KEY_HEADERS = {"X-API-Key": KEY}

PROTECTED = [
    ("get", "/contracts", None),
    ("post", "/contracts", api_body()),
    ("get", "/ledger", None),
    ("post", "/sweeper/run", {}),
    ("get", "/sweeper/status", None),
    ("post", "/vaults", {"vault_id": "v", "buyer_did": "did:x"}),
    ("post", "/vaults/v/reset", None),
    ("post", "/vault/refresh", None),
    ("post", "/vault/freeze", None),
    ("post", "/mcp/execute", {"name": "aegispay_check_escrow_status", "arguments": {"agreement_id": "x"}}),
    ("post", "/a2a/negotiate", {"buyer_pref": {}, "worker_pref": {}}),
]


def call(c, method, path, body=None, headers=None):
    return getattr(c, method)(f"{P}{path}", **({"json": body} if body is not None else {}), headers=headers or {})


def app_client(tmp_path, **cfg):
    cfg.setdefault("DATABASE_URL", f"sqlite:///{tmp_path}/a.db")
    app = create_app(settings(**cfg), engine=MockPayPalEscrowEngine())
    app.state.orchestrator.arbiter = RecordingArbiter()
    return TestClient(app)


def test_mock_mode_without_a_key_stays_open_for_local_demos(tmp_path):
    with app_client(tmp_path) as c:
        assert c.get(f"{P}/health").status_code == 200
        assert c.get(f"{P}/contracts").status_code == 200


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_outside_mock_mode_requests_are_refused_until_a_key_is_configured(tmp_path, method, path, body):
    with app_client(tmp_path, PAYPAL_MODE="sandbox") as c:
        assert call(c, method, path, body).status_code == 503
        assert call(c, method, path, body, KEY_HEADERS).status_code == 503


@pytest.mark.parametrize("method,path,body", PROTECTED)
@pytest.mark.parametrize("mode", ["mock", "sandbox"])
def test_a_configured_key_is_required_in_every_mode(tmp_path, mode, method, path, body):
    with app_client(tmp_path, PAYPAL_MODE=mode, API_AUTH_KEY=KEY) as c:
        assert call(c, method, path, body).status_code == 401
        assert call(c, method, path, body, {"X-API-Key": "wrong"}).status_code == 401
        assert call(c, method, path, body, {"X-API-Key": KEY[:-1]}).status_code == 401
        assert call(c, method, path, body, KEY_HEADERS).status_code != 401


def test_health_and_the_signed_webhook_never_need_the_key(tmp_path):
    with app_client(tmp_path, PAYPAL_MODE="sandbox", API_AUTH_KEY=KEY, PAYPAL_WEBHOOK_MOCK_BYPASS=True) as c:
        assert c.get(f"{P}/health").status_code == 200
        event = {"id": "WH-1", "event_type": "PAYMENT.AUTHORIZATION.VOIDED", "resource": {"id": "A"}}
        assert c.post(f"{P}/webhooks/paypal", json=event).status_code == 200


def test_health_is_open_even_when_unconfigured_outside_mock_mode(tmp_path):
    with app_client(tmp_path, PAYPAL_MODE="sandbox") as c:
        assert c.get(f"{P}/health").status_code == 200


# ------------------------------------------------------------------------------------- WebSocket
def ws_rejected(c, headers=None):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/api/v1/ws/ledger", headers=headers or {}):
            pass


def test_websocket_rejects_foreign_origins_even_in_mock_mode(tmp_path):
    with app_client(tmp_path) as c:
        ws_rejected(c, {"origin": "https://evil.example"})
        with c.websocket_connect("/api/v1/ws/ledger", headers={"origin": "http://localhost:3000"}) as ws:
            assert ws.receive_json()["event_type"] == "INITIAL_SNAPSHOT"


def test_websocket_needs_the_key_when_one_is_configured(tmp_path):
    with app_client(tmp_path, API_AUTH_KEY=KEY) as c:
        ws_rejected(c)
        ws_rejected(c, {"x-api-key": "wrong"})
        with c.websocket_connect("/api/v1/ws/ledger", headers=KEY_HEADERS) as ws:
            assert ws.receive_json()["event_type"] == "INITIAL_SNAPSHOT"


def test_websocket_is_closed_outside_mock_mode_without_a_key(tmp_path):
    with app_client(tmp_path, PAYPAL_MODE="sandbox") as c:
        ws_rejected(c)


# ------------------------------------------------------------------- demo-only overrides are mock-only
FUTURE = "2099-01-01T00:00:00+00:00"


def test_sweeper_clock_override_is_refused_outside_mock_mode(tmp_path):
    with app_client(tmp_path, PAYPAL_MODE="sandbox", API_AUTH_KEY=KEY) as c:
        aid = c.post(f"{P}/contracts", json=api_body(test_suite=SUITE), headers=KEY_HEADERS).json()["agreement_id"]
        r = c.post(f"{P}/sweeper/run", json={"simulate_now": FUTURE}, headers=KEY_HEADERS)
        assert r.status_code == 403
        assert c.post(f"{P}/sweeper/run", json={"threshold_hours": 0}, headers=KEY_HEADERS).status_code == 403
        assert c.get(f"{P}/sweeper/status", params={"simulate_now": FUTURE}, headers=KEY_HEADERS).status_code == 403
        assert c.post(f"{P}/sweeper/run", json={}, headers=KEY_HEADERS).status_code == 200
        assert c.get(f"{P}/contracts/{aid}", headers=KEY_HEADERS).json()["fsm_state"] == "PAYPAL_AUTH_LOCKED"


def test_sweeper_clock_override_still_works_in_mock_mode(tmp_path):
    with app_client(tmp_path) as c:
        aid = c.post(f"{P}/contracts", json=api_body()).json()["agreement_id"]
        assert c.post(f"{P}/sweeper/run", json={"simulate_now": FUTURE}).json()["voided_count"] == 1
        assert c.get(f"{P}/contracts/{aid}").json()["fsm_state"] == "ESCROW_VOIDED_REFUNDED"


def test_vault_spend_reset_and_demo_vault_creation_are_mock_only(tmp_path):
    with app_client(tmp_path, PAYPAL_MODE="sandbox", API_AUTH_KEY=KEY) as c:
        c.post(f"{P}/vaults", json={"vault_id": "v", "buyer_did": "did:x", "current_daily_spent_micros": 5}, headers=KEY_HEADERS)
        assert c.post(f"{P}/vault/refresh", params={"vault_id": "v"}, headers=KEY_HEADERS).status_code == 403
        assert c.get(f"{P}/vaults/v", headers=KEY_HEADERS).json()["current_daily_spent_micros"] == 5
        assert c.get(f"{P}/vault/status", headers=KEY_HEADERS).status_code == 403
        assert c.post(f"{P}/vaults/ghost/freeze", headers=KEY_HEADERS).status_code == 403
        assert c.get(f"{P}/vaults/ghost", headers=KEY_HEADERS).status_code == 404


def test_circuit_breaker_reset_stays_available_to_key_holders(tmp_path):
    with app_client(tmp_path, PAYPAL_MODE="sandbox", API_AUTH_KEY=KEY) as c:
        c.post(f"{P}/vaults", json={"vault_id": "v", "buyer_did": "did:x", "is_frozen": 1}, headers=KEY_HEADERS)
        r = c.post(f"{P}/vaults/v/reset", headers=KEY_HEADERS)
        assert r.status_code == 200 and r.json()["vault"]["is_frozen"] == 0


def test_mcp_with_missing_arguments_is_a_422_not_a_500(tmp_path):
    with app_client(tmp_path) as c:
        r = c.post(f"{P}/mcp/execute", json={"name": "aegispay_submit_deliverable", "arguments": {}})
        assert r.status_code == 422
        r = c.post(f"{P}/mcp/execute", json={"name": "aegispay_propose_agreement", "arguments": {"buyer_agent_id": "b"}})
        assert r.status_code == 422
