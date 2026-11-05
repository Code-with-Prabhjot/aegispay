"""
AegisPay Schemas Module
Pydantic v2 schemas and models with strict validation for:
- 11-State Financial Finite State Machine (FSM)
- SLA Contract creation & response models
- Verified Settlement split results
- Double-entry accounting ledger entries
"""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional
import uuid

from pydantic import BaseModel, Field, field_validator, model_validator


class FsmState(str, Enum):
    """
    Finite State Machine (FSM) states for the AegisPay Escrow & Settlement lifecycle.
    Covers the 11 logical milestones and all branch/terminal states defined in the blueprint.
    """
    AGREEMENT_DRAFTED = "AGREEMENT_DRAFTED"
    PAYPAL_AUTH_LOCKED = "PAYPAL_AUTH_LOCKED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    SANDBOX_EXECUTING = "SANDBOX_EXECUTING"
    ARBITRATION_AUDITING = "ARBITRATION_AUDITING"
    FULL_CAPTURE_PENDING = "FULL_CAPTURE_PENDING"
    PARTIAL_CAPTURE_PENDING = "PARTIAL_CAPTURE_PENDING"
    VOID_PENDING = "VOID_PENDING"
    PAYPAL_CAPTURED = "PAYPAL_CAPTURED"
    PARTIAL_CAPTURED = "PARTIAL_CAPTURED"
    CAPTURE_DECLINED = "CAPTURE_DECLINED"
    ESCROW_VOIDED_REFUNDED = "ESCROW_VOIDED_REFUNDED"
    PAYOUT_DISPATCHED = "PAYOUT_DISPATCHED"
    SETTLED_COMPLETE = "SETTLED_COMPLETE"
    PAYOUT_FAILED_REVERSED = "PAYOUT_FAILED_REVERSED"


class LedgerAccount(str, Enum):
    """Standard account heads for the AegisPay double-entry accounting ledger."""
    BUYER_VAULT = "buyer_vault"
    PAYPAL_ESCROW_HOLD = "paypal_escrow_hold"
    WORKER_PAYABLE = "worker_payable"
    PLATFORM_FEE_REVENUE = "platform_fee_revenue"
    PAYPAL_RAIL_EXPENSE = "paypal_rail_expense"


class SettlementDecision(str, Enum):
    """Deterministic settlement outcomes from the arbitration engine."""
    FULL_CAPTURE = "FULL_CAPTURE"
    PARTIAL_CAPTURE = "PARTIAL_CAPTURE"
    VOID_AUTHORIZATION = "VOID_AUTHORIZATION"


class SettlementSplitResult(BaseModel):
    """
    Financial split breakdown in micro-dollars calculated from arbitration score.
    $1.00 USD = 1,000,000 micro-dollars. 1 cent = 10,000 micro-dollars.
    """
    decision: str = Field(
        ...,
        description="Settlement decision: FULL_CAPTURE, PARTIAL_CAPTURE, or VOID_AUTHORIZATION",
    )
    capture_micros: int = Field(
        ...,
        ge=0,
        description="Total micro-dollars to capture from PayPal authorization hold",
    )
    buyer_released_micros: int = Field(
        ...,
        ge=0,
        description="Total micro-dollars uncaptured and released back to buyer",
    )
    platform_fee_micros: int = Field(
        ...,
        ge=0,
        description="Protocol clearing fee in micro-dollars deducted from capture",
    )
    paypal_rail_fee_micros: int = Field(
        ...,
        ge=0,
        description="PayPal batch payout rail fee in micro-dollars (e.g., $0.25)",
    )
    worker_net_payout_micros: int = Field(
        ...,
        ge=0,
        description="Net micro-dollars disbursed to the worker agent",
    )

    @field_validator("decision")
    @classmethod
    def validate_decision(cls, v: str) -> str:
        valid_decisions = {d.value for d in SettlementDecision}
        if v not in valid_decisions:
            raise ValueError(f"Invalid settlement decision '{v}'. Expected one of {valid_decisions}")
        return v

    @property
    def capture_usd(self) -> str:
        """Formatted USD string for captured amount."""
        return f"{Decimal(self.capture_micros) / Decimal(1_000_000):.2f}"

    @property
    def buyer_released_usd(self) -> str:
        """Formatted USD string for released hold amount."""
        return f"{Decimal(self.buyer_released_micros) / Decimal(1_000_000):.2f}"

    @property
    def platform_fee_usd(self) -> str:
        """Formatted USD string for platform fee."""
        return f"{Decimal(self.platform_fee_micros) / Decimal(1_000_000):.2f}"

    @property
    def paypal_rail_fee_usd(self) -> str:
        """Formatted USD string for PayPal payout rail fee."""
        return f"{Decimal(self.paypal_rail_fee_micros) / Decimal(1_000_000):.2f}"

    @property
    def worker_net_payout_usd(self) -> str:
        """Formatted USD string for net worker payout."""
        return f"{Decimal(self.worker_net_payout_micros) / Decimal(1_000_000):.2f}"


