"""
tests/test_settlement_math.py
Verification Test Suite for AegisPay Phase 1 Backend Foundation & Verified Settlement Engine.

Covers:
1. Perfect execution (S = 0.95): 100% capture, 5% fee, $0.25 rail fee, net worker payout.
2. Partial capture (S = 0.75): exact 70% linear factor, micro-dollar cent floor, buyer hold release.
3. Lower partial boundary (S = 0.60): exact 50% capture floor.
4. Breach / failure (S = 0.45): $0.00 capture, 100% buyer release, VOID_AUTHORIZATION.
5. Security violation override (S = 0.98, ast_security_passed=False): immediate 100% void.
6. Double-entry ledger balance conservation: sum(Debits) == sum(Credits) across all scenarios.
7. Idempotency key determinism: identical inputs produce identical UUIDv5 keys.
8. Cent floor rounding and negative protection in micros_to_usd_str.
9. PayPalEscrowEngine HTTP 422 / DECLINED non-crashing failure handling.
"""

import uuid
import pytest
import httpx

from backend.app.schemas import (
    DoubleEntryLedgerEntry,
    FsmState,
    LedgerAccount,
    SettlementDecision,
    SettlementSplitResult,
)
from backend.app.services.paypal_escrow import (
    AEGIS_NAMESPACE,
    compute_settlement_split,
    compute_settlement_split_micros,
    derive_idempotency_key,
    micros_to_usd_str,
    PayPalEscrowEngine,
)
from backend.app.services.ledger_service import (
    DoubleEntryLedgerEngine,
    NegativeBalanceError,
    UnbalancedLedgerError,
)

BUDGET_15_USD_MICROS = 15_000_000  # $15.00 in micro-dollars


# ==============================================================================
# 1. PERFECT EXECUTION TEST (S = 0.95)
# ==============================================================================

def test_perfect_execution_settlement():
    """
    S = 0.95 (>= 0.90 threshold):
    - 100% of budget captured ($15.00 = 15,000,000 micros)
    - $0.00 released to buyer
    - 5.00% protocol fee ($0.75 = 750,000 micros)
    - $0.25 PayPal payout rail fee (250,000 micros)
    - Net worker payout: $14.00 (14,000,000 micros)
    """
    res = compute_settlement_split_micros(
        agreed_budget_micros=BUDGET_15_USD_MICROS,
        composite_score=0.95,
        ast_security_passed=True,
        platform_fee_bps=500,
        paypal_payout_fee_micros=250_000,
    )

    assert res["decision"] == "FULL_CAPTURE"
    assert res["capture_micros"] == 15_000_000
    assert res["buyer_released_micros"] == 0
    assert res["platform_fee_micros"] == 750_000
    assert res["paypal_rail_fee_micros"] == 250_000
    assert res["worker_net_payout_micros"] == 14_000_000

    # Verify sum of disbursements equals captured amount
    assert (
        res["worker_net_payout_micros"]
        + res["platform_fee_micros"]
        + res["paypal_rail_fee_micros"]
        == res["capture_micros"]
    )

    # Verify Pydantic schema wrapper and USD formatting
    split_obj = compute_settlement_split(BUDGET_15_USD_MICROS, 0.95)
    assert split_obj.capture_usd == "15.00"
    assert split_obj.buyer_released_usd == "0.00"
    assert split_obj.platform_fee_usd == "0.75"
    assert split_obj.paypal_rail_fee_usd == "0.25"
    assert split_obj.worker_net_payout_usd == "14.00"


# ==============================================================================
# 2. PARTIAL CAPTURE TEST (S = 0.75)
# ==============================================================================

