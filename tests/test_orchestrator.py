"""Orchestrator tests: mocked PayPal, real arbiter + ledger, no network, no DB."""
import pytest

from backend.app.schemas import FsmState, LedgerAccount, SlaContractCreate
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.orchestrator import (
    ALLOWED_TRANSITIONS,
    EscrowOrchestrator,
    IllegalTransitionError,
    MockPayPalEngine,
    OrchestratorError,
)
from backend.app.services.scenario_presets import get_scenario

B = LedgerAccount.BUYER_VAULT.value
E = LedgerAccount.PAYPAL_ESCROW_HOLD.value
W = LedgerAccount.WORKER_PAYABLE.value
F = LedgerAccount.PLATFORM_FEE_REVENUE.value
R = LedgerAccount.PAYPAL_RAIL_EXPENSE.value


def make(scenario_name, **mock_kw):
    sc = get_scenario(scenario_name)
    paypal = MockPayPalEngine(**mock_kw)
    orch = EscrowOrchestrator(paypal=paypal, ledger=DoubleEntryLedgerEngine())
    c = orch.create_agreement(SlaContractCreate(
        buyer_agent_id="buyer-1", worker_agent_id="worker-1",
        worker_paypal_email="worker@example.com",
        budget_micros=sc.budget_micros, task_description="Implement the requested function",
    ))
    return orch, paypal, sc, c


async def run(orch, sc, c):
    await orch.lock_escrow(c.agreement_id, fund_buyer_vault=True)
    return await orch.run_job(c.agreement_id, sc.worker_code, sc.buyer_test_code, sc.semantic_score)


def conserved(orch, total):
    return sum(orch.ledger.get_all_balances().values()) == total


async def test_clean_pass_full_capture_and_payout():
    orch, pp, sc, c = make("CLEAN_PASS")
    c = await run(orch, sc, c)
    assert c.fsm_state == FsmState.SETTLED_COMPLETE
    assert c.paypal_capture_id and c.paypal_payout_batch_id
    bal = orch.ledger.get_all_balances()
    assert bal[E] == 0 and bal[B] == 0
    assert bal[F] == 750_000 and bal[R] == 250_000
    assert bal[W] == 14_000_000
    assert conserved(orch, sc.budget_micros)


async def test_partial_capture_releases_remainder():
    orch, pp, sc, c = make("PARTIAL_DEFECT")
    c = await run(orch, sc, c)
    assert c.fsm_state == FsmState.SETTLED_COMPLETE
    assert ("capture", c.agreement_id, 10_500_000) in pp.calls
    assert orch.ledger.get_balance(B) == 4_500_000
    assert orch.ledger.get_balance(E) == 0
    assert conserved(orch, sc.budget_micros)


async def test_malicious_code_voids_and_refunds():
    orch, pp, sc, c = make("MALICIOUS_INJECTION")
    c = await run(orch, sc, c)
    assert c.fsm_state == FsmState.ESCROW_VOIDED_REFUNDED
    assert not any(x[0] in ("capture", "payout") for x in pp.calls)
    assert orch.ledger.get_balance(B) == sc.budget_micros
    assert orch.ledger.get_balance(E) == 0


async def test_capture_declined_releases_hold_no_payout():
    orch, pp, sc, c = make("CLEAN_PASS", decline_capture=True)
    c = await run(orch, sc, c)
    assert c.fsm_state == FsmState.CAPTURE_DECLINED
    assert not any(x[0] == "payout" for x in pp.calls)
    assert orch.ledger.get_balance(B) == sc.budget_micros


async def test_payout_failure_reverses_worker_funds():
    orch, pp, sc, c = make("CLEAN_PASS", fail_payout=True)
    c = await run(orch, sc, c)
    assert c.fsm_state == FsmState.PAYOUT_FAILED_REVERSED
    assert orch.ledger.get_balance(W) == 0
    assert orch.ledger.get_balance(B) == 14_000_000
    assert conserved(orch, sc.budget_micros)


async def test_auth_expiry_refunds_buyer():
    orch, pp, sc, c = make("CLEAN_PASS")
    await orch.lock_escrow(c.agreement_id, fund_buyer_vault=True)
    c = await orch.expire_authorization(c.agreement_id)
    assert c.fsm_state == FsmState.AUTH_EXPIRED
    assert orch.ledger.get_balance(B) == sc.budget_micros


async def test_illegal_transitions_rejected():
    orch, pp, sc, c = make("CLEAN_PASS")
    with pytest.raises(IllegalTransitionError):
        await orch.run_job(c.agreement_id, sc.worker_code, sc.buyer_test_code)  # not locked yet
    await run(orch, sc, c)
    with pytest.raises(IllegalTransitionError):
        await orch.lock_escrow(c.agreement_id)  # already settled


async def test_unapproved_order_blocks_lock():
    orch, pp, sc, c = make("CLEAN_PASS", skip_authorization=True)
    with pytest.raises(OrchestratorError):
        await orch.lock_escrow(c.agreement_id, fund_buyer_vault=True)
    assert c.fsm_state == FsmState.AGREEMENT_DRAFTED


def test_terminal_states_have_no_exits():
    for s in (FsmState.SETTLED_COMPLETE, FsmState.ESCROW_VOIDED_REFUNDED,
              FsmState.CAPTURE_DECLINED, FsmState.AUTH_EXPIRED, FsmState.PAYOUT_FAILED_REVERSED):
        assert ALLOWED_TRANSITIONS[s] == set()
