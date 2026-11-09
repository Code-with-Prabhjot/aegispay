"""
backend/app/services/orchestrator.py
Glue between the FSM, ledger, PayPal engine, arbiter and persistence.
Every state change goes through fsm.transition() and is persisted with its history.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
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
from backend.app.services.a2a import validate_dual_signatures
from backend.app.services.arbitration_engine import ArbitrationArbiter, get_execution_runner
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.llm_judge import get_semantic_judge
from backend.app.services.merkle_audit import (
    compile_7leaf_bundle,
    compute_genesis_hash,
    compute_transition_hash,
)
from backend.app.services.paypal_escrow import compute_settlement_split, usd_amount_to_micros
from backend.app.services.scenario_presets import PresetScenario, get_scenario
from backend.app.services.vault_service import (
    VaultCircuitBreakerError,
    check_vault_allowance,
    record_vault_settlement,
    release_vault_funds,
    reservation_epoch,
    reserve_vault_funds,
)

logger = logging.getLogger(__name__)

ESCROW = LedgerAccount.PAYPAL_ESCROW_HOLD.value
BUYER = LedgerAccount.BUYER_VAULT.value
WORKER = LedgerAccount.WORKER_PAYABLE.value

# A contract in these states has a decision recorded but PayPal hasn't confirmed it yet.
SETTLEMENT_PENDING_STATES = frozenset({
    FsmState.FULL_CAPTURE_PENDING,
    FsmState.PARTIAL_CAPTURE_PENDING,
    FsmState.VOID_PENDING,
})
ARBITER_FAILURE_PREFIX = "Arbiter failure"
# An evaluation that has been running this long has died (crash or restart); the worker's code is not stored.
STUCK_EVALUATION_AFTER = timedelta(minutes=10)

AUTHORIZATION_CREATED = "PAYMENT.AUTHORIZATION.CREATED"
AUTHORIZATION_VOIDED = "PAYMENT.AUTHORIZATION.VOIDED"
CAPTURE_COMPLETED = "PAYMENT.CAPTURE.COMPLETED"

# States at or past a successful capture; a capture webhook here confirms what already happened.
POST_CAPTURE_STATES = frozenset({
    FsmState.PAYPAL_CAPTURED,
    FsmState.PARTIAL_CAPTURED,
    FsmState.PAYOUT_DISPATCHED,
    FsmState.PAYOUT_FAILED_REVERSED,
    FsmState.SETTLED_COMPLETE,
})


class NotFoundError(Exception):
    pass


class UpstreamError(Exception):
    """PayPal (or mock) call failed."""


class PolicyViolationError(fsm.FsmError):
    """The request is refused by a trust policy (test-suite binding, scanning)."""


class SandboxUnavailableError(UpstreamError):
    """No isolated sandbox is available, so untrusted code is not run."""


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        engine: Any = None,
        settings: Settings = None,
        arbiter: Optional[ArbitrationArbiter] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        broadcaster: Optional[Any] = None,
        paypal: Optional[Any] = None,
    ):
        self.repo = repo
        self.ledger = ledger
        self.engine = engine or paypal
        self.settings = settings
        self.arbiter = arbiter or ArbitrationArbiter(
            runner=get_execution_runner(settings), judge=get_semantic_judge(settings)
        )
        self.clock = clock
        self.broadcaster = broadcaster
        self._lock = asyncio.Lock()  # serialise state-changing flows (single-process demo)

    def set_broadcaster(self, broadcaster: Any) -> None:
        """Sets or updates the real-time event broadcaster."""
        self.broadcaster = broadcaster

    @property
    def lock(self) -> asyncio.Lock:
        """The lock serialising state-changing flows; the sweeper shares it."""
        return self._lock

    # ------------------------------------------------------------------ helpers
    def get(self, agreement_id: str) -> fsm.ContractRecord:
        c = self.repo.get_contract(agreement_id)
        if c is None:
            raise NotFoundError(agreement_id)
        return c

    def _move(self, c: fsm.ContractRecord, state: FsmState, reason: Optional[str] = None):
        prev_state = c.fsm_state
        now = self.clock()
        rec = fsm.transition(c, state, reason=reason, now=now)

        prev_hash = c.hash_chain_head or compute_genesis_hash(c.agreement_id)
        payload_dict = {
            "reason": reason,
            "from_state": prev_state.value if prev_state else None,
            "to_state": state.value,
        }
        new_hash = compute_transition_hash(prev_hash, now.isoformat(), state.value, payload_dict)
        c.hash_chain_head = new_hash
        self.repo.append_hash_chain_entry(
            agreement_id=c.agreement_id,
            state=state.value,
            prev_hash=prev_hash,
            current_hash=new_hash,
            payload_json=json.dumps(payload_dict),
            created_at=now,
        )

        if state in (FsmState.SETTLED_COMPLETE, FsmState.ESCROW_VOIDED_REFUNDED):
            bundle = compile_7leaf_bundle(c, repo=self.repo, ledger=self.ledger)
            c.merkle_root = bundle["merkle_root"]
            self.repo.update_contract_proofs(
                c.agreement_id, hash_chain_head=c.hash_chain_head, merkle_root=c.merkle_root
            )

        self.repo.save_contract(c)

        if self.broadcaster:
            self.broadcaster.broadcast_sync({
                "event_type": "FSM_TRANSITION",
                "agreement_id": c.agreement_id,
                "timestamp": now.isoformat(),
                "data": {
                    "from_state": prev_state.value if prev_state else None,
                    "to_state": state.value,
                    "reason": reason,
                    "contract": c.to_response().model_dump(mode="json"),
                },
            })
        return rec

    # ------------------------------------------------------------------- create
    async def create_contract(
        self, payload: SlaContractCreate, vault_id: Optional[str] = None
    ) -> fsm.ContractRecord:
        """Draft the contract, then authorize the PayPal hold -> PAYPAL_AUTH_LOCKED."""
        async with self._lock:
            now = self.clock()
            agreement_id = payload.agreement_id or f"agr_{uuid.uuid4().hex[:12]}"
            effective_vault_id = vault_id or getattr(payload, "vault_id", None)
            if self.repo.get_contract(agreement_id):
                raise fsm.FsmError(f"Agreement '{agreement_id}' already exists")

            has_buyer_sig = bool(getattr(payload, "buyer_signature", None))
            has_worker_sig = bool(getattr(payload, "worker_signature", None))
            if self.settings.REQUIRE_A2A_SIGNATURES and not (has_buyer_sig and has_worker_sig):
                raise PolicyViolationError("A buyer- and worker-signed agreement is required")
            if has_buyer_sig or has_worker_sig:
                if not validate_dual_signatures(
                    payload.model_dump(), allow_unbound_ids=self.settings.is_mock
                ):
                    raise fsm.FsmError("A2A signature validation failed: contractor proposal rejected")

            if payload.test_suite:
                scan_suite = getattr(self.arbiter, "scan_test_suite", None)
                if scan_suite is not None:
                    suite_safe, suite_violations = scan_suite(payload.test_suite)
                    if not suite_safe:
                        raise PolicyViolationError(
                            "The test suite failed the security scan: " + "; ".join(suite_violations)
                        )

            if effective_vault_id:
                allowed, reason = check_vault_allowance(
                    effective_vault_id,
                    payload.budget_micros,
                    repo=self.repo,
                    now_epoch=int(now.timestamp()),
                )
                if not allowed:
                    raise VaultCircuitBreakerError(reason, vault_id=effective_vault_id)

            # Count the hold against the daily budget now, not at capture, so concurrent contracts
            # cannot all pass the check against the same unspent allowance.
            reserved = False
            if effective_vault_id:
                reserve_vault_funds(
                    effective_vault_id, payload.budget_micros, repo=self.repo,
                    now_epoch=int(now.timestamp()),
                )
                reserved = True

            try:
                return await self._draft_and_lock(payload, agreement_id, effective_vault_id, now)
            except BaseException:
                if reserved:
                    release_vault_funds(effective_vault_id, payload.budget_micros, repo=self.repo)
                raise

    async def _draft_and_lock(
        self,
        payload: SlaContractCreate,
        agreement_id: str,
        effective_vault_id: Optional[str],
        now: datetime,
    ) -> fsm.ContractRecord:
        """Persist the draft, place the PayPal hold and lock it (the caller holds the lock)."""
        genesis_hash = compute_genesis_hash(agreement_id)
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
            vault_id=effective_vault_id,
            hash_chain_head=genesis_hash,
            buyer_signature=getattr(payload, "buyer_signature", None),
            worker_signature=getattr(payload, "worker_signature", None),
            test_suite=payload.test_suite,
            test_suite_sha256=sha256_hex(payload.test_suite) if payload.test_suite else None,
        )
        c.transitions.append(
            fsm.TransitionRecord(agreement_id, None, FsmState.AGREEMENT_DRAFTED,
                                 "SLA contract drafted", now)
        )
        self.repo.save_contract(c)
        self.repo.append_hash_chain_entry(
            agreement_id=agreement_id,
            state=FsmState.AGREEMENT_DRAFTED.value,
            prev_hash="0" * 64,
            current_hash=genesis_hash,
            payload_json=json.dumps({"task_description": payload.task_description, "budget_micros": payload.budget_micros}),
            created_at=now,
        )
        if self.broadcaster:
            self.broadcaster.broadcast_sync({
                "event_type": "FSM_TRANSITION",
                "agreement_id": c.agreement_id,
                "timestamp": now.isoformat(),
                "data": {
                    "from_state": None,
                    "to_state": FsmState.AGREEMENT_DRAFTED.value,
                    "reason": "SLA contract drafted",
                    "contract": c.to_response().model_dump(mode="json"),
                },
            })

        try:
            order = await self.engine.create_escrow_authorization_order(
                agreement_id,
                payload.budget_micros,
                payload.task_description,
                vault_id=effective_vault_id,
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
            self._record_hold_locked(c)
        return c

    def _record_hold_locked(self, c: fsm.ContractRecord) -> None:
        # Demo funding: the buyer vault is topped up so the hold can be placed.
        self.ledger.deposit(BUYER, c.budget_micros, c.agreement_id,
                            "Buyer vault funding for escrow hold")
        self.ledger.record_transfer(ESCROW, BUYER, c.budget_micros, c.agreement_id,
                                    f"PayPal authorization hold locked ({c.agreement_id})")
        self.repo.save_contract(c)

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
            self._settle_vault(c, 0, is_void=True, penalize=False)
            self.repo.save_contract(c)

    def _settle_vault(self, c: fsm.ContractRecord, captured_micros: int, is_void: bool,
                      penalize: bool = True) -> None:
        """
        Closes the vault accounting for a contract's hold: return the reservation made when the hold
        was placed, then book what was actually captured (or, for a worker-fault void, the strike
        toward the circuit breaker).
        """
        vault_id = getattr(c, "vault_id", None)
        if not vault_id:
            return
        release_vault_funds(vault_id, c.budget_micros, repo=self.repo,
                            reserved_at_epoch=reservation_epoch(c))
        if is_void and not penalize:
            return
        record_vault_settlement(
            vault_id, captured_micros, is_void=is_void, repo=self.repo,
            now_epoch=int(self.clock().timestamp()),
        )

    @staticmethod
    def _voided_for_worker_fault(report: ArbitrationReport) -> bool:
        return not report.stdout.startswith(ARBITER_FAILURE_PREFIX)

    # ------------------------------------------------------------------- submit
    def _resolve_test_suite(self, c: fsm.ContractRecord, supplied: Optional[str]) -> str:
        """
        The suite a deliverable is graded against is fixed by the agreement. A submitter may only
        repeat it. Without a committed suite, a supplied one is graded in demo/test runs only.
        """
        if c.test_suite:
            if supplied is not None and sha256_hex(supplied) != c.test_suite_sha256:
                raise PolicyViolationError(
                    "The test suite is fixed by the agreement; the submitted suite does not match it"
                )
            return c.test_suite
        if not self.settings.allow_unbound_test_suite:
            raise PolicyViolationError(
                "This agreement has no committed test suite; the buyer must supply one at creation"
            )
        if not supplied:
            raise PolicyViolationError("A test suite is required")
        return supplied

    async def submit_work(
        self,
        agreement_id: str,
        source_code: str,
        test_suite: Optional[str] = None,
        semantic_score: Optional[float] = None,
        runtime: Optional[str] = None,
    ) -> fsm.ContractRecord:
        """Worker delivery -> sandbox -> arbitration -> capture/void -> payout dispatch."""
        async with self._lock:
            c = self.get(agreement_id)
            if semantic_score is not None and not self.settings.is_mock:
                raise PolicyViolationError("semantic_score overrides are only accepted in mock mode")
            test_suite = self._resolve_test_suite(c, test_suite)
            if not self.settings.allow_unisolated_sandbox:
                is_isolated = getattr(self.arbiter, "is_isolated", None)
                if is_isolated is None or not is_isolated(source_code, test_suite, runtime):
                    raise SandboxUnavailableError(
                        "Refusing to run untrusted code without an isolated sandbox "
                        "(start Docker and build the sandbox image, or set ALLOW_UNISOLATED_SANDBOX for demos)"
                    )
            try:
                self._move(c, FsmState.SANDBOX_EXECUTING, "Worker deliverable submitted")
            except fsm.AuthorizationExpiredError:
                self._release_expired_hold(c)
                raise
            self.repo.save_contract(c)
            self._move(c, FsmState.ARBITRATION_AUDITING, "Running test suite + AST scan + rubric")
            self.repo.save_contract(c)
            budget_micros = c.budget_micros

        # The sandbox run takes seconds. The contract is already in ARBITRATION_AUDITING, which
        # rejects a second submission, so release the lock that webhooks and other requests wait on.
        try:
            report: ArbitrationReport = await asyncio.to_thread(
                self.arbiter.evaluate,
                agreement_id=agreement_id,
                worker_code=source_code,
                buyer_test_code=test_suite,
                budget_micros=budget_micros,
                semantic_score=semantic_score,
                platform_fee_bps=self.settings.PROTOCOL_FEE_BPS,
                paypal_payout_fee_micros=self.settings.PAYPAL_PAYOUT_FEE_MICROS,
                runtime=runtime,
            )
        except Exception as exc:  # noqa: BLE001 - any arbiter failure must not strand the hold
            logger.exception("Arbiter failed for %s", agreement_id)
            async with self._lock:
                c = self._contract_awaiting_verdict(agreement_id)
                await self._void_after_arbiter_failure(c, exc)
                return c

        async with self._lock:
            c = self._contract_awaiting_verdict(agreement_id)
            c.report = report.model_dump(mode="json")
            c.verification_score = report.composite_score
            if self.broadcaster:
                self.broadcaster.broadcast_sync({
                    "event_type": "ARBITER_EVALUATED",
                    "agreement_id": agreement_id,
                    "timestamp": self.clock().isoformat(),
                    "data": {
                        "report": report.model_dump(mode="json"),
                        "verification_score": report.composite_score,
                        "decision": report.settlement.decision,
                        "passed_tests": report.passed_tests,
                        "total_tests": report.total_tests,
                        "ast_safe": report.ast_safe,
                    },
                })
            split = report.settlement
            self._move(c, fsm.target_state_for_capture(split.decision),
                       f"Score {report.composite_score:.4f} -> {split.decision}")
            self.repo.save_contract(c)

            if split.decision == "VOID_AUTHORIZATION":
                await self._void(c, report)
            else:
                await self._capture_and_payout(c, report)
            return c

    def _contract_awaiting_verdict(self, agreement_id: str) -> fsm.ContractRecord:
        """Reload a contract after its evaluation; refuse if something settled it in the meantime."""
        c = self.get(agreement_id)
        if c.fsm_state != FsmState.ARBITRATION_AUDITING:
            raise fsm.FsmError(
                f"{agreement_id} is {c.fsm_state.value}: it changed while it was being evaluated, "
                "so the evaluation result was discarded"
            )
        return c

    async def _void_after_arbiter_failure(self, c: fsm.ContractRecord, exc: BaseException) -> None:
        """
        The arbiter crashed, so nothing was verified and the worker cannot be paid. Void the hold so
        the buyer's funds are not left locked; the worker can resubmit under a new agreement.
        """
        split = compute_settlement_split(c.budget_micros, 0.0)
        report = ArbitrationReport(
            agreement_id=c.agreement_id, s_pytest=0.0, s_ast=1.0, s_semantic=0.0, composite_score=0.0,
            passed_tests=0, total_tests=0, ast_safe=True, settlement=split,
            stdout=f"{ARBITER_FAILURE_PREFIX} ({type(exc).__name__}): hold voided, nothing was verified or captured.",
        )
        c.report = report.model_dump(mode="json")
        c.verification_score = 0.0
        self._move(c, FsmState.VOID_PENDING, f"Arbiter failed ({type(exc).__name__}); voiding the hold")
        await self._void(c, report, penalize_vault=False)

    async def retry_settlement(self, agreement_id: str) -> fsm.ContractRecord:
        """
        Re-drive a settlement PayPal never confirmed. The capture and void calls carry deterministic
        PayPal-Request-Ids, so repeating one that already went through returns PayPal's original answer.
        A contract whose evaluation died mid-flight (no stored code to re-run) is voided instead.
        """
        async with self._lock:
            c = self.get(agreement_id)
            if c.fsm_state in (FsmState.SANDBOX_EXECUTING, FsmState.ARBITRATION_AUDITING):
                if self.clock() - c.updated_at < STUCK_EVALUATION_AFTER:
                    raise fsm.FsmError(f"{agreement_id} is still being evaluated; retry later")
                await self._void_after_arbiter_failure(c, TimeoutError("evaluation never completed"))
                return c
            if c.fsm_state not in SETTLEMENT_PENDING_STATES:
                raise fsm.FsmError(f"Nothing to retry for {agreement_id} in state {c.fsm_state.value}")
            if not c.report:
                raise fsm.FsmError(f"{agreement_id} has no arbitration report to settle from")
            report = ArbitrationReport(**c.report)
            if c.fsm_state == FsmState.VOID_PENDING:
                await self._void(c, report)
            else:
                await self._capture_and_payout(c, report)
            return c

    async def _void(self, c: fsm.ContractRecord, report: ArbitrationReport, penalize_vault: bool = True) -> None:
        try:
            res = await self.engine.void_authorization(c.agreement_id, c.paypal_authorization_id)
        except Exception as exc:
            raise UpstreamError(f"PayPal void failed: {exc}") from exc
        if not res.get("ok", False):
            raise UpstreamError(f"PayPal void rejected: {res}")
        self._move(c, FsmState.ESCROW_VOIDED_REFUNDED, "Authorization voided; hold released to buyer")
        with self.repo.db.transaction():
            self.ledger.record_settlement(c.agreement_id, report.settlement)
            self._settle_vault(c, 0, is_void=True, penalize=penalize_vault)
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
            self._settle_vault(c, split.capture_micros, is_void=False)
            self.repo.save_contract(c)
        await self._dispatch_payout(c, attempt=1)

    # ------------------------------------------------------------------- payout
    async def _dispatch_payout(self, c: fsm.ContractRecord, attempt: int) -> None:
        split = ArbitrationReport(**c.report).settlement
        try:
            res = await self.engine.dispatch_worker_payout(
                c.agreement_id, c.worker_paypal_email, split.worker_net_payout_micros,
                c.verification_score or 0.0,
                attempt=attempt,
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
        """Idempotent webhook handler. Caller has already verified the signature."""
        event_id = event.get("id")
        event_type = str(event.get("event_type", ""))
        resource = event.get("resource") or {}
        batch_id = resource.get("payout_batch_id") or (
            (resource.get("payout_item") or {}).get("payout_batch_id")
        )
        async with self._lock:
            with self.repo.db.transaction():
                if not self.repo.record_webhook_event(event_id, event_type, batch_id or resource.get("id"),
                                                      event, self.clock()):
                    return {"status": "duplicate", "event_id": event_id}
                outcome, agreement_id = self._apply_event(event_type, resource, batch_id)
                self.repo.set_webhook_outcome(event_id, outcome)
            if event_type == CAPTURE_COMPLETED and outcome in ("processed", "already_applied"):
                await self._resume_payout(agreement_id, event_id)
            return {"status": outcome, "event_id": event_id, "agreement_id": agreement_id}

    def _apply_event(self, event_type: str, resource: Dict[str, Any], batch_id: Optional[str]):
        if event_type == AUTHORIZATION_CREATED:
            return self._apply_authorization_created(event_type, resource)
        if event_type == CAPTURE_COMPLETED:
            return self._apply_capture_completed(event_type, resource)
        if event_type == AUTHORIZATION_VOIDED:
            return self._apply_authorization_voided(event_type, resource)
        return self._apply_payout_event(event_type, batch_id)

    def _find_contract(self, authorization_id: Optional[str],
                       custom_id: Optional[str]) -> Optional[fsm.ContractRecord]:
        """Locate by authorization ID, else by custom_id (the agreement_id set on the order)."""
        c = self.repo.get_contract_by_authorization(authorization_id) if authorization_id else None
        if c is None and custom_id:
            c = self.repo.get_contract(custom_id)
            if c and authorization_id and c.paypal_authorization_id not in (None, authorization_id):
                return None
        return c

    def _apply_authorization_created(self, event_type: str, resource: Dict[str, Any]):
        """Locks the hold once the buyer approves; create_contract has already done so if no approval was needed."""
        auth_id = resource.get("id")
        c = self._find_contract(auth_id, resource.get("custom_id"))
        if c is None:
            return "ignored_unknown_contract", None
        if c.fsm_state != FsmState.AGREEMENT_DRAFTED:
            if c.paypal_authorization_id == auth_id:
                return "already_applied", c.agreement_id
            return "ignored_illegal_state", c.agreement_id
        if usd_amount_to_micros(resource.get("amount")) != (c.budget_micros // 10_000) * 10_000:
            return "ignored_amount_mismatch", c.agreement_id
        c.paypal_authorization_id = auth_id
        c.paypal_order_id = ((resource.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
        c.authorized_at = self.clock()
        self._move(c, FsmState.PAYPAL_AUTH_LOCKED, f"Webhook {event_type}")
        self._record_hold_locked(c)
        if c.vault_id:  # the reservation made at creation was released when that attempt failed
            reserve_vault_funds(c.vault_id, c.budget_micros, repo=self.repo,
                                now_epoch=int(self.clock().timestamp()))
        return "processed", c.agreement_id

    def _apply_capture_completed(self, event_type: str, resource: Dict[str, Any]):
        """Confirms a capture; if the synchronous capture response was lost, settles from here."""
        capture_id = resource.get("id")
        related = (resource.get("supplementary_data") or {}).get("related_ids") or {}
        c = self._find_contract(related.get("authorization_id"), resource.get("custom_id"))
        if c is None:
            return "ignored_unknown_contract", None
        if c.fsm_state in POST_CAPTURE_STATES and c.paypal_capture_id == capture_id:
            return "already_applied", c.agreement_id
        target = {
            FsmState.FULL_CAPTURE_PENDING: FsmState.PAYPAL_CAPTURED,
            FsmState.PARTIAL_CAPTURE_PENDING: FsmState.PARTIAL_CAPTURED,
        }.get(c.fsm_state)
        if target is None:
            return "ignored_illegal_state", c.agreement_id
        split = ArbitrationReport(**c.report).settlement
        if usd_amount_to_micros(resource.get("amount")) != split.capture_micros:
            return "ignored_amount_mismatch", c.agreement_id
        c.paypal_capture_id = capture_id
        self._move(c, target, f"Webhook {event_type}")
        self.ledger.record_settlement(c.agreement_id, split)
        self._settle_vault(c, split.capture_micros, is_void=False)
        self.repo.save_contract(c)
        return "processed", c.agreement_id

    async def _resume_payout(self, agreement_id: str, event_id: str) -> None:
        """Dispatch a captured contract's payout; on failure, let PayPal's redelivery try again."""
        c = self.get(agreement_id)
        if c.fsm_state not in (FsmState.PAYPAL_CAPTURED, FsmState.PARTIAL_CAPTURED):
            return
        try:
            await self._dispatch_payout(c, attempt=1)
        except UpstreamError:
            self.repo.delete_webhook_event(event_id)  # otherwise the redelivery is dropped as a duplicate
            raise

    def _apply_authorization_voided(self, event_type: str, resource: Dict[str, Any]):
        """Confirms a void; if the synchronous void response was lost, releases the hold from here."""
        c = self._find_contract(resource.get("id"), resource.get("custom_id"))
        if c is None:
            return "ignored_unknown_contract", None
        if c.fsm_state == FsmState.ESCROW_VOIDED_REFUNDED:
            return "already_applied", c.agreement_id
        if c.fsm_state != FsmState.VOID_PENDING:
            return "ignored_illegal_state", c.agreement_id
        self._move(c, FsmState.ESCROW_VOIDED_REFUNDED, f"Webhook {event_type}")
        report = ArbitrationReport(**c.report)
        self.ledger.record_settlement(c.agreement_id, report.settlement)
        self._settle_vault(c, 0, is_void=True, penalize=self._voided_for_worker_fault(report))
        self.repo.save_contract(c)
        return "processed", c.agreement_id

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
            "resource": {"payout_batch_id": batch_id, "transaction_status": {"SUCCEEDED": "SUCCESS"}.get(suffix, suffix)},
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
            test_suite=scenario.buyer_test_code,
        ))
        c = await self.submit_work(c.agreement_id, scenario.worker_code,
                                   semantic_score=scenario.semantic_score)
        if c.fsm_state == FsmState.PAYOUT_DISPATCHED and c.paypal_payout_batch_id:
            await self.handle_webhook(self.build_payout_event(c.paypal_payout_batch_id, payout_outcome))
            c = self.get(c.agreement_id)
        return c


Orchestrator = EscrowOrchestrator