def test_partial_capture_settlement():
    """
    S = 0.75 (midpoint of [0.60, 0.90)):
    - Normalized = (0.75 - 0.60) / 0.30 = 0.50
    - Linear Factor = 0.50 + 0.40 * 0.50 = 0.70 (exact 70%)
    - Captured: 70% of $15.00 = $10.50 (10,500,000 micros)
    - Released to buyer: $4.50 (4,500,000 micros)
    - 5% platform fee on $10.50: (10,500,000 * 500) // 10,000 = 525,000 micros.
      Floored to nearest whole cent (10,000 micros): $0.52 (520,000 micros).
    - $0.25 PayPal payout rail fee (250,000 micros)
    - Net worker payout: 10,500,000 - 520,000 - 250,000 = 9,730,000 micros ($9.73).
    """
    res = compute_settlement_split_micros(
        agreed_budget_micros=BUDGET_15_USD_MICROS,
        composite_score=0.75,
        ast_security_passed=True,
        platform_fee_bps=500,
        paypal_payout_fee_micros=250_000,
    )

    assert res["decision"] == "PARTIAL_CAPTURE"
    assert res["capture_micros"] == 10_500_000
    assert res["buyer_released_micros"] == 4_500_000
    assert res["platform_fee_micros"] == 520_000
    assert res["paypal_rail_fee_micros"] == 250_000
    assert res["worker_net_payout_micros"] == 9_730_000

    # Total budget must be strictly conserved: captured + released == agreed budget
    assert res["capture_micros"] + res["buyer_released_micros"] == BUDGET_15_USD_MICROS

    split_obj = compute_settlement_split(BUDGET_15_USD_MICROS, 0.75)
    assert split_obj.capture_usd == "10.50"
    assert split_obj.buyer_released_usd == "4.50"
    assert split_obj.platform_fee_usd == "0.52"
    assert split_obj.paypal_rail_fee_usd == "0.25"
    assert split_obj.worker_net_payout_usd == "9.73"


# ==============================================================================
# 3. LOWER PARTIAL BOUNDARY TEST (S = 0.60)
# ==============================================================================

def test_lower_partial_boundary_settlement():
    """
    S = 0.60 (exact lower threshold for partial capture):
    - Normalized = (0.60 - 0.60) / 0.30 = 0.00
    - Linear Factor = 0.50 (exact 50% capture floor)
    - Captured: 50% of $15.00 = $7.50 (7,500,000 micros)
    - Released to buyer: $7.50 (7,500,000 micros)
    - Platform fee: 5% of $7.50 = 375,000 micros -> cent floor: $0.37 (370,000 micros)
    - Rail fee: $0.25 (250,000 micros)
    - Net worker payout: 7,500,000 - 370,000 - 250,000 = 6,880,000 micros ($6.88)
    """
    res = compute_settlement_split_micros(
        agreed_budget_micros=BUDGET_15_USD_MICROS,
        composite_score=0.60,
        ast_security_passed=True,
    )

    assert res["decision"] == "PARTIAL_CAPTURE"
    assert res["capture_micros"] == 7_500_000
    assert res["buyer_released_micros"] == 7_500_000
    assert res["platform_fee_micros"] == 370_000
    assert res["paypal_rail_fee_micros"] == 250_000
    assert res["worker_net_payout_micros"] == 6_880_000

    assert res["capture_micros"] + res["buyer_released_micros"] == BUDGET_15_USD_MICROS


# ==============================================================================
# 4. BREACH / SLA FAILURE TEST (S = 0.45)
# ==============================================================================

def test_breach_failure_settlement():
    """
    S = 0.45 (< 0.60 threshold):
    - Complete breach: Decision = VOID_AUTHORIZATION
    - Captured: $0.00 (0 micros)
    - 100% released back to buyer: $15.00 (15,000,000 micros)
    - Fees and worker payout all zeroed out
    """
    res = compute_settlement_split_micros(
        agreed_budget_micros=BUDGET_15_USD_MICROS,
        composite_score=0.45,
        ast_security_passed=True,
    )

    assert res["decision"] == "VOID_AUTHORIZATION"
    assert res["capture_micros"] == 0
    assert res["buyer_released_micros"] == BUDGET_15_USD_MICROS
    assert res["platform_fee_micros"] == 0
    assert res["paypal_rail_fee_micros"] == 0
    assert res["worker_net_payout_micros"] == 0


# ==============================================================================
# 5. SECURITY VIOLATION OVERRIDE TEST (S = 0.98, AST Failed)
# ==============================================================================

