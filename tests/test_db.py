"""Repository / persistence tests (sqlite)."""

from datetime import datetime, timezone

import pytest

from backend.app.db import Repository, _sqlite_path
from backend.app.schemas import DoubleEntryLedgerEntry, FsmState as S
from backend.app.services import fsm
from backend.app.services.ledger_service import DoubleEntryLedgerEngine, NegativeBalanceError

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def repo(tmp_path):
    return Repository.from_url(f"sqlite:///{tmp_path}/t.db")


def contract(aid="agr_1"):
    c = fsm.ContractRecord(
        agreement_id=aid, buyer_agent_id="b", worker_agent_id="w",
        worker_paypal_email="w@example.com", budget_micros=5_000_000,
        task_description="task text", timeout_seconds=60, created_at=NOW, updated_at=NOW,
    )
    c.transitions.append(fsm.TransitionRecord(aid, None, S.AGREEMENT_DRAFTED, "drafted", NOW))
    return c


def test_url_parsing():
    assert _sqlite_path("sqlite:///./x.db") == "./x.db"
    assert _sqlite_path("sqlite:////tmp/x.db") == "/tmp/x.db"
    assert _sqlite_path("sqlite:///:memory:") == ":memory:"
    with pytest.raises(ValueError):
        _sqlite_path("postgresql://u@h/db")


def test_contract_roundtrip_and_transitions(repo):
    c = contract()
    repo.save_contract(c)
    fsm.transition(c, S.PAYPAL_AUTH_LOCKED, reason="locked", now=NOW)
    c.paypal_order_id = "ORD"
    c.report = {"a": 1}
    repo.save_contract(c)
    repo.save_contract(c)  # idempotent: no duplicate transitions
    got = repo.get_contract("agr_1")
    assert got.fsm_state == S.PAYPAL_AUTH_LOCKED
    assert got.paypal_order_id == "ORD" and got.report == {"a": 1}
    assert [t.to_state for t in got.transitions] == [S.AGREEMENT_DRAFTED, S.PAYPAL_AUTH_LOCKED]
    assert got.transitions[1].created_at == NOW
    assert repo.get_contract("nope") is None
    repo.save_contract(contract("agr_2"))
    assert len(repo.list_contracts()) == 2


def test_transaction_rollback(repo):
    with pytest.raises(RuntimeError):
        with repo.db.transaction():
            repo.save_contract(contract())
            raise RuntimeError("boom")
    assert repo.get_contract("agr_1") is None


def test_ledger_persist_and_reload(repo):
    led = DoubleEntryLedgerEngine(repository=repo)
    led.deposit("buyer_vault", 10_000_000, "ref", "fund")
    led.record_transfer("paypal_escrow_hold", "buyer_vault", 4_000_000, "ref", "hold")
    reloaded = DoubleEntryLedgerEngine.from_repository(repo)
    assert reloaded.get_all_balances() == led.get_all_balances()
    assert reloaded.get_balance("buyer_vault") == 6_000_000
    assert [e.entry_id for e in reloaded.entries] == [e.entry_id for e in led.entries]
    # reloaded engine keeps persisting
    reloaded.record_transfer("buyer_vault", "paypal_escrow_hold", 1_000_000, "ref")
    assert DoubleEntryLedgerEngine.from_repository(repo).get_balance("buyer_vault") == 7_000_000


def test_ledger_in_memory_api_unchanged_and_failed_tx_not_persisted(repo):
    mem = DoubleEntryLedgerEngine()
    mem.deposit("buyer_vault", 1_000_000, "r")
    mem.record_transfer("paypal_escrow_hold", "buyer_vault", 1_000_000, "r")
    assert mem.get_balance("paypal_escrow_hold") == 1_000_000

    led = DoubleEntryLedgerEngine(repository=repo)
    with pytest.raises(NegativeBalanceError):
        led.record_transfer("paypal_escrow_hold", "buyer_vault", 1, "r")
    assert repo.load_ledger_entries() == []


def test_webhook_event_dedupe(repo):
    assert repo.record_webhook_event("WH-1", "T", "b", {"x": 1}, NOW) is True
    assert repo.record_webhook_event("WH-1", "T", "b", {"x": 1}, NOW) is False
    repo.set_webhook_outcome("WH-1", "processed")
    assert repo.get_webhook_event("WH-1")["outcome"] == "processed"


def test_payout_upsert_and_lookup(repo):
    repo.save_contract(contract())
    repo.save_payout("p1", "agr_1", "BATCH", 100, "DISPATCHED", 1, NOW)
    repo.save_payout("p1", "agr_1", "BATCH", 100, "FAILED", 1, NOW)
    assert repo.get_payout_by_batch("BATCH")["status"] == "FAILED"
    assert repo.get_payout_for_agreement("agr_1")["payout_id"] == "p1"
