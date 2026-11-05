"""Vault daily-budget accounting counts open holds, and the vault payload sent to PayPal is well-formed."""

import json

import httpx
import pytest

from backend.app.schemas import FsmState
from backend.app.services.orchestrator import PolicyViolationError
from backend.app.services.paypal_escrow import PayPalEscrowEngine
from backend.app.services.paypal_mock import MockPayPalEscrowEngine
from backend.app.services.vault_service import (
    VaultCircuitBreakerError,
    register_vault,
    release_vault_funds,
    reserve_vault_funds,
)
from tests.test_settlement_recovery import FlakyEngine
from tests.test_trust_policy import SUITE, RecordingArbiter, make_orch, payload

TASK = 10_000_000
DAILY = 20_000_000


def vault(orch, **over):
    base = dict(vault_id="v1", buyer_did="did:x", max_amount_per_task_micros=TASK,
                max_daily_budget_micros=DAILY, window_reset_timestamp=0)
    base.update(over)
    return register_vault(repo=orch.repo, **base)


def spent(orch, vault_id="v1"):
    return orch.repo.get_vault(vault_id).current_daily_spent_micros


def new_contract(orch, budget=TASK):
    return orch.create_contract(payload(test_suite=SUITE, vault_id="v1", budget_micros=budget))


# --------------------------------------------------------------------------------- reservation math
def test_reserve_and_release_adjust_the_spend_and_never_go_negative():
    orch = make_orch()
    vault(orch)
    reserve_vault_funds("v1", 7_000_000, repo=orch.repo, now_epoch=1_000)
    assert spent(orch) == 7_000_000
    release_vault_funds("v1", 3_000_000, repo=orch.repo)
    assert spent(orch) == 4_000_000
    release_vault_funds("v1", 99_000_000, repo=orch.repo)
    assert spent(orch) == 0


def test_reserve_starts_a_fresh_window_when_the_old_one_ended():
    orch = make_orch()
    vault(orch, current_daily_spent_micros=15_000_000, window_reset_timestamp=500)
    reserve_vault_funds("v1", 2_000_000, repo=orch.repo, now_epoch=1_000)
    v = orch.repo.get_vault("v1")
    assert v.current_daily_spent_micros == 2_000_000 and v.window_reset_timestamp == 1_000 + 86400


def test_unknown_vault_is_a_no_op():
    orch = make_orch()
    reserve_vault_funds("ghost", 1, repo=orch.repo)
    release_vault_funds("ghost", 1, repo=orch.repo)


# ------------------------------------------------------------ the daily limit sees open holds
async def test_open_holds_count_against_the_daily_budget():
    orch = make_orch()
    vault(orch)
    await new_contract(orch)
    await new_contract(orch)
    assert spent(orch) == DAILY
    with pytest.raises(VaultCircuitBreakerError, match="EXCEEDS_DAILY_BUDGET"):
        await new_contract(orch)
    assert len(orch.repo.list_contracts()) == 2  # the rejected one left nothing behind


async def test_failed_hold_gives_the_reservation_back():
    orch = make_orch(engine=FlakyEngine())
    vault(orch)
    original = orch.engine.create_escrow_authorization_order

    async def down(*a, **k):
        raise httpx.ConnectError("PayPal unreachable")

    orch.engine.create_escrow_authorization_order = down
    with pytest.raises(Exception):
        await new_contract(orch)
    assert spent(orch) == 0
    orch.engine.create_escrow_authorization_order = original
    await new_contract(orch)
    assert spent(orch) == TASK


async def test_capture_replaces_the_reservation_with_the_captured_amount():
    orch = make_orch(arbiter=RecordingArbiter(score=0.75))
    vault(orch)
    c = await new_contract(orch)
    assert spent(orch) == TASK
    await orch.submit_work(c.agreement_id, "def f(): ...")
    assert spent(orch) == 7_000_000  # score 0.75 captures 70% of the $10 hold, not the $10 reserved
    assert orch.repo.get_vault("v1").consecutive_void_count == 0


