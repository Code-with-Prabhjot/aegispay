"""
Escrow orchestrator: agreement -> escrow lock -> arbitration -> capture/void -> payout -> ledger.

- Composes PayPalEscrowEngine (or MockPayPalEngine), ArbitrationArbiter, DoubleEntryLedgerEngine.
- Enforces the FsmState transition table. Illegal moves raise IllegalTransitionError.
- State is held in memory only; persistence is intentionally left to the DB layer
  (see `on_transition` hook to plug a repository in later).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Protocol

from backend.app.schemas import (
    ArbitrationReport,
    FsmState,
    LedgerAccount,
    SettlementDecision,
    SlaContractCreate,
    SlaContractResponse,
)
from backend.app.services.arbitration_engine import ArbitrationArbiter
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.paypal_escrow import derive_idempotency_key

S = FsmState

ALLOWED_TRANSITIONS: Dict[FsmState, set] = {
    S.AGREEMENT_DRAFTED: {S.PAYPAL_AUTH_LOCKED},
    S.PAYPAL_AUTH_LOCKED: {S.SANDBOX_EXECUTING, S.AUTH_EXPIRED, S.ESCROW_VOIDED_REFUNDED},
    S.SANDBOX_EXECUTING: {S.ARBITRATION_AUDITING},
    S.ARBITRATION_AUDITING: {S.FULL_CAPTURE_PENDING, S.PARTIAL_CAPTURE_PENDING, S.VOID_PENDING},
    S.FULL_CAPTURE_PENDING: {S.PAYPAL_CAPTURED, S.CAPTURE_DECLINED},
    S.PARTIAL_CAPTURE_PENDING: {S.PARTIAL_CAPTURED, S.CAPTURE_DECLINED},
    S.VOID_PENDING: {S.ESCROW_VOIDED_REFUNDED},
    S.PAYPAL_CAPTURED: {S.PAYOUT_DISPATCHED, S.PAYOUT_FAILED_REVERSED},
    S.PARTIAL_CAPTURED: {S.PAYOUT_DISPATCHED, S.PAYOUT_FAILED_REVERSED},
    S.PAYOUT_DISPATCHED: {S.SETTLED_COMPLETE},
    # terminal states
    S.AUTH_EXPIRED: set(),
    S.CAPTURE_DECLINED: set(),
    S.ESCROW_VOIDED_REFUNDED: set(),
    S.SETTLED_COMPLETE: set(),
    S.PAYOUT_FAILED_REVERSED: set(),
}

MIN_PAYOUT_MICROS = 10_000  # matches PayPalEscrowEngine (< 1 cent is skipped)


class OrchestratorError(Exception):
    pass


class IllegalTransitionError(OrchestratorError):
    pass


class UnknownAgreementError(OrchestratorError):
    pass


class PayPalGateway(Protocol):
    """Subset of PayPalEscrowEngine used here. The real engine satisfies it as-is."""

    async def create_escrow_authorization_order(
        self, agreement_id: str, amount_micros: int, description: str, enable_vault: bool = True
    ) -> Dict[str, Any]: ...

    async def capture_authorization(
        self, agreement_id: str, authorization_id: str, capture_micros: int, is_final_capture: bool = True
    ) -> Dict[str, Any]: ...

    async def void_authorization(self, agreement_id: str, authorization_id: str) -> Dict[str, Any]: ...

    async def dispatch_worker_payout(
        self, agreement_id: str, worker_paypal_email: str, net_payout_micros: int, verification_score: float
    ) -> Dict[str, Any]: ...


def extract_authorization_id(order: Dict[str, Any]) -> Optional[str]:
    """Reads the authorization id from a PayPal order JSON (after buyer approval)."""
    try:
        return order["purchase_units"][0]["payments"]["authorizations"][0]["id"]
    except (KeyError, IndexError, TypeError):
        return None


class MockPayPalEngine:
    """Offline stand-in for PayPalEscrowEngine. No keys, no network. Failure switches for tests."""

    def __init__(self, decline_capture: bool = False, fail_payout: bool = False, skip_authorization: bool = False):
        self.decline_capture = decline_capture
        self.fail_payout = fail_payout
        self.skip_authorization = skip_authorization
        self.calls: list = []

    async def create_escrow_authorization_order(self, agreement_id, amount_micros, description, enable_vault=True):
        self.calls.append(("order", agreement_id, amount_micros))
        order: Dict[str, Any] = {"id": f"MOCK-ORDER-{agreement_id[:8]}", "status": "COMPLETED"}
        if not self.skip_authorization:
            order["purchase_units"] = [
                {"payments": {"authorizations": [{"id": f"MOCK-AUTH-{agreement_id[:8]}", "status": "CREATED"}]}}
            ]
        return order

    async def capture_authorization(self, agreement_id, authorization_id, capture_micros, is_final_capture=True):
        self.calls.append(("capture", agreement_id, capture_micros))
        if self.decline_capture:
            return {"fsm_transition": "CAPTURE_DECLINED", "ok": False, "paypal_status": "DECLINED"}
        return {
            "fsm_transition": "PAYPAL_CAPTURED",
            "ok": True,
            "paypal_status": "COMPLETED",
            "capture_id": f"MOCK-CAP-{agreement_id[:8]}",
        }

    async def void_authorization(self, agreement_id, authorization_id):
        self.calls.append(("void", agreement_id))
        return {
            "fsm_transition": "ESCROW_VOIDED_REFUNDED",
            "ok": True,
            "authorization_id": authorization_id,
            "paypal_status": "VOIDED",
        }

    async def dispatch_worker_payout(self, agreement_id, worker_paypal_email, net_payout_micros, verification_score):
        self.calls.append(("payout", agreement_id, net_payout_micros))
        if net_payout_micros < MIN_PAYOUT_MICROS:
            return {"status": "SKIPPED_BELOW_ONE_CENT", "ok": False}
        if self.fail_payout:
            return {"status": "FAILED", "ok": False}
        return {"ok": True, "payout_response": {"batch_header": {"payout_batch_id": f"MOCK-BATCH-{agreement_id[:8]}"}}}


class EscrowOrchestrator:
    def __init__(
        self,
        paypal: PayPalGateway,
        arbiter: Optional[ArbitrationArbiter] = None,
        ledger: Optional[DoubleEntryLedgerEngine] = None,
        platform_fee_bps: int = 500,
        paypal_payout_fee_micros: int = 250_000,
        on_transition: Optional[Callable[[SlaContractResponse, FsmState, FsmState], None]] = None,
    ):
        self.paypal = paypal
        self.arbiter = arbiter or ArbitrationArbiter()
        self.ledger = ledger or DoubleEntryLedgerEngine()
        self.platform_fee_bps = platform_fee_bps
        self.paypal_payout_fee_micros = paypal_payout_fee_micros
        self.on_transition = on_transition  # hook for the DB teammate; no persistence here
        self._contracts: Dict[str, SlaContractResponse] = {}
        self._reports: Dict[str, ArbitrationReport] = {}

    # ---------- state helpers ----------
    def get(self, agreement_id: str) -> SlaContractResponse:
        try:
            return self._contracts[agreement_id]
        except KeyError:
            raise UnknownAgreementError(agreement_id)

    def report(self, agreement_id: str) -> Optional[ArbitrationReport]:
        return self._reports.get(agreement_id)

    def _move(self, c: SlaContractResponse, new: FsmState) -> None:
        old = c.fsm_state
        if new not in ALLOWED_TRANSITIONS[old]:
            raise IllegalTransitionError(f"{old.value} -> {new.value} not allowed")
        c.fsm_state = new
        c.updated_at = datetime.now(timezone.utc)
        if self.on_transition:
            self.on_transition(c, old, new)

    # ---------- steps ----------
    def create_agreement(self, req: SlaContractCreate) -> SlaContractResponse:
        agreement_id = req.agreement_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        c = SlaContractResponse(
            agreement_id=agreement_id,
            buyer_agent_id=req.buyer_agent_id,
            worker_agent_id=req.worker_agent_id,
            worker_paypal_email=req.worker_paypal_email,
            budget_micros=req.budget_micros,
            task_description=req.task_description,
            timeout_seconds=req.timeout_seconds,
            fsm_state=S.AGREEMENT_DRAFTED,
            created_at=now,
            updated_at=now,
        )
        self._contracts[agreement_id] = c
        return c

    async def lock_escrow(self, agreement_id: str, fund_buyer_vault: bool = False) -> SlaContractResponse:
        """AGREEMENT_DRAFTED -> PAYPAL_AUTH_LOCKED. Places the PayPal hold and mirrors it in the ledger."""
        c = self.get(agreement_id)
        if c.fsm_state != S.AGREEMENT_DRAFTED:
            raise IllegalTransitionError(f"lock_escrow requires AGREEMENT_DRAFTED, got {c.fsm_state.value}")
        if fund_buyer_vault:  # demo/test helper: simulate buyer top-up
            self.ledger.deposit(LedgerAccount.BUYER_VAULT.value, c.budget_micros, agreement_id, "buyer funding")
        order = await self.paypal.create_escrow_authorization_order(
            agreement_id, c.budget_micros, c.task_description
        )
        auth_id = extract_authorization_id(order)
        if not auth_id:
            raise OrchestratorError("PayPal order has no authorization yet (buyer approval pending)")
        self.ledger.record_transfer(
            LedgerAccount.PAYPAL_ESCROW_HOLD.value, LedgerAccount.BUYER_VAULT.value,
            c.budget_micros, agreement_id, "escrow hold placed",
        )
        c.paypal_order_id = order.get("id")
        c.paypal_authorization_id = auth_id
        self._move(c, S.PAYPAL_AUTH_LOCKED)
        return c

    async def expire_authorization(self, agreement_id: str) -> SlaContractResponse:
        """PAYPAL_AUTH_LOCKED -> AUTH_EXPIRED. Hold lapsed; return funds to the buyer vault."""
        c = self.get(agreement_id)
        self._move(c, S.AUTH_EXPIRED)
        self.ledger.record_transfer(
            LedgerAccount.BUYER_VAULT.value, LedgerAccount.PAYPAL_ESCROW_HOLD.value,
            c.budget_micros, agreement_id, "authorization expired",
        )
        return c

    async def run_job(self, agreement_id: str, worker_code: str, buyer_test_code: str,
                      semantic_score: Optional[float] = None) -> SlaContractResponse:
        """Full pipeline from PAYPAL_AUTH_LOCKED to a terminal state."""
        c = self.get(agreement_id)
        self._move(c, S.SANDBOX_EXECUTING)
        report = await asyncio.to_thread(
            self.arbiter.evaluate, agreement_id, worker_code, buyer_test_code,
            c.budget_micros, semantic_score, self.platform_fee_bps, self.paypal_payout_fee_micros,
        )
        self._reports[agreement_id] = report
        self._move(c, S.ARBITRATION_AUDITING)
        c.verification_score = report.composite_score
        return await self._settle(c, report)

    async def _settle(self, c: SlaContractResponse, report: ArbitrationReport) -> SlaContractResponse:
        split = report.settlement
        aid = c.agreement_id
        decision = SettlementDecision(split.decision)

        if decision == SettlementDecision.VOID_AUTHORIZATION:
            self._move(c, S.VOID_PENDING)
            res = await self.paypal.void_authorization(aid, c.paypal_authorization_id)
            if not res.get("ok"):
                raise OrchestratorError(f"void failed: {res}")
            self.ledger.record_settlement(aid, split)
            self._move(c, S.ESCROW_VOIDED_REFUNDED)
            return c

        full = decision == SettlementDecision.FULL_CAPTURE
        self._move(c, S.FULL_CAPTURE_PENDING if full else S.PARTIAL_CAPTURE_PENDING)
        res = await self.paypal.capture_authorization(
            aid, c.paypal_authorization_id, split.capture_micros, is_final_capture=True
        )
        if not res.get("ok"):
            # Nothing captured: release the whole hold, no payout.
            self._move(c, S.CAPTURE_DECLINED)
            self.ledger.record_transfer(
                LedgerAccount.BUYER_VAULT.value, LedgerAccount.PAYPAL_ESCROW_HOLD.value,
                c.budget_micros, aid, "capture declined: hold released",
            )
            return c

        c.paypal_capture_id = res.get("capture_id")
        self._move(c, S.PAYPAL_CAPTURED if full else S.PARTIAL_CAPTURED)
        self.ledger.record_settlement(aid, split)

        net = split.worker_net_payout_micros
        if net < MIN_PAYOUT_MICROS:
            # Dust: nothing to send, treat as settled.
            self._move(c, S.PAYOUT_DISPATCHED)
            self._move(c, S.SETTLED_COMPLETE)
            return c

        pay = await self.paypal.dispatch_worker_payout(
            aid, c.worker_paypal_email, net, report.composite_score
        )
        if not pay.get("ok"):
            self._move(c, S.PAYOUT_FAILED_REVERSED)
            self.ledger.record_transfer(
                LedgerAccount.BUYER_VAULT.value, LedgerAccount.WORKER_PAYABLE.value,
                net, aid, "payout failed: reversed to buyer",
            )
            return c

        c.paypal_payout_batch_id = (
            pay.get("payout_response", {}).get("batch_header", {}).get("payout_batch_id")
        )
        self._move(c, S.PAYOUT_DISPATCHED)
        self._move(c, S.SETTLED_COMPLETE)
        return c
