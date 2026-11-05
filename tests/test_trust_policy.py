"""Trust policy: who may choose the tests, the score, and what runs where."""

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import Repository
from backend.app.main import create_app
from backend.app.schemas import ArbitrationReport, FsmState, SlaContractCreate
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.orchestrator import (
    EscrowOrchestrator,
    PolicyViolationError,
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
