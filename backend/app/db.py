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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

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


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


@dataclass
class VaultVelocityRecord:
    vault_id: str
    buyer_did: str
    max_amount_per_task_micros: int = 25_000_000
    max_daily_budget_micros: int = 100_000_000
    current_daily_spent_micros: int = 0
    window_reset_timestamp: int = 0
    consecutive_void_count: int = 0
    is_frozen: int = 0

    @property
    def status(self) -> str:
        return "VAULT_LOCKED_CIRCUIT_BREAKER" if self.is_frozen == 1 else "ACTIVE"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vault_id": self.vault_id,
            "buyer_did": self.buyer_did,
            "max_amount_per_task_micros": self.max_amount_per_task_micros,
            "max_daily_budget_micros": self.max_daily_budget_micros,
            "current_daily_spent_micros": self.current_daily_spent_micros,
            "window_reset_timestamp": self.window_reset_timestamp,
            "consecutive_void_count": self.consecutive_void_count,
            "is_frozen": self.is_frozen,
            "status": self.status,
        }


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
            for col in (
                "vault_id VARCHAR(128)",
                "hash_chain_head VARCHAR(64)",
                "merkle_root VARCHAR(64)",
                "buyer_signature TEXT",
                "worker_signature TEXT",
                "test_suite TEXT",
                "test_suite_sha256 VARCHAR(64)",
            ):
                try:
                    self._conn.execute(f"ALTER TABLE contracts ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass
            try:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_hash_chain (
                        seq          BIGINT PRIMARY KEY,
                        agreement_id VARCHAR(128) NOT NULL REFERENCES contracts (agreement_id),
                        state        VARCHAR(40) NOT NULL,
                        prev_hash    VARCHAR(64) NOT NULL,
                        current_hash VARCHAR(64) NOT NULL,
                        payload_json TEXT,
                        created_at   TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_chain_agreement ON audit_hash_chain (agreement_id, seq)"
                )
            except sqlite3.OperationalError:
                pass

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
                    created_at, updated_at, vault_id, hash_chain_head, merkle_root,
                    buyer_signature, worker_signature, test_suite, test_suite_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (agreement_id) DO UPDATE SET
                    fsm_state = excluded.fsm_state,
                    paypal_order_id = excluded.paypal_order_id,
                    paypal_authorization_id = excluded.paypal_authorization_id,
                    paypal_capture_id = excluded.paypal_capture_id,
                    paypal_payout_batch_id = excluded.paypal_payout_batch_id,
                    verification_score = excluded.verification_score,
                    authorized_at = excluded.authorized_at,
                    report_json = excluded.report_json,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    vault_id = excluded.vault_id,
                    hash_chain_head = excluded.hash_chain_head,
                    merkle_root = excluded.merkle_root,
                    buyer_signature = excluded.buyer_signature,
                    worker_signature = excluded.worker_signature,
                    test_suite = excluded.test_suite,
                    test_suite_sha256 = excluded.test_suite_sha256
                """,
                (
                    c.agreement_id, c.buyer_agent_id, c.worker_agent_id, c.worker_paypal_email,
                    c.budget_micros, c.task_description, c.timeout_seconds, c.fsm_state.value,
                    c.paypal_order_id, c.paypal_authorization_id, c.paypal_capture_id,
                    c.paypal_payout_batch_id, c.verification_score, _iso(c.authorized_at),
                    json.dumps(c.report) if c.report is not None else None,
                    _iso(c.created_at), _iso(c.updated_at), getattr(c, "vault_id", None),
                    getattr(c, "hash_chain_head", None), getattr(c, "merkle_root", None),
                    getattr(c, "buyer_signature", None), getattr(c, "worker_signature", None),
                    getattr(c, "test_suite", None), getattr(c, "test_suite_sha256", None),
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
        def _get_val(key: str) -> Any:
            try:
                return r[key]
            except (IndexError, KeyError):
                return None

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
            vault_id=_get_val("vault_id"),
            hash_chain_head=_get_val("hash_chain_head"),
            merkle_root=_get_val("merkle_root"),
            buyer_signature=_get_val("buyer_signature"),
            worker_signature=_get_val("worker_signature"),
            test_suite=_get_val("test_suite"),
            test_suite_sha256=_get_val("test_suite_sha256"),
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

    def list_contracts_in_states(self, states: Iterable[Any]) -> List[ContractRecord]:
        """Every contract in the given FSM states, oldest first, with no row limit (sweeper use)."""
        values = [getattr(s, "value", s) for s in states]
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        rows = self.db.query(
            f"SELECT * FROM contracts WHERE fsm_state IN ({placeholders}) ORDER BY created_at ASC",
            values,
        )
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

    def get_webhook_events_for_agreement(self, agreement_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM webhook_events WHERE payload LIKE ? OR resource_id LIKE ? ORDER BY received_at ASC",
            (f"%{agreement_id}%", f"%{agreement_id}%"),
        )
        return [dict(r) for r in rows]

    # ---------------------------------------------------- audit hash chain (Module 4)
    def append_hash_chain_entry(
        self,
        agreement_id: str,
        state: str,
        prev_hash: str,
        current_hash: str,
        payload_json: Optional[str],
        created_at: datetime,
    ) -> None:
        with self.db.transaction():
            seq = self.db.query("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM audit_hash_chain")[0]["n"]
            self.db.execute(
                """
                INSERT INTO audit_hash_chain (seq, agreement_id, state, prev_hash, current_hash, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (seq, agreement_id, state, prev_hash, current_hash, payload_json, _iso(created_at)),
            )
            self.db.execute(
                "UPDATE contracts SET hash_chain_head = ? WHERE agreement_id = ?",
                (current_hash, agreement_id),
            )

    def get_hash_chain(self, agreement_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM audit_hash_chain WHERE agreement_id = ? ORDER BY seq ASC",
            (agreement_id,),
        )
        return [dict(r) for r in rows]

    def update_contract_proofs(
        self,
        agreement_id: str,
        hash_chain_head: Optional[str] = None,
        merkle_root: Optional[str] = None,
    ) -> None:
        with self.db.transaction():
            if hash_chain_head and merkle_root:
                self.db.execute(
                    "UPDATE contracts SET hash_chain_head = ?, merkle_root = ? WHERE agreement_id = ?",
                    (hash_chain_head, merkle_root, agreement_id),
                )
            elif hash_chain_head:
                self.db.execute(
                    "UPDATE contracts SET hash_chain_head = ? WHERE agreement_id = ?",
                    (hash_chain_head, agreement_id),
                )
            elif merkle_root:
                self.db.execute(
                    "UPDATE contracts SET merkle_root = ? WHERE agreement_id = ?",
                    (merkle_root, agreement_id),
                )

    # ------------------------------------------------------ buyer vault velocity
    def save_vault(self, v: VaultVelocityRecord) -> None:
        with self.db.transaction():
            self.db.execute(
                """
                INSERT INTO buyer_vault_velocity (
                    vault_id, buyer_did, max_amount_per_task_micros, max_daily_budget_micros,
                    current_daily_spent_micros, window_reset_timestamp, consecutive_void_count, is_frozen
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (vault_id) DO UPDATE SET
                    buyer_did = excluded.buyer_did,
                    max_amount_per_task_micros = excluded.max_amount_per_task_micros,
                    max_daily_budget_micros = excluded.max_daily_budget_micros,
                    current_daily_spent_micros = excluded.current_daily_spent_micros,
                    window_reset_timestamp = excluded.window_reset_timestamp,
                    consecutive_void_count = excluded.consecutive_void_count,
                    is_frozen = excluded.is_frozen
                """,
                (
                    v.vault_id,
                    v.buyer_did,
                    v.max_amount_per_task_micros,
                    v.max_daily_budget_micros,
                    v.current_daily_spent_micros,
                    v.window_reset_timestamp,
                    v.consecutive_void_count,
                    v.is_frozen,
                ),
            )

    def get_vault(self, vault_id: str) -> Optional[VaultVelocityRecord]:
        rows = self.db.query("SELECT * FROM buyer_vault_velocity WHERE vault_id = ?", (vault_id,))
        if not rows:
            return None
        r = rows[0]
        return VaultVelocityRecord(
            vault_id=r["vault_id"],
            buyer_did=r["buyer_did"],
            max_amount_per_task_micros=r["max_amount_per_task_micros"],
            max_daily_budget_micros=r["max_daily_budget_micros"],
            current_daily_spent_micros=r["current_daily_spent_micros"],
            window_reset_timestamp=r["window_reset_timestamp"],
            consecutive_void_count=r["consecutive_void_count"],
            is_frozen=r["is_frozen"],
        )

    def list_vaults(self) -> List[VaultVelocityRecord]:
        rows = self.db.query("SELECT * FROM buyer_vault_velocity ORDER BY vault_id")
        return [
            VaultVelocityRecord(
                vault_id=r["vault_id"],
                buyer_did=r["buyer_did"],
                max_amount_per_task_micros=r["max_amount_per_task_micros"],
                max_daily_budget_micros=r["max_daily_budget_micros"],
                current_daily_spent_micros=r["current_daily_spent_micros"],
                window_reset_timestamp=r["window_reset_timestamp"],
                consecutive_void_count=r["consecutive_void_count"],
                is_frozen=r["is_frozen"],
            )
            for r in rows
        ]

    def delete_vault(self, vault_id: str) -> None:
        self.db.execute("DELETE FROM buyer_vault_velocity WHERE vault_id = ?", (vault_id,))