def test_security_violation_override():
    """
    Hard Veto Rule: Even with high score S = 0.98, if ast_security_passed is False,
    the settlement MUST immediately force a 100% VOID_AUTHORIZATION.
    Zero captured, $15.00 released to buyer.
    """
    res = compute_settlement_split_micros(
        agreed_budget_micros=BUDGET_15_USD_MICROS,
        composite_score=0.98,
        ast_security_passed=False,
    )

    assert res["decision"] == "VOID_AUTHORIZATION"
    assert res["capture_micros"] == 0
    assert res["buyer_released_micros"] == BUDGET_15_USD_MICROS
    assert res["platform_fee_micros"] == 0
    assert res["worker_net_payout_micros"] == 0


# ==============================================================================
# 6. DOUBLE-ENTRY LEDGER BALANCE CONSERVATION ACROSS SCENARIOS
# ==============================================================================

def test_double_entry_ledger_perfect_settlement():
    """
    Verifies double-entry accounting for Perfect Settlement (S = 0.95):
    - Initial deposit of $15.00 into buyer_vault.
    - Transfer to paypal_escrow_hold.
    - Record settlement entries.
    - Asserts total debits == total credits.
    - Asserts account balances match the exact split.
    """
    ledger = DoubleEntryLedgerEngine({LedgerAccount.BUYER_VAULT.value: BUDGET_15_USD_MICROS})

    # 1. Lock escrow hold
    ledger.record_transfer(
        debit_account=LedgerAccount.PAYPAL_ESCROW_HOLD.value,
        credit_account=LedgerAccount.BUYER_VAULT.value,
        amount_micros=BUDGET_15_USD_MICROS,
        reference_id="agr_perfect_1",
        description="PayPal Orders v2 AUTHORIZE hold",
    )

    assert ledger.get_balance(LedgerAccount.BUYER_VAULT.value) == 0
    assert ledger.get_balance(LedgerAccount.PAYPAL_ESCROW_HOLD.value) == BUDGET_15_USD_MICROS

    # 2. S = 0.95 Settlement
    split = compute_settlement_split(BUDGET_15_USD_MICROS, 0.95)
    ledger.record_settlement("agr_perfect_1", split)

    # Escrow hold should be fully cleared
    assert ledger.get_balance(LedgerAccount.PAYPAL_ESCROW_HOLD.value) == 0
    assert ledger.get_balance(LedgerAccount.BUYER_VAULT.value) == 0
    assert ledger.get_balance(LedgerAccount.PLATFORM_FEE_REVENUE.value) == 750_000
    assert ledger.get_balance(LedgerAccount.PAYPAL_RAIL_EXPENSE.value) == 250_000
    # Net worker payable
    assert ledger.get_balance(LedgerAccount.WORKER_PAYABLE.value) == 14_000_000

    # Conservation invariant: total system balance == $15.00
    balances = ledger.get_all_balances()
    assert sum(balances.values()) == BUDGET_15_USD_MICROS
    assert ledger.verify_balance_conservation() is True


def test_double_entry_ledger_partial_settlement():
    """
    Verifies double-entry accounting for Partial Settlement (S = 0.75):
    - Escrow hold released back to buyer: $4.50
    - Platform fee: $0.52
    - Rail fee: $0.25
    - Worker net payable: $9.73
    """
    ledger = DoubleEntryLedgerEngine({LedgerAccount.BUYER_VAULT.value: BUDGET_15_USD_MICROS})

    ledger.record_transfer(
        debit_account=LedgerAccount.PAYPAL_ESCROW_HOLD.value,
        credit_account=LedgerAccount.BUYER_VAULT.value,
        amount_micros=BUDGET_15_USD_MICROS,
        reference_id="agr_partial_1",
    )

    split = compute_settlement_split(BUDGET_15_USD_MICROS, 0.75)
    ledger.record_settlement("agr_partial_1", split)

    assert ledger.get_balance(LedgerAccount.PAYPAL_ESCROW_HOLD.value) == 0
    assert ledger.get_balance(LedgerAccount.BUYER_VAULT.value) == 4_500_000  # $4.50 returned!
    assert ledger.get_balance(LedgerAccount.PLATFORM_FEE_REVENUE.value) == 520_000  # $0.52 fee
    assert ledger.get_balance(LedgerAccount.PAYPAL_RAIL_EXPENSE.value) == 250_000  # $0.25 rail
    assert ledger.get_balance(LedgerAccount.WORKER_PAYABLE.value) == 9_730_000  # $9.73 net

    assert sum(ledger.get_all_balances().values()) == BUDGET_15_USD_MICROS
    assert ledger.verify_balance_conservation() is True