async def test_worker_fault_void_frees_the_budget_and_counts_a_strike():
    orch = make_orch(arbiter=RecordingArbiter(score=0.1))
    vault(orch)
    c = await new_contract(orch)
    await orch.submit_work(c.agreement_id, "def f(): ...")
    assert orch.get(c.agreement_id).fsm_state == FsmState.ESCROW_VOIDED_REFUNDED
    v = orch.repo.get_vault("v1")
    assert (v.current_daily_spent_micros, v.consecutive_void_count) == (0, 1)


async def test_expired_hold_frees_the_budget_without_a_strike():
    orch = make_orch()
    vault(orch)
    c = await new_contract(orch)
    rec = orch.get(c.agreement_id)
    from datetime import timedelta
    orch.clock = lambda: rec.authorized_at + timedelta(hours=73)
    await orch.check_expiry(c.agreement_id)
    v = orch.repo.get_vault("v1")
    assert orch.get(c.agreement_id).fsm_state == FsmState.AUTH_EXPIRED
    assert (v.current_daily_spent_micros, v.consecutive_void_count) == (0, 0)


# --------------------------------------------------------------- webhook recovery books the same way
async def test_webhook_recovered_capture_books_vault_spend():
    from tests.test_webhooks import capture_completed
    engine = FlakyEngine(fail_capture=1)
    orch = make_orch(arbiter=RecordingArbiter(score=0.95), engine=engine)
    vault(orch)
    c = await new_contract(orch)
    with pytest.raises(Exception):
        await orch.submit_work(c.agreement_id, "def f(): ...")  # capture response "lost"
    rec = orch.get(c.agreement_id)
    event = capture_completed(value="10.00", auth_id=rec.paypal_authorization_id)
    event["resource"]["custom_id"] = c.agreement_id
    out = await orch.handle_webhook(event)
    assert out["status"] == "processed"
    assert spent(orch) == TASK


async def test_webhook_recovered_void_after_arbiter_failure_is_not_a_strike():
    from tests.test_webhooks import authorization_voided
    engine = FlakyEngine(fail_void=1)

    class Crashing(RecordingArbiter):
        def evaluate(self, *a, **k):
            raise RuntimeError("boom")

    orch = make_orch(arbiter=Crashing(), engine=engine)
    vault(orch)
    c = await new_contract(orch)
    with pytest.raises(Exception):
        await orch.submit_work(c.agreement_id, "def f(): ...")  # void response "lost"
    rec = orch.get(c.agreement_id)
    event = authorization_voided(auth_id=rec.paypal_authorization_id)
    event["resource"]["custom_id"] = c.agreement_id
    assert (await orch.handle_webhook(event))["status"] == "processed"
    v = orch.repo.get_vault("v1")
    assert (v.current_daily_spent_micros, v.consecutive_void_count) == (0, 0)


# ------------------------------------------------------------------------ the PayPal payload shape
def recorded_order_body(**kw):
    seen = []

    def handler(request: httpx.Request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
        seen.append(json.loads(request.content))
        return httpx.Response(201, json={"id": "O1", "status": "CREATED"})

    import asyncio
    engine = PayPalEscrowEngine("id", "secret", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asyncio.run(engine.create_escrow_authorization_order("agr", 5_000_000, "d", **kw))
    return seen[0]


def test_vaulted_order_pays_with_the_vaulted_paypal_account():
    body = recorded_order_body(vault_id="VAULT-1")
    assert body["payment_source"] == {"paypal": {"vault_id": "VAULT-1"}}
    assert "vault_id" not in body["payment_source"]
    assert body["intent"] == "AUTHORIZE"


def test_unvaulted_order_still_asks_paypal_to_vault_on_success():
    body = recorded_order_body()
    assert body["payment_source"]["paypal"]["attributes"]["vault"]["store_in_vault"] == "ON_SUCCESS"


def test_mock_order_uses_the_same_shape():
    import asyncio
    order = asyncio.run(MockPayPalEscrowEngine().create_escrow_authorization_order("agr", 1_000_000, "d", vault_id="V"))
    assert order["payment_source"] == {"paypal": {"vault_id": "V"}}
    assert "vault_id" not in order