class DoubleEntryLedgerEntry(BaseModel):
    """
    Immutable journal entry for the double-entry accounting ledger.
    Every transaction strictly transfers funds between a debit and credit account.
    """
    entry_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique UUID identifier for this ledger journal entry",
    )
    debit_account: str = Field(
        ...,
        description="Account receiving the debit (asset increase or liability/equity decrease)",
    )
    credit_account: str = Field(
        ...,
        description="Account receiving the credit (liability/equity increase or asset decrease)",
    )
    amount_micros: int = Field(
        ...,
        gt=0,
        description="Transaction amount in integer micro-dollars. Must be strictly positive.",
    )
    reference_id: str = Field(
        ...,
        description="External reference identifier (e.g. agreement_id, capture_id, payout_batch_id)",
    )
    currency: str = Field(
        default="USD",
        description="ISO 4217 Currency code (default 'USD')",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when this ledger entry was created",
    )
    description: Optional[str] = Field(
        default=None,
        description="Human-readable ledger memo describing the financial transfer",
    )

    @model_validator(mode="after")
    def validate_accounts_distinct(self) -> "DoubleEntryLedgerEntry":
        if self.debit_account == self.credit_account:
            raise ValueError(
                f"Debit account '{self.debit_account}' and Credit account '{self.credit_account}' "
                "must be distinct in a double-entry transaction."
            )
        return self


