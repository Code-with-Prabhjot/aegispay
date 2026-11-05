"""Arbitration must not hold the lock that webhooks and other requests wait on."""

import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.schemas import FsmState
from backend.app.services import fsm
from tests.test_trust_policy import SUITE, RecordingArbiter, make_orch, payload
from tests.test_webhooks import authorization_voided


class BlockedArbiter(RecordingArbiter):
    """Evaluation that waits until the test lets it finish."""

    def __init__(self, score=1.0):
        super().__init__(score=score)
        self.started = threading.Event()
        self.release = threading.Event()

    def evaluate(self, *args, **kwargs):
        self.started.set()
        assert self.release.wait(timeout=10), "test never released the arbiter"
        return super().evaluate(*args, **kwargs)


async def wait_until_evaluating(arbiter):
    for _ in range(200):
        if arbiter.started.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("evaluation never started")


async def start_submission(arbiter=None, **orch_kw):
    arbiter = arbiter or BlockedArbiter()
    orch = make_orch(arbiter=arbiter, **orch_kw)
    c = await orch.create_contract(payload(test_suite=SUITE))
    task = asyncio.create_task(orch.submit_work(c.agreement_id, "def f(): ..."))
    await wait_until_evaluating(arbiter)
    return orch, arbiter, c.agreement_id, task


async def test_other_requests_proceed_while_an_evaluation_runs():
    orch, arbiter, aid, task = await start_submission()
    try:
        other = await asyncio.wait_for(orch.create_contract(payload(test_suite=SUITE)), timeout=3)
        assert other.fsm_state == FsmState.PAYPAL_AUTH_LOCKED
        assert orch.get(aid).fsm_state == FsmState.ARBITRATION_AUDITING
    finally:
        arbiter.release.set()
    done = await asyncio.wait_for(task, timeout=10)
    assert done.fsm_state in (FsmState.PAYOUT_DISPATCHED, FsmState.SETTLED_COMPLETE)


async def test_a_webhook_for_the_same_contract_is_not_blocked_and_changes_nothing():
    orch, arbiter, aid, task = await start_submission()
    try:
        rec = orch.get(aid)
        event = authorization_voided(auth_id=rec.paypal_authorization_id)
        event["resource"]["custom_id"] = aid
        out = await asyncio.wait_for(orch.handle_webhook(event), timeout=3)
        assert out["status"] == "ignored_illegal_state"
        assert orch.get(aid).fsm_state == FsmState.ARBITRATION_AUDITING
    finally:
        arbiter.release.set()
    await asyncio.wait_for(task, timeout=10)


async def test_a_second_submission_during_evaluation_is_refused():
    orch, arbiter, aid, task = await start_submission()
    try:
        with pytest.raises(fsm.FsmError):
            await asyncio.wait_for(orch.submit_work(aid, "def g(): ..."), timeout=3)
    finally:
        arbiter.release.set()
    await asyncio.wait_for(task, timeout=10)
    assert len(arbiter.calls) == 1  # graded once


async def test_contract_aborted_while_evaluating_is_not_settled_a_second_time():
    orch, arbiter, aid, task = await start_submission()
    base = datetime.now(timezone.utc)
    orch.clock = lambda: base + timedelta(minutes=11)
    try:
        aborted = await asyncio.wait_for(orch.retry_settlement(aid), timeout=3)
        assert aborted.fsm_state == FsmState.ESCROW_VOIDED_REFUNDED
    finally:
        arbiter.release.set()
    with pytest.raises(fsm.FsmError, match="changed while it was being evaluated"):
        await asyncio.wait_for(task, timeout=10)
    assert orch.get(aid).fsm_state == FsmState.ESCROW_VOIDED_REFUNDED
    bal = orch.ledger.get_all_balances()
    assert (bal["buyer_vault"], bal["paypal_escrow_hold"], bal["worker_payable"]) == (15_000_000, 0, 0)
    assert orch.ledger.verify_balance_conservation()


async def test_an_arbiter_crash_during_unlocked_evaluation_still_voids_the_hold():
    class Crashing(BlockedArbiter):
        def evaluate(self, *a, **k):
            self.started.set()
            self.release.wait(timeout=10)
            raise RuntimeError("runner exploded")

    arbiter = Crashing()
    orch, arbiter, aid, task = await start_submission(arbiter)
    arbiter.release.set()
    done = await asyncio.wait_for(task, timeout=10)
    assert done.fsm_state == FsmState.ESCROW_VOIDED_REFUNDED
    assert orch.ledger.get_all_balances()["paypal_escrow_hold"] == 0
