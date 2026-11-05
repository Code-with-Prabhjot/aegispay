"""Recovery from failures mid-settlement: no contract may be left with funds locked and no way out."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.schemas import FsmState
from backend.app.services import fsm
from backend.app.services.orchestrator import UpstreamError
from backend.app.services.paypal_mock import MockPayPalEscrowEngine
from tests.test_trust_policy import (
    P,
    SUITE,
    RecordingArbiter,
    api_body,
    make_orch,
    payload,
    settings,
)

BUDGET = 15_000_000


class FlakyEngine(MockPayPalEscrowEngine):
    """Mock PayPal that can fail a number of capture / void / payout calls first."""

    def __init__(self, fail_capture=0, fail_void=0, fail_payout=0):
        super().__init__()
        self.fail = {"capture": fail_capture, "void": fail_void, "payout": fail_payout}
        self.calls = {"capture": 0, "void": 0, "payout": 0}

    def _maybe_fail(self, kind):
        self.calls[kind] += 1
        if self.fail[kind] > 0:
            self.fail[kind] -= 1
            raise httpx.ConnectError("PayPal unreachable")

    async def capture_authorization(self, *a, **k):
        self._maybe_fail("capture")
        return await super().capture_authorization(*a, **k)

    async def void_authorization(self, *a, **k):
        self._maybe_fail("void")
        return await super().void_authorization(*a, **k)

    async def dispatch_worker_payout(self, *a, **k):
        self._maybe_fail("payout")
        return await super().dispatch_worker_payout(*a, **k)


class CrashingArbiter(RecordingArbiter):
    def evaluate(self, *args, **kwargs):
        raise RuntimeError("sandbox runner exploded")


def balances(orch):
    return orch.ledger.get_all_balances()


# ------------------------------------------------------------------------ arbiter crash voids the hold
async def test_arbiter_crash_voids_the_hold_instead_of_stranding_it():
    orch = make_orch(arbiter=CrashingArbiter())
    c = await orch.create_contract(payload(test_suite=SUITE))
    done = await orch.submit_work(c.agreement_id, "def f(): ...")
    assert done.fsm_state == FsmState.ESCROW_VOIDED_REFUNDED
    assert "Arbiter failure (RuntimeError)" in done.report["stdout"]
    assert done.report["settlement"]["decision"] == "VOID_AUTHORIZATION"
    bal = balances(orch)
    assert (bal["buyer_vault"], bal["paypal_escrow_hold"], bal["worker_payable"]) == (BUDGET, 0, 0)
    assert orch.ledger.verify_balance_conservation()


async def test_arbiter_crash_with_paypal_down_leaves_a_retryable_void():
    engine = FlakyEngine(fail_void=1)
    orch = make_orch(arbiter=CrashingArbiter(), engine=engine)
    c = await orch.create_contract(payload(test_suite=SUITE))
    with pytest.raises(UpstreamError):
        await orch.submit_work(c.agreement_id, "def f(): ...")
    assert orch.get(c.agreement_id).fsm_state == FsmState.VOID_PENDING
    assert balances(orch)["paypal_escrow_hold"] == BUDGET  # still held until PayPal confirms

    done = await orch.retry_settlement(c.agreement_id)
    assert done.fsm_state == FsmState.ESCROW_VOIDED_REFUNDED
    bal = balances(orch)
    assert (bal["buyer_vault"], bal["paypal_escrow_hold"]) == (BUDGET, 0)


# --------------------------------------------------------------------- failed capture can be retried
@pytest.mark.parametrize("score,pending,capture_micros", [
    (0.95, FsmState.FULL_CAPTURE_PENDING, BUDGET),
    (0.75, FsmState.PARTIAL_CAPTURE_PENDING, 10_500_000),
])
async def test_failed_capture_is_recoverable_and_pays_exactly_once(score, pending, capture_micros):
    engine = FlakyEngine(fail_capture=1)
    orch = make_orch(arbiter=RecordingArbiter(score=score), engine=engine)
    c = await orch.create_contract(payload(test_suite=SUITE))
    with pytest.raises(UpstreamError):
        await orch.submit_work(c.agreement_id, "def f(): ...")
    stuck = orch.get(c.agreement_id)
    assert stuck.fsm_state == pending
    assert balances(orch)["worker_payable"] == 0

    done = await orch.retry_settlement(c.agreement_id)
    assert done.fsm_state == FsmState.PAYOUT_DISPATCHED
    assert done.paypal_capture_id and done.paypal_payout_batch_id
    assert engine.calls["capture"] == 2
    bal = balances(orch)
    assert bal["worker_payable"] + bal["platform_fee_revenue"] + bal["paypal_rail_expense"] == capture_micros
    assert orch.ledger.verify_balance_conservation()

    with pytest.raises(fsm.FsmError):  # a settled contract cannot be re-driven
        await orch.retry_settlement(c.agreement_id)
    assert engine.calls["capture"] == 2


async def test_retry_surfaces_a_second_failure_and_can_be_repeated():
    engine = FlakyEngine(fail_capture=2)
    orch = make_orch(engine=engine)
    c = await orch.create_contract(payload(test_suite=SUITE))
    with pytest.raises(UpstreamError):
        await orch.submit_work(c.agreement_id, "def f(): ...")
    with pytest.raises(UpstreamError):
        await orch.retry_settlement(c.agreement_id)
    assert orch.get(c.agreement_id).fsm_state == FsmState.FULL_CAPTURE_PENDING
    assert (await orch.retry_settlement(c.agreement_id)).fsm_state == FsmState.PAYOUT_DISPATCHED


async def test_declined_capture_on_retry_ends_in_capture_declined():
    engine = FlakyEngine(fail_capture=1)
    orch = make_orch(engine=engine)
    c = await orch.create_contract(payload(test_suite=SUITE))
    with pytest.raises(UpstreamError):
        await orch.submit_work(c.agreement_id, "def f(): ...")
    engine.decline_captures_for.add(c.agreement_id)
    done = await orch.retry_settlement(c.agreement_id)
    assert done.fsm_state == FsmState.CAPTURE_DECLINED
    assert balances(orch)["worker_payable"] == 0


@pytest.mark.parametrize("state", [FsmState.AGREEMENT_DRAFTED, FsmState.PAYPAL_AUTH_LOCKED,
                                   FsmState.SETTLED_COMPLETE, FsmState.CAPTURE_DECLINED,
                                   FsmState.ESCROW_VOIDED_REFUNDED])
async def test_nothing_to_retry_in_other_states(state):
    orch = make_orch()
    c = await orch.create_contract(payload(test_suite=SUITE))
    rec = orch.get(c.agreement_id)
    rec.fsm_state = state
    orch.repo.save_contract(rec)
    with pytest.raises(fsm.FsmError, match="Nothing to retry"):
        await orch.retry_settlement(c.agreement_id)


# ---------------------------------------------------------------- an evaluation that died is aborted
async def _contract_stuck_in_arbitration(orch):
    c = await orch.create_contract(payload(test_suite=SUITE))
    base = datetime.now(timezone.utc)
    orch.clock = lambda: base
    rec = orch.get(c.agreement_id)
    orch._move(rec, FsmState.SANDBOX_EXECUTING, "submitted")
    orch._move(rec, FsmState.ARBITRATION_AUDITING, "evaluating")
    return c.agreement_id, base


async def test_dead_evaluation_is_voided_after_the_grace_period():
    orch = make_orch()
    aid, base = await _contract_stuck_in_arbitration(orch)
    orch.clock = lambda: base + timedelta(minutes=11)
    done = await orch.retry_settlement(aid)
    assert done.fsm_state == FsmState.ESCROW_VOIDED_REFUNDED
    assert balances(orch)["buyer_vault"] == BUDGET


async def test_live_evaluation_is_left_alone():
    orch = make_orch()
    aid, base = await _contract_stuck_in_arbitration(orch)
    orch.clock = lambda: base + timedelta(minutes=2)
    with pytest.raises(fsm.FsmError, match="still being evaluated"):
        await orch.retry_settlement(aid)
    assert orch.get(aid).fsm_state == FsmState.ARBITRATION_AUDITING


# ------------------------------------------------------------------------------------------- API
def test_api_settlement_retry_endpoint(tmp_path):
    engine = FlakyEngine(fail_capture=1)
    app = create_app(settings(DATABASE_URL=f"sqlite:///{tmp_path}/r.db"), engine=engine)
    app.state.orchestrator.arbiter = RecordingArbiter()
    with TestClient(app) as c:
        aid = c.post(f"{P}/contracts", json=api_body(test_suite=SUITE)).json()["agreement_id"]
        assert c.post(f"{P}/contracts/{aid}/submit", json={"source_code": "def f(): ..."}).status_code == 502
        assert c.get(f"{P}/contracts/{aid}").json()["fsm_state"] == "FULL_CAPTURE_PENDING"
        r = c.post(f"{P}/contracts/{aid}/settlement/retry")
        assert r.status_code == 200 and r.json()["fsm_state"] == "PAYOUT_DISPATCHED"
        assert c.post(f"{P}/contracts/{aid}/settlement/retry").status_code == 409
        assert c.post(f"{P}/contracts/nope/settlement/retry").status_code == 404
