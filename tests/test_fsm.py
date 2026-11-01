"""FSM transition engine tests."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.schemas import FsmState as S
from backend.app.services import fsm

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def make(state=S.AGREEMENT_DRAFTED, authorized_at=None) -> fsm.ContractRecord:
    return fsm.ContractRecord(
        agreement_id="agr_t", buyer_agent_id="b", worker_agent_id="w",
        worker_paypal_email="w@example.com", budget_micros=15_000_000,
        task_description="do the thing", timeout_seconds=3600,
        fsm_state=state, authorized_at=authorized_at, created_at=NOW, updated_at=NOW,
    )


def walk(c, *states):
    for s in states:
        fsm.transition(c, s, now=NOW)


def test_table_covers_every_state():
    assert set(fsm.LEGAL_TRANSITIONS) == set(S)


def test_terminal_states():
    assert fsm.TERMINAL_STATES == {
        S.AUTH_EXPIRED, S.CAPTURE_DECLINED, S.ESCROW_VOIDED_REFUNDED, S.SETTLED_COMPLETE,
    }


def test_full_capture_happy_path_records_history():
    c = make()
    walk(c, S.PAYPAL_AUTH_LOCKED, S.SANDBOX_EXECUTING, S.ARBITRATION_AUDITING,
         S.FULL_CAPTURE_PENDING, S.PAYPAL_CAPTURED, S.PAYOUT_DISPATCHED, S.SETTLED_COMPLETE)
    assert c.fsm_state == S.SETTLED_COMPLETE
    assert [t.to_state for t in c.transitions][0] == S.PAYPAL_AUTH_LOCKED
    assert len(c.transitions) == 7
    assert all(t.created_at == NOW for t in c.transitions)
    assert c.transitions[1].from_state == S.PAYPAL_AUTH_LOCKED


def test_partial_and_void_paths():
    c = make(S.ARBITRATION_AUDITING)
    walk(c, S.PARTIAL_CAPTURE_PENDING, S.PARTIAL_CAPTURED, S.PAYOUT_DISPATCHED)
    v = make(S.ARBITRATION_AUDITING)
    walk(v, S.VOID_PENDING, S.ESCROW_VOIDED_REFUNDED)
    assert v.fsm_state == S.ESCROW_VOIDED_REFUNDED


@pytest.mark.parametrize("start,bad", [
    (S.AGREEMENT_DRAFTED, S.SANDBOX_EXECUTING),
    (S.PAYPAL_AUTH_LOCKED, S.PAYPAL_CAPTURED),
    (S.ARBITRATION_AUDITING, S.PAYPAL_CAPTURED),
    (S.FULL_CAPTURE_PENDING, S.PARTIAL_CAPTURED),
    (S.PARTIAL_CAPTURE_PENDING, S.PAYPAL_CAPTURED),
    (S.VOID_PENDING, S.PAYPAL_CAPTURED),
    (S.PAYPAL_CAPTURED, S.SETTLED_COMPLETE),
    (S.PAYOUT_DISPATCHED, S.PAYPAL_CAPTURED),
])
def test_illegal_transitions_rejected_without_mutation(start, bad):
    c = make(start)
    with pytest.raises(fsm.IllegalTransitionError):
        fsm.transition(c, bad, now=NOW)
    assert c.fsm_state == start and c.transitions == []


@pytest.mark.parametrize("terminal", sorted(fsm.TERMINAL_STATES, key=lambda s: s.value))
def test_terminal_states_reject_everything(terminal):
    for target in S:
        c = make(terminal)
        with pytest.raises(fsm.IllegalTransitionError, match="terminal"):
            fsm.transition(c, target, now=NOW)


def test_auth_expiry_on_late_start():
    c = make(S.PAYPAL_AUTH_LOCKED, authorized_at=NOW - timedelta(hours=72, minutes=1))
    with pytest.raises(fsm.AuthorizationExpiredError):
        fsm.transition(c, S.SANDBOX_EXECUTING, now=NOW)
    assert c.fsm_state == S.AUTH_EXPIRED
    assert c.transitions[-1].to_state == S.AUTH_EXPIRED
    with pytest.raises(fsm.IllegalTransitionError):
        fsm.transition(c, S.SANDBOX_EXECUTING, now=NOW)


def test_auth_within_window_ok_and_boundary():
    c = make(S.PAYPAL_AUTH_LOCKED, authorized_at=NOW - timedelta(hours=72))
    fsm.transition(c, S.SANDBOX_EXECUTING, now=NOW)
    assert c.fsm_state == S.SANDBOX_EXECUTING


def test_expire_if_needed():
    c = make(S.PAYPAL_AUTH_LOCKED, authorized_at=NOW - timedelta(hours=100))
    assert fsm.expire_if_needed(c, NOW) is not None
    assert c.fsm_state == S.AUTH_EXPIRED
    fresh = make(S.PAYPAL_AUTH_LOCKED, authorized_at=NOW)
    assert fsm.expire_if_needed(fresh, NOW) is None


def test_capture_declined_halts_payout():
    for pending in (S.FULL_CAPTURE_PENDING, S.PARTIAL_CAPTURE_PENDING):
        c = make(pending)
        fsm.transition(c, S.CAPTURE_DECLINED, now=NOW)
        with pytest.raises(fsm.IllegalTransitionError):
            fsm.transition(c, S.PAYOUT_DISPATCHED, now=NOW)


def test_payout_failed_then_retry_then_settled():
    c = make(S.PAYOUT_DISPATCHED)
    walk(c, S.PAYOUT_FAILED_REVERSED, S.PAYOUT_DISPATCHED, S.SETTLED_COMPLETE)
    assert c.fsm_state == S.SETTLED_COMPLETE


def test_payout_event_mapping():
    f = fsm.target_state_for_payout_event
    assert f("PAYMENT.PAYOUTS-ITEM.SUCCEEDED") == S.SETTLED_COMPLETE
    assert f("PAYMENT.PAYOUTS-ITEM.FAILED") == S.PAYOUT_FAILED_REVERSED
    assert f("PAYMENT.PAYOUTS-ITEM.UNCLAIMED") == S.PAYOUT_FAILED_REVERSED
    assert f("PAYMENT.CAPTURE.COMPLETED") is None
    assert f("PAYMENT.PAYOUTS-ITEM.BLOCKED") is None


def test_decision_mapping():
    assert fsm.target_state_for_capture("FULL_CAPTURE") == S.FULL_CAPTURE_PENDING
    assert fsm.target_state_for_capture("PARTIAL_CAPTURE") == S.PARTIAL_CAPTURE_PENDING
    assert fsm.target_state_for_capture("VOID_AUTHORIZATION") == S.VOID_PENDING