class SlaContractCreate(BaseModel):
    """Request payload for creating and drafting an autonomous agent SLA contract."""
    agreement_id: Optional[str] = Field(
        default=None,
        description="Unique SLA contract identifier. Generated if not supplied.",
    )
    buyer_agent_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Identifier of the buyer autonomous agent hiring the worker",
    )
    worker_agent_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Identifier of the worker autonomous agent executing the task",
    )
    worker_paypal_email: str = Field(
        ...,
        min_length=3,
        max_length=254,
        description="PayPal email address of the worker agent receiver",
    )
    budget_micros: int = Field(
        ...,
        gt=0,
        description="Agreed escrow budget in integer micro-dollars ($1.00 USD = 1,000,000 micros)",
    )
    task_description: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        description="Description of the task requirements and SLA specifications",
    )
    timeout_seconds: int = Field(
        default=3600,
        gt=0,
        le=259200,  # Max 72 hours (PayPal honor window)
        description="Execution TTL in seconds before contract expires (default 3600s / 1 hr)",
    )
    vault_id: Optional[str] = Field(
        default=None,
        description="Optional PayPal Vault payment token for pre-authorized autonomous task billing",
    )
    buyer_signature: Optional[str] = Field(
        default=None,
        description="Optional Ed25519 signature from buyer agent",
    )
    worker_signature: Optional[str] = Field(
        default=None,
        description="Optional Ed25519 signature from worker agent",
    )
    buyer_public_key: Optional[str] = Field(
        default=None,
        description="Optional Buyer Ed25519 public key hex or did:key",
    )
    worker_public_key: Optional[str] = Field(
        default=None,
        description="Optional Worker Ed25519 public key hex or did:key",
    )

    @field_validator("worker_paypal_email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        v = v.strip()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError(f"Invalid email address '{v}'")
        return v


class SlaContractResponse(BaseModel):
    """Full SLA contract representation including financial status and PayPal IDs."""
    agreement_id: str = Field(..., description="Unique SLA contract identifier")
    buyer_agent_id: str = Field(..., description="Identifier of the buyer agent")
    worker_agent_id: str = Field(..., description="Identifier of the worker agent")
    worker_paypal_email: str = Field(..., description="PayPal receiver email for payout")
    budget_micros: int = Field(..., description="Agreed escrow budget in micro-dollars")
    task_description: str = Field(..., description="Task requirements and SLA description")
    timeout_seconds: int = Field(..., description="Execution timeout in seconds")
    fsm_state: FsmState = Field(..., description="Current state in the 11-state financial FSM")
    paypal_order_id: Optional[str] = Field(default=None, description="PayPal v2 Order ID")
    paypal_authorization_id: Optional[str] = Field(default=None, description="PayPal Authorization Hold ID")
    paypal_capture_id: Optional[str] = Field(default=None, description="PayPal Capture ID if captured")
    paypal_payout_batch_id: Optional[str] = Field(default=None, description="PayPal Batch Payout ID if disbursed")
    verification_score: Optional[float] = Field(default=None, description="Composite arbitration score [0.0, 1.0]")
    created_at: datetime = Field(..., description="Contract creation timestamp")
    updated_at: datetime = Field(..., description="Last state update timestamp")
    budget_usd: Optional[str] = Field(default=None, description="Budget in USD string format")
    vault_id: Optional[str] = Field(default=None, description="Optional PayPal Vault payment token ID")
    hash_chain_head: Optional[str] = Field(default=None, description="SHA-256 hash head of the audit trail")
    merkle_root: Optional[str] = Field(default=None, description="7-leaf Merkle root hash upon settlement")
    buyer_signature: Optional[str] = Field(default=None, description="Buyer Ed25519 signature")
    worker_signature: Optional[str] = Field(default=None, description="Worker Ed25519 signature")

    @model_validator(mode="after")
    def set_budget_usd(self) -> "SlaContractResponse":
        if self.budget_usd is None and self.budget_micros is not None:
            self.budget_usd = f"{Decimal(self.budget_micros) / Decimal(1_000_000):.2f}"
        return self


class ArbitrationReport(BaseModel):
    """Result of multi-vector SLA verification arbiter."""
    agreement_id: str = Field(..., description="SLA contract agreement identifier")
    s_pytest: float = Field(..., ge=0.0, le=1.0, description="Test suite pass ratio [0.0, 1.0]")
    s_ast: float = Field(..., ge=0.0, le=1.0, description="AST security scan score (1.0 clean, 0.0 violated)")
    s_semantic: float = Field(..., ge=0.0, le=1.0, description="Semantic / rubric evaluation score [0.0, 1.0]")
    composite_score: float = Field(..., ge=0.0, le=1.0, description="Final weighted composite score S in [0.0, 1.0]")
    passed_tests: int = Field(..., ge=0, description="Number of passing unit tests")
    total_tests: int = Field(..., ge=0, description="Total number of evaluated unit tests")
    ast_safe: bool = Field(..., description="Whether source code passed AST security checks")
    ast_violations: list[str] = Field(default_factory=list, description="List of detected AST violations")
    stdout: str = Field(default="", description="Captured test runner stdout/stderr")
    settlement: SettlementSplitResult = Field(..., description="Calculated financial settlement split")
    test_assertions: list["TestAssertionItem"] = Field(default_factory=list, description="List of individual test assertions")
    scanned_imports: list[str] = Field(default_factory=list, description="All module imports parsed from code")


class TestAssertionItem(BaseModel):
    """Individual unit test assertion result for master-detail inspection."""
    name: str = Field(..., description="Name of the test function or assertion")
    status: str = Field(..., description="Test outcome status: PASSED or FAILED")
    duration_ms: float = Field(default=0.0, description="Execution duration in milliseconds")
    error_message: Optional[str] = Field(default=None, description="Captured failure traceback or error message")


class AstSecurityAudit(BaseModel):
    """Static AST security report breakdown for master-detail inspection."""
    is_clean: bool = Field(..., description="True if deliverable has zero security perimeter violations")
    scanned_imports: list[str] = Field(default_factory=list, description="All module imports discovered during AST traversal")
    blocked_calls: list[str] = Field(default_factory=list, description="Prohibited built-in calls or blacklisted modules flagged")
    score: float = Field(default=1.0, ge=0.0, le=1.0, description="AST security score (1.0 = clean, 0.0 = hard veto)")


class LedgerJournalLeg(BaseModel):
    """Double-entry journal leg formatted for master-detail table expansion."""
    debit_account: str = Field(..., description="Destination debit account")
    credit_account: str = Field(..., description="Source credit account")
    amount_micros: int = Field(..., description="Transaction amount in integer micro-dollars")
    amount_usd: str = Field(..., description="Formatted USD string representation")
    memo: str = Field(default="", description="Accounting memo / description")
    created_at: datetime = Field(..., description="Timestamp when journal leg was recorded")


class AgreementDetailResponse(SlaContractResponse):
    """
    AG Grid Master-Detail nested response rehydrated from SQLite.
    Includes test assertions, AST security scan details, and double-entry ledger legs.
    """
    test_assertions: list[TestAssertionItem] = Field(
        default_factory=list,
        description="Nested child collection of test case assertions"
    )
    ast_security_audit: AstSecurityAudit = Field(
        default_factory=lambda: AstSecurityAudit(is_clean=True, scanned_imports=[], blocked_calls=[], score=1.0),
        description="Static analysis AST security report"
    )
    ledger_journal: list[LedgerJournalLeg] = Field(
        default_factory=list,
        description="Double-entry journal legs recorded for this contract"
    )
    transitions: list[Dict[str, Any]] = Field(
        default_factory=list,
        description="FSM state transition history records"
    )
    arbitration_report: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Raw arbitration evaluation report payload"
    )
    audit_bundle: Optional[Dict[str, Any]] = Field(
        default=None,
        description="7-leaf Merkle verification bundle"
    )


class VaultVelocityModel(BaseModel):
    vault_id: str = Field(..., description="PayPal Vault payment token / setup token ID")
    buyer_did: str = Field(..., description="Ed25519 DID of the buyer agent")
    max_amount_per_task_micros: int = Field(default=25_000_000, description="Max allowed per task in micros")
    max_daily_budget_micros: int = Field(default=100_000_000, description="Max allowed daily budget in micros")
    current_daily_spent_micros: int = Field(default=0, description="Running sum of captured micros in 24h rolling window")
    window_reset_timestamp: int = Field(default=0, description="Epoch timestamp when 24h budget window resets")
    consecutive_void_count: int = Field(default=0, description="Consecutive AST voids count")
    is_frozen: int = Field(default=0, description="0 = active, 1 = frozen by circuit breaker")


# Re-export v2 models for Module 1 & 4
from backend.app.schemas_v2 import (
    AgentIdentity,
    SlaContractV2,
    A2ANegotiateRequest,
    VerifyIdentityRequest,
    MerkleProofResponse,
)


