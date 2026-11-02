"""
backend/app/db.py
Thin repository layer over stdlib sqlite3 (no ORM). SQL is kept portable so the same
statements work against the PostgreSQL schema in backend/db/schema.sql (swap the driver and
the `?` placeholder in `Database._sql`).

DATABASE_URL: `sqlite:///./aegispay.db` (default), `sqlite:///:memory:`, `sqlite:////abs/path.db`.
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from backend.app.schemas import DoubleEntryLedgerEntry, FsmState
from backend.app.services.fsm import ContractRecord, TransitionRecord

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
DEFAULT_DATABASE_URL = "sqlite:///./aegispay.db"


def _sqlite_path(url: str) -> str:
    if not url.startswith("sqlite:"):
        raise ValueError(
            f"Unsupported DATABASE_URL '{url}': this build only ships the sqlite driver. "
            "Apply backend/db/schema.sql to PostgreSQL and add a driver to use it."
        )
    rest = url[len("sqlite:"):]
    if rest.startswith("///"):
        rest = rest[3:]
    elif rest.startswith("//"):
        rest = rest[2:]
    return rest or ":memory:"


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


class Database:
    """Single shared connection guarded by a re-entrant lock, with nestable transactions."""

    def __init__(self, url: str = DEFAULT_DATABASE_URL):
        self.url = url
        self.path = _sqlite_path(url)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        self._depth = 0

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Atomic block; nested blocks join the outermost transaction."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self._conn.execute("COMMIT")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()


class Repository:
    """Persistence operations for contracts, transitions, ledger, payouts and webhook events."""

    def __init__(self, db: Database):
        self.db = db

    @classmethod
    def from_url(cls, url: str = DEFAULT_DATABASE_URL) -> "Repository":
        db = Database(url)
        db.init_schema()
        return cls(db)

    # ------------------------------------------------------------------ contracts
    def save_contract(self, c: ContractRecord) -> None:
        """Upsert the contract and append any not-yet-persisted transitions."""
        with self.db.transaction():
            self.db.execute(
                """
                INSERT INTO contracts (agreement_id, buyer_agent_id, worker_agent_id,
                    worker_paypal_email, budget_micros, task_description, timeout_seconds,
                    fsm_state, paypal_order_id, paypal_authorization_id, paypal_capture_id,
                    paypal_payout_batch_id, verification_score, authorized_at, report_json,
                    created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (agreement_id) DO UPDATE SET
                    fsm_state = excluded.fsm_state,
                    paypal_order_id = excluded.paypal_order_id,
                    paypal_authorization_id = excluded.paypal_authorization_id,
                    paypal_capture_id = excluded.paypal_capture_id,
                    paypal_payout_batch_id = excluded.paypal_payout_batch_id,
                    verification_score = excluded.verification_score,
                    authorized_at = excluded.authorized_at,
                    report_json = excluded.report_json,
                    updated_at = excluded.updated_at
                """,
                (
                    c.agreement_id, c.buyer_agent_id, c.worker_agent_id, c.worker_paypal_email,
                    c.budget_micros, c.task_description, c.timeout_seconds, c.fsm_state.value,
                    c.paypal_order_id, c.paypal_authorization_id, c.paypal_capture_id,
                    c.paypal_payout_batch_id, c.verification_score, _iso(c.authorized_at),
                    json.dumps(c.report) if c.report is not None else None,
                    _iso(c.created_at), _iso(c.updated_at),
                ),
            )
            persisted = self.db.query(
                "SELECT COUNT(*) AS n FROM state_transitions WHERE agreement_id = ?",
                (c.agreement_id,),
            )[0]["n"]
            for t in c.transitions[persisted:]:
                self.add_transition(t)

    def add_transition(self, t: TransitionRecord) -> None:
        seq = self.db.query("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM state_transitions")[0]["n"]
        self.db.execute(
            "INSERT INTO state_transitions (seq, agreement_id, from_state, to_state, reason, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (seq, t.agreement_id, t.from_state.value if t.from_state else None,
             t.to_state.value, t.reason, _iso(t.created_at)),
        )

    def list_transitions(self, agreement_id: str) -> List[TransitionRecord]:
        rows = self.db.query(
            "SELECT * FROM state_transitions WHERE agreement_id = ? ORDER BY seq", (agreement_id,)
        )
        return [
            TransitionRecord(
                agreement_id=r["agreement_id"],
                from_state=FsmState(r["from_state"]) if r["from_state"] else None,
                to_state=FsmState(r["to_state"]),
                reason=r["reason"],
                created_at=_dt(r["created_at"]),
            )
            for r in rows
        ]

    def _row_to_contract(self, r: Any) -> ContractRecord:
        return ContractRecord(
            agreement_id=r["agreement_id"],
            buyer_agent_id=r["buyer_agent_id"],
            worker_agent_id=r["worker_agent_id"],
            worker_paypal_email=r["worker_paypal_email"],
            budget_micros=r["budget_micros"],
            task_description=r["task_description"],
            timeout_seconds=r["timeout_seconds"],
            fsm_state=FsmState(r["fsm_state"]),
            paypal_order_id=r["paypal_order_id"],
            paypal_authorization_id=r["paypal_authorization_id"],
            paypal_capture_id=r["paypal_capture_id"],
            paypal_payout_batch_id=r["paypal_payout_batch_id"],
            verification_score=r["verification_score"],
            authorized_at=_dt(r["authorized_at"]),
            report=json.loads(r["report_json"]) if r["report_json"] else None,
            created_at=_dt(r["created_at"]),
            updated_at=_dt(r["updated_at"]),
            transitions=self.list_transitions(r["agreement_id"]),
        )

    def get_contract(self, agreement_id: str) -> Optional[ContractRecord]:
        rows = self.db.query("SELECT * FROM contracts WHERE agreement_id = ?", (agreement_id,))
        return self._row_to_contract(rows[0]) if rows else None

    def get_contract_by_authorization(self, authorization_id: str) -> Optional[ContractRecord]:
        rows = self.db.query(
            "SELECT * FROM contracts WHERE paypal_authorization_id = ?", (authorization_id,)
        )
        return self._row_to_contract(rows[0]) if rows else None

    def list_contracts(self, limit: int = 200) -> List[ContractRecord]:
        rows = self.db.query("SELECT * FROM contracts ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._row_to_contract(r) for r in rows]

    # --------------------------------------------------------------------- ledger
    def append_ledger_entries(
        self, entries: Sequence[DoubleEntryLedgerEntry], kind: str = "TRANSFER"
    ) -> None:
        with self.db.transaction():
            seq = self.db.query("SELECT COALESCE(MAX(seq), 0) AS n FROM ledger_entries")[0]["n"]
            for e in entries:
                seq += 1
                self.db.execute(
                    "INSERT INTO ledger_entries (entry_id, seq, entry_kind, debit_account,"
                    " credit_account, amount_micros, reference_id, currency, description, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (e.entry_id, seq, kind, e.debit_account, e.credit_account, e.amount_micros,
                     e.reference_id, e.currency, e.description, _iso(e.created_at)),
                )

    def load_ledger_entries(self) -> List[Dict[str, Any]]:
        """Returns rows in insertion order as dicts: {'kind': ..., 'entry': DoubleEntryLedgerEntry}."""
        out = []
        for r in self.db.query("SELECT * FROM ledger_entries ORDER BY seq"):
            out.append({
                "kind": r["entry_kind"],
                "entry": DoubleEntryLedgerEntry(
                    entry_id=r["entry_id"],
                    debit_account=r["debit_account"],
                    credit_account=r["credit_account"],
                    amount_micros=r["amount_micros"],
                    reference_id=r["reference_id"],
                    currency=r["currency"],
                    description=r["description"],
                    created_at=_dt(r["created_at"]),
                ),
            })
        return out

    def get_ledger_entries_for_agreement(self, agreement_id: str) -> List[DoubleEntryLedgerEntry]:
        """Returns all double-entry ledger entries for a specific agreement in order."""
        out = []
        for r in self.db.query("SELECT * FROM ledger_entries WHERE reference_id = ? ORDER BY seq", (agreement_id,)):
            out.append(
                DoubleEntryLedgerEntry(
                    entry_id=r["entry_id"],
                    debit_account=r["debit_account"],
                    credit_account=r["credit_account"],
                    amount_micros=r["amount_micros"],
                    reference_id=r["reference_id"],
                    currency=r["currency"],
                    description=r["description"],
                    created_at=_dt(r["created_at"]),
                )
            )
        return out

    # -------------------------------------------------------------------- payouts
    def save_payout(self, payout_id: str, agreement_id: str, batch_id: str, amount_micros: int,
                    status: str, attempt: int, now: datetime) -> None:
        self.db.execute(
            """
            INSERT INTO payouts (payout_id, agreement_id, batch_id, amount_micros, status,
                                 attempt, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (payout_id) DO UPDATE SET
                batch_id = excluded.batch_id, status = excluded.status,
                attempt = excluded.attempt, updated_at = excluded.updated_at
            """,
            (payout_id, agreement_id, batch_id, amount_micros, status, attempt,
             _iso(now), _iso(now)),
        )

    def get_payout_by_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.query("SELECT * FROM payouts WHERE batch_id = ?", (batch_id,))
        return dict(rows[0]) if rows else None

    def get_payout_for_agreement(self, agreement_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM payouts WHERE agreement_id = ? ORDER BY attempt DESC", (agreement_id,)
        )
        return dict(rows[0]) if rows else None

    # ------------------------------------------------------------- webhook events
    def record_webhook_event(self, event_id: str, event_type: str, resource_id: Optional[str],
                             payload: Dict[str, Any], now: datetime) -> bool:
        """Insert the event; returns False when this event_id was already recorded (duplicate)."""
        cur = self.db.execute(
            "INSERT INTO webhook_events (event_id, event_type, resource_id, payload, received_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT (event_id) DO NOTHING",
            (event_id, event_type, resource_id, json.dumps(payload), _iso(now)),
        )
        return cur.rowcount == 1

    def set_webhook_outcome(self, event_id: str, outcome: str) -> None:
        self.db.execute("UPDATE webhook_events SET outcome = ? WHERE event_id = ?", (outcome, event_id))

    def delete_webhook_event(self, event_id: str) -> None:
        self.db.execute("DELETE FROM webhook_events WHERE event_id = ?", (event_id,))

    def get_webhook_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.query("SELECT * FROM webhook_events WHERE event_id = ?", (event_id,))
        return dict(rows[0]) if rows else None