def test_double_entry_ledger_void_settlement():
    """
    Verifies double-entry accounting for Void Settlement (S = 0.45 or AST breach):
    - 100% of held escrow ($15.00) is returned to buyer_vault.
    - Zero fees, zero payable.
    """
    ledger = DoubleEntryLedgerEngine({LedgerAccount.BUYER_VAULT.value: BUDGET_15_USD_MICROS})

    ledger.record_transfer(
        debit_account=LedgerAccount.PAYPAL_ESCROW_HOLD.value,
        credit_account=LedgerAccount.BUYER_VAULT.value,
        amount_micros=BUDGET_15_USD_MICROS,
        reference_id="agr_void_1",
    )

    split = compute_settlement_split(BUDGET_15_USD_MICROS, 0.45)
    ledger.record_settlement("agr_void_1", split)

    assert ledger.get_balance(LedgerAccount.PAYPAL_ESCROW_HOLD.value) == 0
    assert ledger.get_balance(LedgerAccount.BUYER_VAULT.value) == BUDGET_15_USD_MICROS
    assert ledger.get_balance(LedgerAccount.PLATFORM_FEE_REVENUE.value) == 0
    assert ledger.get_balance(LedgerAccount.PAYPAL_RAIL_EXPENSE.value) == 0
    assert ledger.get_balance(LedgerAccount.WORKER_PAYABLE.value) == 0

    assert sum(ledger.get_all_balances().values()) == BUDGET_15_USD_MICROS
    assert ledger.verify_balance_conservation() is True


def test_ledger_rejects_negative_balance():
    """
    Double-entry accounting must reject any transaction that would overdraw
    an account or create a negative balance.
    """
    ledger = DoubleEntryLedgerEngine({LedgerAccount.BUYER_VAULT.value: 5_000_000})  # Only $5.00

    # Attempt to transfer $15.00 from buyer_vault with only $5.00
    with pytest.raises(NegativeBalanceError) as exc_info:
        ledger.record_transfer(
            debit_account=LedgerAccount.PAYPAL_ESCROW_HOLD.value,
            credit_account=LedgerAccount.BUYER_VAULT.value,
            amount_micros=15_000_000,
            reference_id="agr_overdraw_test",
        )

    assert "negative balance" in str(exc_info.value).lower()
    # Ensure buyer vault was not altered
    assert ledger.get_balance(LedgerAccount.BUYER_VAULT.value) == 5_000_000


def test_ledger_rejects_unbalanced_transaction():
    """
    Verifies that unbalanced entries or direct self-transfers are strictly rejected.
    """
    ledger = DoubleEntryLedgerEngine({LedgerAccount.BUYER_VAULT.value: 10_000_000})

    # Self-transfer is rejected
    with pytest.raises(ValueError):
        ledger.record_transfer(
            debit_account=LedgerAccount.BUYER_VAULT.value,
            credit_account=LedgerAccount.BUYER_VAULT.value,
            amount_micros=1_000_000,
            reference_id="ref_self",
        )


# ==============================================================================
# 7. DETERMINISTIC IDEMPOTENCY KEY DERIVATION (FIX #2)
# ==============================================================================

