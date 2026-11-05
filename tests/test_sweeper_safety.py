"""The 72h sweeper must never destroy a real hold, record a fake one, or claim a refund PayPal didn't make."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from backend.app.schemas import FsmState
from backend.app.services.orchestrator import extract_authorization_id
from backend.app.services.paypal_mock import MockPayPalEscrowEngine
from backend.app.services.sweeper import ReauthorizationSweeper
from backend.app.services.vault_service import register_vault
from tests.test_trust_policy import SUITE, make_orch, payload

BUDGET = 15_000_000
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


class SweepEngine(MockPayPalEscrowEngine):
    """Mock PayPal whose renewal and void behaviour can be scripted."""

    def __init__(self, reauth="ok", fresh="ok", void="ok"):
        super().__init__()
        self.reauth, self.fresh, self.void = reauth, fresh, void
        self.voided, self.order_attempts = [], []

    async def reauthorize_authorization(self, agreement_id, authorization_id, amount_micros):
        if self.reauth != "ok":
            raise httpx.ConnectError("reauthorize unavailable")
        return await super().reauthorize_authorization(agreement_id, authorization_id, amount_micros)

    async def create_escrow_authorization_order(self, agreement_id, amount_micros, description,
                                                enable_vault=True, vault_id=None, attempt=1):
        order = await super().create_escrow_authorization_order(
            agreement_id, amount_micros, description, enable_vault, vault_id, attempt)
        if attempt > 1:
            self.order_attempts.append(attempt)
            if self.fresh == "error":
                raise httpx.ConnectError("PayPal unreachable")
            if self.fresh == "needs_approval":  # what real PayPal returns: an order, but no authorization yet
                order["status"] = "PAYER_ACTION_REQUIRED"
                order["purchase_units"][0]["payments"] = {}
        return order

    async def void_authorization(self, agreement_id, authorization_id):
        if self.void != "ok":
            raise httpx.ConnectError("PayPal unreachable")
        self.voided.append(authorization_id)
        return await super().void_authorization(agreement_id, authorization_id)


async def aged_contract(orch, vaulted, hours=71):
    if vaulted:
        register_vault("v1", "did:x", repo=orch.repo)
    c = await orch.create_contract(payload(test_suite=SUITE, vault_id="v1" if vaulted else None))
    rec = orch.get(c.agreement_id)
    rec.authorized_at = NOW - timedelta(hours=hours)
    orch.repo.save_contract(rec)
    return c.agreement_id


def make(engine, **kw):
    orch = make_orch(engine=engine)
    sweeper = ReauthorizationSweeper(orch.repo, orch.ledger, engine, clock=lambda: NOW, lock=orch.lock, **kw)
    return orch, sweeper


def snapshot(orch, aid):
    c = orch.get(aid)
    return (c.fsm_state, c.paypal_authorization_id, c.authorized_at, c.created_at, orch.ledger.get_all_balances())


# ------------------------------------------------------------------------------------ re-authorization
async def test_no_authorization_on_the_fresh_order_keeps_the_old_hold_untouched():
    engine = SweepEngine(reauth="fail", fresh="needs_approval")
    orch, sweeper = make(engine)
    aid = await aged_contract(orch, vaulted=True)
    before = snapshot(orch, aid)
    res = await sweeper.sweep(now=NOW)
    assert res["failed_agreements"] == [aid] and res["refreshed_count"] == 0
    assert snapshot(orch, aid) == before
    assert engine.voided == []  # the real hold was not voided
    assert "AUTH-RENEW" not in str(orch.get(aid).paypal_authorization_id)


async def test_unreachable_paypal_keeps_the_old_hold_untouched():
    engine = SweepEngine(reauth="fail", fresh="error")
    orch, sweeper = make(engine)
    aid = await aged_contract(orch, vaulted=True)
    before = snapshot(orch, aid)
    res = await sweeper.sweep(now=NOW)
    assert res["failed_agreements"] == [aid]
    assert snapshot(orch, aid) == before and engine.voided == []


async def test_reauthorization_success_updates_the_hold_and_keeps_creation_history():
    engine = SweepEngine()
    orch, sweeper = make(engine)
    aid = await aged_contract(orch, vaulted=True)
    created_before = orch.get(aid).created_at
    old_auth = orch.get(aid).paypal_authorization_id
    res = await sweeper.sweep(now=NOW)
    assert res["refreshed_agreements"] == [aid] and res["failed_count"] == 0
    c = orch.get(aid)
    assert c.paypal_authorization_id != old_auth and c.paypal_authorization_id.startswith("MOCKREAUTH-")
    assert c.authorized_at == NOW and c.created_at == created_before
    assert c.fsm_state == FsmState.PAYPAL_AUTH_LOCKED
    assert orch.ledger.verify_balance_conservation()


async def test_fresh_order_replaces_the_hold_and_only_then_voids_the_old_one():
    engine = SweepEngine(reauth="fail")
    orch, sweeper = make(engine)
    aid = await aged_contract(orch, vaulted=True)
    old_auth = orch.get(aid).paypal_authorization_id
    res = await sweeper.sweep(now=NOW)
    new_auth = orch.get(aid).paypal_authorization_id
    assert res["refreshed_agreements"] == [aid]
    assert new_auth != old_auth and extract_authorization_id({"purchase_units": [{"payments": {
        "authorizations": [{"id": new_auth}]}}]}) == new_auth
    assert engine.voided == [old_auth]


async def test_refreshed_hold_is_not_swept_again_and_later_renewals_use_new_request_ids():
    engine = SweepEngine(reauth="fail")
    orch, sweeper = make(engine)
    aid = await aged_contract(orch, vaulted=True)
    created_before = orch.get(aid).created_at
    await sweeper.sweep(now=NOW)
    again = await sweeper.sweep(now=NOW + timedelta(minutes=5))  # hold is fresh: nothing to do
    assert again["scanned_count"] == 0

    await sweeper.sweep(now=NOW + timedelta(hours=71))
    await sweeper.sweep(now=NOW + timedelta(hours=142))
    assert engine.order_attempts == [2, 3, 4]  # never the original key, never a repeat
    assert orch.get(aid).created_at == created_before


# -------------------------------------------------------------------------------------------- void
async def test_failed_void_does_not_mark_the_contract_refunded():
    engine = SweepEngine(void="fail")
    orch, sweeper = make(engine)
    aid = await aged_contract(orch, vaulted=False)
    before = snapshot(orch, aid)
    res = await sweeper.sweep(now=NOW)
    assert res["failed_agreements"] == [aid] and res["voided_count"] == 0
    assert snapshot(orch, aid) == before
    assert orch.ledger.get_all_balances()["paypal_escrow_hold"] == BUDGET


async def test_failed_void_is_retried_by_the_next_sweep():
    engine = SweepEngine(void="fail")
    orch, sweeper = make(engine)
    aid = await aged_contract(orch, vaulted=False)
    await sweeper.sweep(now=NOW)
    engine.void = "ok"
    res = await sweeper.sweep(now=NOW)
    assert res["voided_agreements"] == [aid]
    assert orch.get(aid).fsm_state == FsmState.ESCROW_VOIDED_REFUNDED
    bal = orch.ledger.get_all_balances()
    assert (bal["buyer_vault"], bal["paypal_escrow_hold"]) == (BUDGET, 0)


async def test_hold_younger_than_the_threshold_is_left_alone():
    orch, sweeper = make(SweepEngine())
    aid = await aged_contract(orch, vaulted=False, hours=10)
    res = await sweeper.sweep(now=NOW)
    assert res["scanned_count"] == 0 and orch.get(aid).fsm_state == FsmState.PAYPAL_AUTH_LOCKED


# ------------------------------------------------------------------------------- locking and status
async def test_sweep_waits_for_the_orchestrator_lock():
    orch, sweeper = make(SweepEngine())
    aid = await aged_contract(orch, vaulted=False)
    async with orch.lock:
        task = asyncio.create_task(sweeper.sweep(now=NOW))
        await asyncio.sleep(0.05)
        assert not task.done()
        assert orch.get(aid).fsm_state == FsmState.PAYPAL_AUTH_LOCKED  # not voided mid-flow
    res = await asyncio.wait_for(task, timeout=5)
    assert res["voided_agreements"] == [aid]


async def test_status_lists_settlements_stranded_in_a_pending_state():
    orch, sweeper = make(SweepEngine())
    aid = await aged_contract(orch, vaulted=False, hours=1)
    rec = orch.get(aid)
    rec.fsm_state = FsmState.FULL_CAPTURE_PENDING
    orch.repo.save_contract(rec)
    status = sweeper.get_status(now=NOW)
    assert status["stranded_settlements"] == [aid] and status["stranded_settlement_count"] == 1
    assert status["active_authorizations_count"] == 0
