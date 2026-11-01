"""
backend/app/services/orchestrator.py
Glue between the FSM, ledger, PayPal engine, arbiter and persistence.
Every state change goes through fsm.transition() and is persisted with its history.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from backend.app.config import Settings
from backend.app.db import Repository
from backend.app.schemas import (
    ArbitrationReport,
    FsmState,
    LedgerAccount,
    SlaContractCreate,
)
from backend.app.services import fsm
from backend.app.services.arbitration_engine import ArbitrationArbiter
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.scenario_presets import PresetScenario, get_scenario

ESCROW = LedgerAccount.PAYPAL_ESCROW_HOLD.value
BUYER = LedgerAccount.BUYER_VAULT.value
WORKER = LedgerAccount.WORKER_PAYABLE.value


class NotFoundError(Exception):
    pass


class UpstreamError(Exception):
    """PayPal (or mock) call failed."""


def extract_authorization_id(order: Dict[str, Any]) -> Optional[str]:
    try:
        return order["purchase_units"][0]["payments"]["authorizations"][0]["id"]
    except (KeyError, IndexError, TypeError):
        return None


class EscrowOrchestrator:
    def __init__(
        self,
        repo: Repository,
        ledger: DoubleEntryLedgerEngine,
        engine: Any,
        settings: Settings,
        arbiter: Optional[ArbitrationArbiter] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.repo = repo
        self.ledger = ledger
        self.engine = engine
        self.settings = settings
        self.arbiter = arbiter or ArbitrationArbiter()
        self.clock = clock
        self._lock = asyncio.Lock()  # serialise state-changing flows (single-process demo)

    # ------------------------------------------------------------------ helpers
    def get(self, agreement_id: str) -> fsm.ContractRecord:
        c = self.repo.get_contract(agreement_id)
        if c is None:
            raise NotFoundError(agreement_id)
        return c

    def _move(self, c: fsm.ContractRecord, state: FsmState, reason: Optional[str] = None):
        return fsm.transition(c, state, reason=reason, now=self.clock())

    # ------------------------------------------------------------------- create
    async def create_contract(self, payload: SlaContractCreate) -> fsm.ContractRecord:
        """Draft the contract, then authorize the PayPal hold -> PAYPAL_AUTH_LOCKED."""
        async with self._lock:
            now = self.clock()
            agreement_id = payload.agreement_id or f"agr_{uuid.uuid4().hex[:12]}"
            if self.repo.get_contract(agreement_id):
                raise fsm.FsmError(f"Agreement '{agreement_id}' already exists")
            c = fsm.ContractRecord(
                agreement_id=agreement_id,
                buyer_agent_id=payload.buyer_agent_id,
                worker_agent_id=payload.worker_agent_id,
                worker_paypal_email=payload.worker_paypal_email,
                budget_micros=payload.budget_micros,
                task_description=payload.task_description,
                timeout_seconds=payload.timeout_seconds,
                created_at=now,
                updated_at=now,
            )
            c.transitions.append(
                fsm.TransitionRecord(agreement_id, None, FsmState.AGREEMENT_DRAFTED,
                                     "SLA contract drafted", now)
            )
            self.repo.save_contract(c)

            try:
                order = await self.engine.create_escrow_authorization_order(
                    agreement_id, payload.budget_micros, payload.task_description
                )
            except Exception as exc:  # network / PayPal error: contract stays DRAFTED
                raise UpstreamError(f"PayPal authorization failed: {exc}") from exc
            auth_id = extract_authorization_id(order)
            if auth_id is None:
                raise UpstreamError("PayPal order has no authorization (buyer approval required)")

            c.paypal_order_id = order.get("id")
            c.paypal_authorization_id = auth_id
            c.authorized_at = self.clock()
            self._move(c, FsmState.PAYPAL_AUTH_LOCKED, "PayPal authorization hold locked")
            with self.repo.db.transaction():
                # Demo funding: the buyer vault is topped up so the hold can be placed.
                self.ledger.deposit(BUYER, c.budget_micros, agreement_id,
                                    "Buyer vault funding for escrow hold")
                self.ledger.record_transfer(ESCROW, BUYER, c.budget_micros, agreement_id,
                                            f"PayPal authorization hold locked ({agreement_id})")
                self.repo.save_contract(c)
            return c

    # ------------------------------------------------------------------- expiry
    async def check_expiry(self, agreement_id: str) -> fsm.ContractRecord:
        async with self._lock:
            c = self.get(agreement_id)
            if fsm.expire_if_needed(c, self.clock()):
                self._release_expired_hold(c)
            return c

    def _release_expired_hold(self, c: fsm.ContractRecord) -> None:
        with self.repo.db.transaction():
            self.ledger.record_transfer(BUYER, ESCROW, c.budget_micros, c.agreement_id,
                                        f"Authorization expired: hold released to buyer ({c.agreement_id})")
            self.repo.save_contract(c)

    # ------------------------------------------------------------------- submit
    async def submit_work(
        self,
        agreement_id: str,
        source_code: str,
        test_suite: str,
        semantic_score: Optional[float] = None,
    ) -> fsm.ContractRecord:
        """Worker delivery -> sandbox -> arbitration -> capture/void -> payout dispatch."""
        async with self._lock:
            c = self.get(agreement_id)
            try:
                self._move(c, FsmState.SANDBOX_EXECUTING, "Worker deliverable submitted")
            except fsm.AuthorizationExpiredError:
                self._release_expired_hold(c)
                raise
            self.repo.save_contract(c)
            self._move(c, FsmState.ARBITRATION_AUDITING, "Running pytest + AST scan + rubric")
            self.repo.save_contract(c)

            report: ArbitrationReport = await asyncio.to_thread(
                self.arbiter.evaluate,
                agreement_id=agreement_id,
                worker_code=source_code,
                buyer_test_code=test_suite,
                budget_micros=c.budget_micros,
                semantic_score=semantic_score,
                platform_fee_bps=self.settings.PROTOCOL_FEE_BPS,
                paypal_payout_fee_micros=self.settings.PAYPAL_PAYOUT_FEE_MICROS,
            )
            c.report = report.model_dump(mode="json")
            c.verification_score = report.composite_score
            split = report.settlement
            self._move(c, fsm.target_state_for_capture(split.decision),
                       f"Score {report.composite_score:.4f} -> {split.decision}")
            self.repo.save_contract(c)

            if split.decision == "VOID_AUTHORIZATION":
                await self._void(c, report)
            else:
                await self._capture_and_payout(c, report)
            return c

    async def _void(self, c: fsm.ContractRecord, report: ArbitrationReport) -> None:
        try:
            res = await self.engine.void_authorization(c.agreement_id, c.paypal_authorization_id)
        except Exception as exc:
            raise UpstreamError(f"PayPal void failed: {exc}") from exc
        if not res.get("ok", False):
            raise UpstreamError(f"PayPal void rejected: {res}")
        self._move(c, FsmState.ESCROW_VOIDED_REFUNDED, "Authorization voided; hold released to buyer")
        with self.repo.db.transaction():
            self.ledger.record_settlement(c.agreement_id, report.settlement)
            self.repo.save_contract(c)

    async def _capture_and_payout(self, c: fsm.ContractRecord, report: ArbitrationReport) -> None:
        split = report.settlement
        try:
            res = await self.engine.capture_authorization(
                c.agreement_id, c.paypal_authorization_id, split.capture_micros, is_final_capture=True
            )
        except Exception as exc:
            raise UpstreamError(f"PayPal capture failed: {exc}") from exc

        if not res.get("ok"):
            # Halt payout: no ledger settlement, funds stay on hold pending operator alert.
            self._move(c, FsmState.CAPTURE_DECLINED,
                       f"PayPal capture declined ({res.get('paypal_status')}); payout halted")
            self.repo.save_contract(c)
            return

        c.paypal_capture_id = res.get("capture_id")
        captured = (FsmState.PAYPAL_CAPTURED if split.decision == "FULL_CAPTURE"
                    else FsmState.PARTIAL_CAPTURED)
        self._move(c, captured, f"Captured {split.capture_usd} USD")
        with self.repo.db.transaction():
            self.ledger.record_settlement(c.agreement_id, split)
            self.repo.save_contract(c)
        await self._dispatch_payout(c, attempt=1)

    # ------------------------------------------------------------------- payout
    async def _dispatch_payout(self, c: fsm.ContractRecord, attempt: int) -> None:
        split = ArbitrationReport(**c.report).settlement
        try:
            res = await self.engine.dispatch_worker_payout(
                c.agreement_id, c.worker_paypal_email, split.worker_net_payout_micros,
                c.verification_score or 0.0,
            )
        except Exception as exc:
            raise UpstreamError(f"PayPal payout failed: {exc}") from exc

        self._move(c, FsmState.PAYOUT_DISPATCHED, f"Payout attempt {attempt} dispatched")
        if res.get("status") == "SKIPPED_BELOW_ONE_CENT":
            self._move(c, FsmState.SETTLED_COMPLETE, "Net payout below one cent; nothing to disburse")
            self.repo.save_contract(c)
            return
        batch_id = res["payout_response"]["batch_header"]["payout_batch_id"]
        c.paypal_payout_batch_id = batch_id
        with self.repo.db.transaction():
            self.repo.save_contract(c)
            self.repo.save_payout(f"{c.agreement_id}:payout", c.agreement_id, batch_id,
                                  split.worker_net_payout_micros, "DISPATCHED", attempt, self.clock())

    async def retry_payout(self, agreement_id: str) -> fsm.ContractRecord:
        """PAYOUT_FAILED_REVERSED -> PAYOUT_DISPATCHED: re-reserve funds and re-dispatch."""
        async with self._lock:
            c = self.get(agreement_id)
            if c.fsm_state != FsmState.PAYOUT_FAILED_REVERSED:
                raise fsm.IllegalTransitionError(c.fsm_state, FsmState.PAYOUT_DISPATCHED)
            split = ArbitrationReport(**c.report).settlement
            prev = self.repo.get_payout_for_agreement(agreement_id)
            attempt = (prev["attempt"] if prev else 1) + 1
            await self._dispatch_payout(c, attempt)  # raises before any state change on failure
            with self.repo.db.transaction():
                self.ledger.record_transfer(WORKER, ESCROW, split.worker_net_payout_micros,
                                            agreement_id, f"Payout retry: escrow reserve re-debited ({agreement_id})")
            return c

    # ----------------------------------------------------------------- webhooks
    async def handle_webhook(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Idempotent payout webhook handler. Caller has already verified the signature."""
        event_id = event.get("id")
        event_type = str(event.get("event_type", ""))
        resource = event.get("resource") or {}
        batch_id = resource.get("payout_batch_id") or (
            (resource.get("payout_item") or {}).get("payout_batch_id")
        )
        async with self._lock:
            with self.repo.db.transaction():
                if not self.repo.record_webhook_event(event_id, event_type, batch_id, event, self.clock()):
                    return {"status": "duplicate", "event_id": event_id}
                outcome, agreement_id = self._apply_payout_event(event_type, batch_id)
                self.repo.set_webhook_outcome(event_id, outcome)
            return {"status": outcome, "event_id": event_id, "agreement_id": agreement_id}

    def _apply_payout_event(self, event_type: str, batch_id: Optional[str]):
        target = fsm.target_state_for_payout_event(event_type)
        if target is None:
            return "ignored_event_type", None
        payout = self.repo.get_payout_by_batch(batch_id) if batch_id else None
        if payout is None:
            return "ignored_unknown_payout", None
        c = self.get(payout["agreement_id"])
        if not fsm.can_transition(c.fsm_state, target):
            return "ignored_illegal_state", c.agreement_id
        status = "SUCCEEDED" if target == FsmState.SETTLED_COMPLETE else (
            "UNCLAIMED" if event_type.upper().endswith("UNCLAIMED") else "FAILED")
        self._move(c, target, f"Webhook {event_type}")
        if target == FsmState.PAYOUT_FAILED_REVERSED:
            # Re-credit the escrow reserve so the payout can be retried.
            self.ledger.record_transfer(ESCROW, WORKER, payout["amount_micros"], c.agreement_id,
                                        f"Payout {status.lower()}: funds returned to escrow reserve ({c.agreement_id})")
        self.repo.save_payout(payout["payout_id"], c.agreement_id, payout["batch_id"],
                              payout["amount_micros"], status, payout["attempt"], self.clock())
        self.repo.save_contract(c)
        return "processed", c.agreement_id

    @staticmethod
    def build_payout_event(batch_id: str, outcome: str, event_id: Optional[str] = None) -> Dict[str, Any]:
        """Synthesize a PayPal payout-item webhook body (used by the demo + tests)."""
        suffix = {"succeeded": "SUCCEEDED", "failed": "FAILED", "unclaimed": "UNCLAIMED"}[outcome.lower()]
        return {
            "id": event_id or f"WH-{uuid.uuid4().hex[:16].upper()}",
            "event_type": f"PAYMENT.PAYOUTS-ITEM.{suffix}",
            "resource": {"payout_batch_id": batch_id, "transaction_status": suffix},
        }

    # --------------------------------------------------------------------- demo
    async def run_scenario(self, name: str, payout_outcome: str = "succeeded") -> fsm.ContractRecord:
        scenario: PresetScenario = get_scenario(name)
        c = await self.create_contract(SlaContractCreate(
            agreement_id=f"agr_{scenario.scenario_id}_{uuid.uuid4().hex[:6]}",
            buyer_agent_id="buyer-agent-demo",
            worker_agent_id="worker-agent-alpha",
            worker_paypal_email=self.settings.WORKER_SANDBOX_EMAIL,
            budget_micros=scenario.budget_micros,
            task_description=scenario.title + " - " + scenario.description,
        ))
        c = await self.submit_work(c.agreement_id, scenario.worker_code, scenario.buyer_test_code,
                                   semantic_score=scenario.semantic_score)
        if c.fsm_state == FsmState.PAYOUT_DISPATCHED and c.paypal_payout_batch_id:
            await self.handle_webhook(self.build_payout_event(c.paypal_payout_batch_id, payout_outcome))
            c = self.get(c.agreement_id)
        return c