def test_idempotency_key_determinism():
    """
    Verifies Fix #2:
    - Identical agreement_id and action produce the exact same UUIDv5 string across multiple runs.
    - Retries will never generate a brand-new PayPal-Request-Id.
    - Different inputs produce distinct keys.
    """
    agr_id = "sla_agreement_uuid_998877"
    action = "capture_auth"

    key1 = derive_idempotency_key(agr_id, action)
    key2 = derive_idempotency_key(agr_id, action)
    key3 = derive_idempotency_key(agr_id, action, step=1)

    assert key1 == key2 == key3

    # Valid UUID string format
    parsed_uuid = uuid.UUID(key1)
    assert parsed_uuid.version == 5

    # Different action produces distinct key
    key_void = derive_idempotency_key(agr_id, "void_auth")
    assert key1 != key_void

    # Different agreement produces distinct key
    key_other_agr = derive_idempotency_key("other_agreement", action)
    assert key1 != key_other_agr


# ==============================================================================
# 8. CENT FLOOR USD CONVERSION & INPUT GUARDS
# ==============================================================================

def test_micros_to_usd_str_formatting():
    """Verifies integer micro-dollar to USD string conversion with cent floor rounding."""
    assert micros_to_usd_str(15_000_000) == "15.00"
    assert micros_to_usd_str(10_500_000) == "10.50"
    assert micros_to_usd_str(250_000) == "0.25"
    assert micros_to_usd_str(0) == "0.00"
    # Cent floor: 12,345 micros = 0.012345 USD -> "0.01"
    assert micros_to_usd_str(12_345) == "0.01"
    # Cent floor: 9,999 micros = 0.009999 USD -> "0.00"
    assert micros_to_usd_str(9_999) == "0.00"

    with pytest.raises(ValueError):
        micros_to_usd_str(-100)


# ==============================================================================
# 9. PAYPAL ESCROW ENGINE CAPTURE FAILURE HANDLING (FIX #1)
# ==============================================================================

@pytest.mark.asyncio
async def test_paypal_capture_declined_handling_422():
    """
    Verifies Fix #1:
    When PayPal responds with HTTP 422 (e.g. INSTRUMENT_DECLINED / PAYER_CANNOT_PAY),
    capture_authorization catches the error cleanly and returns CAPTURE_DECLINED
    without throwing an unhandled exception or crashing the settlement engine.
    """
    mock_responses = [
        # 1. OAuth token mock
        httpx.Response(200, json={"access_token": "mock_token_123", "expires_in": 3600}),
        # 2. Capture call returns 422 Unprocessable Entity
        httpx.Response(
            422,
            json={
                "name": "UNPROCESSABLE_ENTITY",
                "message": "The requested action could not be performed, semantically incorrect, or failed business validation.",
                "details": [
                    {
                        "issue": "INSTRUMENT_DECLINED",
                        "description": "The instrument presented was declined.",
                    }
                ],
            },
        ),
    ]

    async def mock_transport(request: httpx.Request) -> httpx.Response:
        return mock_responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_transport))
    engine = PayPalEscrowEngine(
        client_id="mock_id",
        client_secret="mock_secret",
        http_client=client,
    )

    try:
        res = await engine.capture_authorization(
            agreement_id="test_agr_422",
            authorization_id="AUTH-12345",
            capture_micros=15_000_000,
        )

        assert res["ok"] is False
        assert res["fsm_transition"] == FsmState.CAPTURE_DECLINED.value
        assert res["paypal_status"] == "DECLINED"
        assert "error_details" in res
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_paypal_void_authorization_success():
    """
    Verifies void_authorization handles HTTP 204 No Content from PayPal.
    """
    mock_responses = [
        # 1. OAuth token
        httpx.Response(200, json={"access_token": "mock_token_123", "expires_in": 3600}),
        # 2. Void call returns 204 No Content
        httpx.Response(204),
    ]

    async def mock_transport(request: httpx.Request) -> httpx.Response:
        return mock_responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_transport))
    engine = PayPalEscrowEngine(
        client_id="mock_id",
        client_secret="mock_secret",
        http_client=client,
    )

    try:
        res = await engine.void_authorization("test_agr_void", "AUTH-999")
        assert res["ok"] is True
        assert res["fsm_transition"] == FsmState.ESCROW_VOIDED_REFUNDED.value
        assert res["paypal_status"] == "VOIDED"
    finally:
        await engine.close()
