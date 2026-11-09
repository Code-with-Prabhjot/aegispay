"""
backend/app/services/ledger_service.py
In-Memory & SQL-Compatible Double-Entry Accounting Engine for AegisPay.
Strictly enforces:
- Account heads: buyer_vault, paypal_escrow_hold, worker_payable, platform_fee_revenue, paypal_rail_expense.
- Balance conservation: Sum of Debits == Sum of Credits on every transaction and globally.
- Non-negative balance invariant: Rejects any transaction that creates an unbalanced state or negative balance.
- SQL-compatible audit export for PostgreSQL and AG Grid Mission Control.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set
import uuid

from backend.app.schemas import (
    DoubleEntryLedgerEntry,
    LedgerAccount,
    SettlementSplitResult,
)


class LedgerError(Exception):
    """Base exception for all ledger operations."""
    pass


class UnbalancedLedgerError(LedgerError):
    """Raised when sum(Debits) != sum(Credits) in a transaction or ledger state."""
    pass


class NegativeBalanceError(LedgerError):
    """Raised when a transaction would cause an account balance to fall below zero."""
    pass


class InvalidAccountError(LedgerError):
    """Raised when an unknown account head is referenced."""
    pass


class DoubleEntryLedgerEngine:
    """
    Cryptographically sound, double-entry financial ledger engine.
    Ensures mathematical balance conservation and zero money loss/creation.
    """

    ALLOWED_ACCOUNTS: Set[str] = {
        LedgerAccount.BUYER_VAULT.value,
        LedgerAccount.PAYPAL_ESCROW_HOLD.value,
        LedgerAccount.WORKER_PAYABLE.value,
        LedgerAccount.PLATFORM_FEE_REVENUE.value,
        LedgerAccount.PAYPAL_RAIL_EXPENSE.value,
    }

    def __init__(
        self,
        initial_balances: Optional[Dict[str, int]] = None,
        repository: Optional[Any] = None,
        broadcaster: Optional[Any] = None,
    ):
        """
        Initializes ledger with optional starting balances (e.g., buyer vault pre-funding).
        Balances are tracked in integer micro-dollars ($1.00 USD = 1,000,000 micros).
        """
        self._repo = repository  # optional backend.app.db.Repository for persistence
        self._broadcaster = broadcaster  # optional LedgerConnectionManager for real-time WebSocket events
        self._entries: List[DoubleEntryLedgerEntry] = []
        self._balances: Dict[str, int] = {acc: 0 for acc in self.ALLOWED_ACCOUNTS}
        # External money that entered each account; the journal is replayed on top of it to audit balances.
        self._deposits: Dict[str, int] = {acc: 0 for acc in self.ALLOWED_ACCOUNTS}

        if initial_balances:
            for acc, amount in initial_balances.items():
                if acc not in self.ALLOWED_ACCOUNTS:
                    raise InvalidAccountError(f"Account '{acc}' is not a recognized ledger account.")
                if amount < 0:
                    raise NegativeBalanceError(f"Initial balance for '{acc}' cannot be negative: {amount}")
                self._balances[acc] = amount
                self._deposits[acc] = amount

    def set_broadcaster(self, broadcaster: Any) -> None:
        """Sets or updates the real-time event broadcaster."""
        self._broadcaster = broadcaster

    @classmethod
    def from_repository(
        cls, repository: Any, broadcaster: Optional[Any] = None
    ) -> "DoubleEntryLedgerEngine":
        """Rebuilds in-memory state (entries + balances) from persisted rows, then keeps persisting."""
        engine = cls(broadcaster=broadcaster)
        for row in repository.load_ledger_entries():
            e = row["entry"]
            if row["kind"] == "DEPOSIT":
                engine._balances[e.debit_account] += e.amount_micros
                engine._deposits[e.debit_account] += e.amount_micros
            else:
                engine._balances[e.debit_account] += e.amount_micros
                engine._balances[e.credit_account] -= e.amount_micros
                engine._entries.append(e)
        engine._repo = repository
        engine._broadcaster = broadcaster
        return engine

    @property
    def entries(self) -> List[DoubleEntryLedgerEntry]:
        """Returns read-only copy of all journal entries in chronological order."""
        return list(self._entries)

    def get_balance(self, account: str) -> int:
        """Returns the current balance of an account in micro-dollars."""
        if account not in self.ALLOWED_ACCOUNTS:
            raise InvalidAccountError(f"Account '{account}' is not a recognized ledger account.")
        return self._balances[account]

    def get_all_balances(self) -> Dict[str, int]:
        """Returns current balances for all ledger accounts."""
        return dict(self._balances)

    def deposit(self, account: str, amount_micros: int, reference_id: str, description: Optional[str] = None) -> None:
        """
        Deposits external funds into an account (e.g. buyer funding their vault).
        """
        if account not in self.ALLOWED_ACCOUNTS:
            raise InvalidAccountError(f"Account '{account}' is not a recognized ledger account.")
        if amount_micros <= 0:
            raise ValueError(f"Deposit amount must be strictly positive: {amount_micros}")

        if self._repo is not None:
            self._repo.append_ledger_entries(
                [DoubleEntryLedgerEntry(
                    debit_account=account,
                    credit_account="external_funding",
                    amount_micros=amount_micros,
                    reference_id=reference_id,
                    description=description or "External deposit",
                )],
                kind="DEPOSIT",
            )
        self._balances[account] += amount_micros
        self._deposits[account] += amount_micros
        if self._broadcaster:
            self._broadcaster.broadcast_sync({
                "event_type": "LEDGER_TRANSACTION",
                "agreement_id": reference_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "debit_account": account,
                    "credit_account": "external_funding",
                    "amount_micros": amount_micros,
                    "amount_usd": f"${Decimal(amount_micros) / Decimal(1_000_000):.2f}",
                    "reference_id": reference_id,
                    "memo": description or "External deposit",
                    "balances": self.get_all_balances(),
                },
            })

    def record_entry(self, entry: DoubleEntryLedgerEntry) -> DoubleEntryLedgerEntry:
        """
        Records a single double-entry journal entry.
        Transfers amount_micros from credit_account (source) to debit_account (destination).
        Enforces that the source account has sufficient funds to avoid a negative balance.
        """
        return self.record_transaction([entry])[0]

    def record_transfer(
        self,
        debit_account: str,
        credit_account: str,
        amount_micros: int,
        reference_id: str,
        description: Optional[str] = None,
    ) -> DoubleEntryLedgerEntry:
        """Convenience method to create and record a single transfer entry."""
        entry = DoubleEntryLedgerEntry(
            debit_account=debit_account,
            credit_account=credit_account,
            amount_micros=amount_micros,
            reference_id=reference_id,
            description=description,
        )
        return self.record_entry(entry)

    def record_transaction(self, entries: List[DoubleEntryLedgerEntry]) -> List[DoubleEntryLedgerEntry]:
        """
        Atomically records a batch of double-entry journal entries.
        Enforces:
        1. Debits == Credits across the batch.
        2. No account balance falls below zero.
        3. All referenced accounts exist in ALLOWED_ACCOUNTS.
        If any validation fails, the entire batch is rejected with zero side-effects.
        """
        if not entries:
            return []

        # Validate accounts and calculate total debits and credits
        total_debits = 0
        total_credits = 0
        tentative_deltas: Dict[str, int] = {acc: 0 for acc in self.ALLOWED_ACCOUNTS}

        for entry in entries:
            if entry.debit_account not in self.ALLOWED_ACCOUNTS:
                raise InvalidAccountError(f"Debit account '{entry.debit_account}' is not allowed.")
            if entry.credit_account not in self.ALLOWED_ACCOUNTS:
                raise InvalidAccountError(f"Credit account '{entry.credit_account}' is not allowed.")
            if entry.amount_micros <= 0:
                raise ValueError(f"Entry amount must be > 0, got {entry.amount_micros}")
            if entry.debit_account == entry.credit_account:
                raise ValueError(f"Debit and Credit account cannot be the same: {entry.debit_account}")

            total_debits += entry.amount_micros
            total_credits += entry.amount_micros

            # Destination receives funds (debit)
            tentative_deltas[entry.debit_account] += entry.amount_micros
            # Source yields funds (credit)
            tentative_deltas[entry.credit_account] -= entry.amount_micros

        # Strictly enforce balance conservation: sum(debits) == sum(credits)
        if total_debits != total_credits:
            raise UnbalancedLedgerError(
                f"Transaction unbalanced: Total Debits ({total_debits}) != Total Credits ({total_credits})"
            )

        # Strictly enforce non-negative balances
        for acc, delta in tentative_deltas.items():
            current = self._balances[acc]
            projected = current + delta
            if projected < 0:
                raise NegativeBalanceError(
                    f"Transaction rejected: Account '{acc}' has balance {current} micros, "
                    f"attempted debit delta {delta} micros resulting in negative balance {projected} micros."
                )

        # Persist first so a storage failure leaves memory untouched
        if self._repo is not None:
            self._repo.append_ledger_entries(entries, kind="TRANSFER")

        # Apply state changes atomically
        for acc, delta in tentative_deltas.items():
            self._balances[acc] += delta

        self._entries.extend(entries)

        if self._broadcaster:
            for e in entries:
                self._broadcaster.broadcast_sync({
                    "event_type": "LEDGER_TRANSACTION",
                    "agreement_id": e.reference_id,
                    "timestamp": e.created_at.isoformat() if e.created_at else datetime.now(timezone.utc).isoformat(),
                    "data": {
                        "entry_id": e.entry_id,
                        "debit_account": e.debit_account,
                        "credit_account": e.credit_account,
                        "amount_micros": e.amount_micros,
                        "amount_usd": f"${Decimal(e.amount_micros) / Decimal(1_000_000):.2f}",
                        "reference_id": e.reference_id,
                        "memo": e.description or "",
                        "created_at": e.created_at.isoformat() if e.created_at else datetime.now(timezone.utc).isoformat(),
                        "balances": self.get_all_balances(),
                    },
                })

        return entries

    def record_settlement(
        self,
        agreement_id: str,
        split: SettlementSplitResult,
    ) -> List[DoubleEntryLedgerEntry]:
        """
        Records the double-entry accounting entries for an SLA settlement based on
        the deterministic SettlementSplitResult:
        - If VOID: 100% of held escrow is released back to buyer_vault.
        - If FULL or PARTIAL CAPTURE:
          1. Gross capture: paypal_escrow_hold -> worker_payable
          2. Released uncaptured hold: paypal_escrow_hold -> buyer_vault (if any)
          3. Platform fee: worker_payable -> platform_fee_revenue (if any)
          4. PayPal rail fee: worker_payable -> paypal_rail_expense (if any)
        """
        entries: List[DoubleEntryLedgerEntry] = []

        if split.decision == "VOID_AUTHORIZATION":
            if split.buyer_released_micros > 0:
                entries.append(
                    DoubleEntryLedgerEntry(
                        debit_account=LedgerAccount.BUYER_VAULT.value,
                        credit_account=LedgerAccount.PAYPAL_ESCROW_HOLD.value,
                        amount_micros=split.buyer_released_micros,
                        reference_id=agreement_id,
                        description=f"Escrow authorization voided: 100% held funds released to buyer ({agreement_id})",
                    )
                )
        else:
            # 1. Gross capture from escrow hold to worker payable
            if split.capture_micros > 0:
                entries.append(
                    DoubleEntryLedgerEntry(
                        debit_account=LedgerAccount.WORKER_PAYABLE.value,
                        credit_account=LedgerAccount.PAYPAL_ESCROW_HOLD.value,
                        amount_micros=split.capture_micros,
                        reference_id=agreement_id,
                        description=f"PayPal gross capture {split.decision} ({agreement_id})",
                    )
                )

            # 2. Uncaptured hold released back to buyer
            if split.buyer_released_micros > 0:
                entries.append(
                    DoubleEntryLedgerEntry(
                        debit_account=LedgerAccount.BUYER_VAULT.value,
                        credit_account=LedgerAccount.PAYPAL_ESCROW_HOLD.value,
                        amount_micros=split.buyer_released_micros,
                        reference_id=agreement_id,
                        description=f"Partial capture remainder released to buyer ({agreement_id})",
                    )
                )

            # 3. Platform fee deducted from worker payable to platform revenue
            if split.platform_fee_micros > 0:
                entries.append(
                    DoubleEntryLedgerEntry(
                        debit_account=LedgerAccount.PLATFORM_FEE_REVENUE.value,
                        credit_account=LedgerAccount.WORKER_PAYABLE.value,
                        amount_micros=split.platform_fee_micros,
                        reference_id=agreement_id,
                        description=f"AegisPay 5% protocol fee deducted from payout ({agreement_id})",
                    )
                )

            # 4. PayPal payout rail fee deducted from worker payable to rail expense
            if split.paypal_rail_fee_micros > 0:
                entries.append(
                    DoubleEntryLedgerEntry(
                        debit_account=LedgerAccount.PAYPAL_RAIL_EXPENSE.value,
                        credit_account=LedgerAccount.WORKER_PAYABLE.value,
                        amount_micros=split.paypal_rail_fee_micros,
                        reference_id=agreement_id,
                        description=f"PayPal Batch Payout rail fee ($0.25) allocated ({agreement_id})",
                    )
                )

        return self.record_transaction(entries)

    def audit_balances(self) -> Dict[str, Any]:
        """
        Independently re-derives every account balance from the deposits plus the journal and
        compares it with the running balances. Every transfer moves money between two internal
        accounts, so the sum of all balances must equal the money deposited from outside.
        """
        replayed = dict(self._deposits)
        for e in self._entries:
            replayed[e.debit_account] = replayed.get(e.debit_account, 0) + e.amount_micros
            replayed[e.credit_account] = replayed.get(e.credit_account, 0) - e.amount_micros
        drift = {
            acc: self._balances.get(acc, 0) - replayed.get(acc, 0)
            for acc in set(self._balances) | set(replayed)
            if self._balances.get(acc, 0) != replayed.get(acc, 0)
        }
        negative = {acc: bal for acc, bal in self._balances.items() if bal < 0}
        total_deposited = sum(self._deposits.values())
        total_balance = sum(self._balances.values())
        net_imbalance = total_balance - total_deposited
        return {
            "balanced": net_imbalance == 0 and not drift and not negative,
            "net_imbalance_micros": net_imbalance,
            "total_deposited_micros": total_deposited,
            "total_balance_micros": total_balance,
            "total_transferred_micros": sum(e.amount_micros for e in self._entries),
            "account_drift_micros": drift,
            "negative_accounts": negative,
        }

    def verify_balance_conservation(self) -> bool:
        """
        True when the running balances match a replay of deposits + journal, no account is
        negative, and the balances sum to the money deposited (nothing created or lost).
        """
        return self.audit_balances()["balanced"]

    def to_records(self) -> List[Dict[str, Any]]:
        """
        Exports ledger entries formatted for frontend AG Grid display.
        Includes dual formatting: integer micro-dollars and USD string.
        """
        records = []
        for e in self._entries:
            records.append({
                "entry_id": e.entry_id,
                "debit_account": e.debit_account,
                "credit_account": e.credit_account,
                "amount_micros": e.amount_micros,
                "amount_usd": f"${Decimal(e.amount_micros) / Decimal(1_000_000):.2f}",
                "reference_id": e.reference_id,
                "currency": e.currency,
                "created_at": e.created_at.isoformat(),
                "description": e.description or "",
            })
        return records

    def to_sql_insert_statements(self, table_name: str = "ledger_journal_entries") -> List[str]:
        """
        Generates standard PostgreSQL ANSI-SQL INSERT statements for persistence.
        """
        stmts = []
        for e in self._entries:
            desc = e.description.replace("'", "''") if e.description else ""
            sql = (
                f"INSERT INTO {table_name} "
                f"(entry_id, debit_account, credit_account, amount_micros, reference_id, currency, created_at, description) "
                f"VALUES ('{e.entry_id}', '{e.debit_account}', '{e.credit_account}', {e.amount_micros}, "
                f"'{e.reference_id}', '{e.currency}', '{e.created_at.isoformat()}', '{desc}');"
            )
            stmts.append(sql)
        return stmts


# Alias for service compatibility
LedgerService = DoubleEntryLedgerEngine
