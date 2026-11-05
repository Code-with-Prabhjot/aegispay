"""
backend/app/services/fsm.py
Financial FSM transition engine for the AegisPay escrow lifecycle.

Implements the diagram in section 3 of the architecture dossier:

    AGREEMENT_DRAFTED -> PAYPAL_AUTH_LOCKED -> {AUTH_EXPIRED | SANDBOX_EXECUTING}
    SANDBOX_EXECUTING -> ARBITRATION_AUDITING
    ARBITRATION_AUDITING -> {FULL_CAPTURE_PENDING | PARTIAL_CAPTURE_PENDING | VOID_PENDING}
    FULL_CAPTURE_PENDING    -> {PAYPAL_CAPTURED | CAPTURE_DECLINED}
    PARTIAL_CAPTURE_PENDING -> {PARTIAL_CAPTURED | CAPTURE_DECLINED}
    VOID_PENDING            -> ESCROW_VOIDED_REFUNDED
    PAYPAL_CAPTURED / PARTIAL_CAPTURED -> PAYOUT_DISPATCHED
    PAYOUT_DISPATCHED -> {SETTLED_COMPLETE | PAYOUT_FAILED_REVERSED}
    PAYOUT_FAILED_REVERSED -> PAYOUT_DISPATCHED   (retry)

Terminal states: AUTH_EXPIRED, CAPTURE_DECLINED, ESCROW_VOIDED_REFUNDED, SETTLED_COMPLETE.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, List, Optional

from backend.app.schemas import FsmState, SlaContractResponse

S = FsmState

AUTH_TTL = timedelta(hours=72)  # PayPal authorization honor window

LEGAL_TRANSITIONS: Dict[FsmState, FrozenSet[FsmState]] = {
    S.AGREEMENT_DRAFTED: frozenset({S.PAYPAL_AUTH_LOCKED}),
    S.PAYPAL_AUTH_LOCKED: frozenset({S.AUTH_EXPIRED, S.SANDBOX_EXECUTING}),
    S.AUTH_EXPIRED: frozenset(),
    S.SANDBOX_EXECUTING: frozenset({S.ARBITRATION_AUDITING}),
    S.ARBITRATION_AUDITING: frozenset(
        {S.FULL_CAPTURE_PENDING, S.PARTIAL_CAPTURE_PENDING, S.VOID_PENDING}
    ),
    S.FULL_CAPTURE_PENDING: frozenset({S.PAYPAL_CAPTURED, S.CAPTURE_DECLINED}),
    S.PARTIAL_CAPTURE_PENDING: frozenset({S.PARTIAL_CAPTURED, S.CAPTURE_DECLINED}),
    S.VOID_PENDING: frozenset({S.ESCROW_VOIDED_REFUNDED}),
    S.PAYPAL_CAPTURED: frozenset({S.PAYOUT_DISPATCHED}),
    S.PARTIAL_CAPTURED: frozenset({S.PAYOUT_DISPATCHED}),
    S.CAPTURE_DECLINED: frozenset(),
    S.ESCROW_VOIDED_REFUNDED: frozenset(),
    S.PAYOUT_DISPATCHED: frozenset({S.SETTLED_COMPLETE, S.PAYOUT_FAILED_REVERSED}),
    S.SETTLED_COMPLETE: frozenset(),
    S.PAYOUT_FAILED_REVERSED: frozenset({S.PAYOUT_DISPATCHED}),
}

TERMINAL_STATES: FrozenSet[FsmState] = frozenset(
    s for s, nxt in LEGAL_TRANSITIONS.items() if not nxt
)

# PayPal payout webhook event types -> target FSM state (matched by suffix).
PAYOUT_WEBHOOK_TARGETS = (
    ("SUCCEEDED", S.SETTLED_COMPLETE),
    ("FAILED", S.PAYOUT_FAILED_REVERSED),
    ("UNCLAIMED", S.PAYOUT_FAILED_REVERSED),
)


class FsmError(Exception):
    """Base class for FSM errors."""


class IllegalTransitionError(FsmError):
    """Raised when a requested transition is not in the legal-transition table."""

    def __init__(self, current: FsmState, requested: FsmState):
        self.current = current
        self.requested = requested
        if current in TERMINAL_STATES:
            msg = f"{current.value} is terminal; cannot transition to {requested.value}"
        else:
            msg = f"Illegal transition {current.value} -> {requested.value}"
        super().__init__(msg)


class AuthorizationExpiredError(FsmError):
    """Raised when work is attempted on an authorization older than the 72h honor window."""


@dataclass
class TransitionRecord:
    agreement_id: str
    from_state: Optional[FsmState]
    to_state: FsmState
    reason: Optional[str]
    created_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_state": self.from_state.value if self.from_state else None,
            "to_state": self.to_state.value,
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ContractRecord:
    """Mutable contract aggregate the FSM operates on (persisted by db.py)."""
    agreement_id: str
    buyer_agent_id: str
    worker_agent_id: str
    worker_paypal_email: str
    budget_micros: int
    task_description: str
    timeout_seconds: int
    fsm_state: FsmState = FsmState.AGREEMENT_DRAFTED
    paypal_order_id: Optional[str] = None
    paypal_authorization_id: Optional[str] = None
    paypal_capture_id: Optional[str] = None
    paypal_payout_batch_id: Optional[str] = None
    verification_score: Optional[float] = None
    authorized_at: Optional[datetime] = None
    report: Optional[Dict[str, Any]] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    transitions: List[TransitionRecord] = field(default_factory=list)
    vault_id: Optional[str] = None
    hash_chain_head: Optional[str] = None
    merkle_root: Optional[str] = None
    buyer_signature: Optional[str] = None
    worker_signature: Optional[str] = None

    def to_response(self) -> SlaContractResponse:
        return SlaContractResponse(
            agreement_id=self.agreement_id,
            buyer_agent_id=self.buyer_agent_id,
            worker_agent_id=self.worker_agent_id,
            worker_paypal_email=self.worker_paypal_email,
            budget_micros=self.budget_micros,
            task_description=self.task_description,
            timeout_seconds=self.timeout_seconds,
            fsm_state=self.fsm_state,
            paypal_order_id=self.paypal_order_id,
            paypal_authorization_id=self.paypal_authorization_id,
            paypal_capture_id=self.paypal_capture_id,
            paypal_payout_batch_id=self.paypal_payout_batch_id,
            verification_score=self.verification_score,
            created_at=self.created_at,
            updated_at=self.updated_at,
            vault_id=self.vault_id,
            hash_chain_head=self.hash_chain_head,
            merkle_root=self.merkle_root,
            buyer_signature=self.buyer_signature,
            worker_signature=self.worker_signature,
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_terminal(state: FsmState) -> bool:
    return state in TERMINAL_STATES


def can_transition(current: FsmState, new_state: FsmState) -> bool:
    return new_state in LEGAL_TRANSITIONS[current]


def is_authorization_expired(contract: ContractRecord, now: Optional[datetime] = None) -> bool:
    """True when a locked authorization is older than the 72h honor window."""
    if contract.fsm_state != S.PAYPAL_AUTH_LOCKED or contract.authorized_at is None:
        return False
    return (now or _utcnow()) - contract.authorized_at > AUTH_TTL


def _apply(contract: ContractRecord, new_state: FsmState, reason: Optional[str],
           now: datetime) -> TransitionRecord:
    rec = TransitionRecord(contract.agreement_id, contract.fsm_state, new_state, reason, now)
    contract.fsm_state = new_state
    contract.updated_at = now
    contract.transitions.append(rec)
    return rec


def transition(
    contract: ContractRecord,
    new_state: FsmState,
    *,
    reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> TransitionRecord:
    """
    Move `contract` to `new_state`, recording a timestamped TransitionRecord.

    Raises IllegalTransitionError for anything outside LEGAL_TRANSITIONS (including any
    transition out of a terminal state). Starting work (-> SANDBOX_EXECUTING) on an
    authorization older than 72h instead moves the contract to AUTH_EXPIRED and raises
    AuthorizationExpiredError.
    """
    now = now or _utcnow()
    if not can_transition(contract.fsm_state, new_state):
        raise IllegalTransitionError(contract.fsm_state, new_state)
    if new_state == S.SANDBOX_EXECUTING and is_authorization_expired(contract, now):
        _apply(contract, S.AUTH_EXPIRED, "PayPal authorization exceeded 72h honor window", now)
        raise AuthorizationExpiredError(
            f"Authorization for {contract.agreement_id} expired (>72h); contract is AUTH_EXPIRED"
        )
    return _apply(contract, new_state, reason, now)


def expire_if_needed(
    contract: ContractRecord, now: Optional[datetime] = None
) -> Optional[TransitionRecord]:
    """If the 72h TTL has elapsed on a locked authorization, transition to AUTH_EXPIRED."""
    now = now or _utcnow()
    if is_authorization_expired(contract, now):
        return _apply(contract, S.AUTH_EXPIRED, "PayPal authorization exceeded 72h honor window", now)
    return None


def target_state_for_capture(decision: str) -> FsmState:
    """Arbitration decision -> *_PENDING state."""
    return {
        "FULL_CAPTURE": S.FULL_CAPTURE_PENDING,
        "PARTIAL_CAPTURE": S.PARTIAL_CAPTURE_PENDING,
        "VOID_AUTHORIZATION": S.VOID_PENDING,
    }[decision]


def target_state_for_payout_event(event_type: str) -> Optional[FsmState]:
    """Map a PayPal payout-item webhook event type to the resulting FSM state (or None)."""
    et = event_type.upper()
    if "PAYOUT" not in et:
        return None
    for suffix, state in PAYOUT_WEBHOOK_TARGETS:
        if et.endswith(suffix):
            return state
    return None
