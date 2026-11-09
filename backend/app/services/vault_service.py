"""
backend/app/services/vault_service.py
PayPal Vaulting v3 & Velocity Circuit Breaker Engine for autonomous agent escrow.
Guards pre-authorized draws with mathematical velocity constraints and AST void circuit breakers.
"""

from dataclasses import dataclass
import time
from typing import Any, Callable, Dict, Optional, Tuple

from backend.app.db import Repository, VaultVelocityRecord
from backend.app.services import fsm


VAULT_LOCKED_CIRCUIT_BREAKER = "VAULT_LOCKED_CIRCUIT_BREAKER"
CIRCUIT_BREAKER_ACTIVE = "CIRCUIT_BREAKER_ACTIVE"


class VaultCircuitBreakerError(fsm.FsmError):
    """Raised when vault velocity or circuit breaker check fails."""

    def __init__(self, reason: str, vault_id: Optional[str] = None):
        self.reason = reason
        self.vault_id = vault_id
        self.status = VAULT_LOCKED_CIRCUIT_BREAKER
        super().__init__(f"Vault velocity circuit breaker rejected: {reason}")


def _resolve_repo(repo: Optional[Repository] = None) -> Repository:
    if repo is not None:
        return repo
    return Repository.from_url()


def check_vault_allowance(
    vault_id: str,
    requested_amount_micros: int,
    repo: Optional[Repository] = None,
    now_epoch: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    Evaluates whether an escrow authorization draw against a buyer vault is allowed.
    - If is_frozen == 1: reject with CIRCUIT_BREAKER_ACTIVE
    - Resets current_daily_spent_micros if current time >= window_reset_timestamp
    - Verifies requested_amount_micros <= max_amount_per_task_micros
    - Verifies current_daily_spent_micros + requested_amount_micros <= max_daily_budget_micros
    """
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    r = _resolve_repo(repo)
    vault = r.get_vault(vault_id)
    if vault is None:
        return False, "VAULT_NOT_FOUND"

    if vault.is_frozen == 1:
        return False, "CIRCUIT_BREAKER_ACTIVE"

    # Rolling 24h window reset check (24 hours = 86,400 seconds)
    if vault.window_reset_timestamp == 0 or now >= vault.window_reset_timestamp:
        vault.current_daily_spent_micros = 0
        vault.window_reset_timestamp = now + 86400
        r.save_vault(vault)

    if requested_amount_micros <= 0:
        return False, "INVALID_AMOUNT"

    if requested_amount_micros > vault.max_amount_per_task_micros:
        return False, "EXCEEDS_TASK_LIMIT"

    if vault.current_daily_spent_micros + requested_amount_micros > vault.max_daily_budget_micros:
        return False, "EXCEEDS_DAILY_BUDGET"

    return True, "ALLOWANCE_APPROVED"


def record_vault_settlement(
    vault_id: str,
    captured_amount_micros: int,
    is_void: bool,
    repo: Optional[Repository] = None,
    now_epoch: Optional[int] = None,
) -> None:
    """
    Updates the vault velocity state upon agreement settlement or void.
    - On capture: increments current_daily_spent_micros, resets consecutive_void_count to 0.
    - On void: increments consecutive_void_count; if >= 3, trips circuit breaker (is_frozen = 1).
    """
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    r = _resolve_repo(repo)
    vault = r.get_vault(vault_id)
    if vault is None:
        return

    if is_void:
        vault.consecutive_void_count += 1
        if vault.consecutive_void_count >= 3:
            vault.is_frozen = 1
    else:
        vault.consecutive_void_count = 0
        if vault.window_reset_timestamp == 0 or now >= vault.window_reset_timestamp:
            vault.current_daily_spent_micros = 0
            vault.window_reset_timestamp = now + 86400
        vault.current_daily_spent_micros += max(0, captured_amount_micros)

    r.save_vault(vault)


def reserve_vault_funds(
    vault_id: str,
    amount_micros: int,
    repo: Optional[Repository] = None,
    now_epoch: Optional[int] = None,
) -> None:
    """
    Counts an open escrow hold against the rolling daily budget. Without this the limit only sees
    captured spend, so any number of contracts can be opened against the same unspent allowance.
    """
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    r = _resolve_repo(repo)
    vault = r.get_vault(vault_id)
    if vault is None:
        return
    if vault.window_reset_timestamp == 0 or now >= vault.window_reset_timestamp:
        vault.current_daily_spent_micros = 0
        vault.window_reset_timestamp = now + 86400
    vault.current_daily_spent_micros += max(0, amount_micros)
    r.save_vault(vault)


WINDOW_SECONDS = 86400


def reservation_epoch(contract: Any) -> Optional[int]:
    """When a contract's hold was reserved against its vault: the moment it reached
    PAYPAL_AUTH_LOCKED (create_contract and the authorization webhook both reserve then)."""
    for t in getattr(contract, "transitions", None) or []:
        if getattr(t, "to_state", None) == fsm.FsmState.PAYPAL_AUTH_LOCKED:
            return int(t.created_at.timestamp())
    created = getattr(contract, "created_at", None)
    return int(created.timestamp()) if created else None


def release_vault_funds(
    vault_id: str,
    amount_micros: int,
    repo: Optional[Repository] = None,
    reserved_at_epoch: Optional[int] = None,
) -> None:
    """
    Returns a reservation made by reserve_vault_funds (the hold settled, was voided or expired).
    A reservation from an earlier 24h window was already wiped when the window reset, so
    subtracting it again would eat into the new window's spend.
    """
    r = _resolve_repo(repo)
    vault = r.get_vault(vault_id)
    if vault is None:
        return
    if (
        reserved_at_epoch is not None
        and vault.window_reset_timestamp
        and reserved_at_epoch < vault.window_reset_timestamp - WINDOW_SECONDS
    ):
        return
    vault.current_daily_spent_micros = max(0, vault.current_daily_spent_micros - max(0, amount_micros))
    r.save_vault(vault)


def reset_vault_circuit_breaker(
    vault_id: str,
    repo: Optional[Repository] = None,
) -> Optional[VaultVelocityRecord]:
    """
    Resets the circuit breaker, unfreezing the vault and clearing consecutive voids.
    """
    r = _resolve_repo(repo)
    vault = r.get_vault(vault_id)
    if vault is None:
        return None
    vault.is_frozen = 0
    vault.consecutive_void_count = 0
    r.save_vault(vault)
    return vault


unfreeze_vault = reset_vault_circuit_breaker


def register_vault(
    vault_id: str,
    buyer_did: str,
    max_amount_per_task_micros: int = 25_000_000,
    max_daily_budget_micros: int = 100_000_000,
    current_daily_spent_micros: int = 0,
    window_reset_timestamp: int = 0,
    consecutive_void_count: int = 0,
    is_frozen: int = 0,
    repo: Optional[Repository] = None,
) -> VaultVelocityRecord:
    """
    Registers or updates a buyer agent vault velocity allowance record.
    """
    r = _resolve_repo(repo)
    record = VaultVelocityRecord(
        vault_id=vault_id,
        buyer_did=buyer_did,
        max_amount_per_task_micros=max_amount_per_task_micros,
        max_daily_budget_micros=max_daily_budget_micros,
        current_daily_spent_micros=current_daily_spent_micros,
        window_reset_timestamp=window_reset_timestamp,
        consecutive_void_count=consecutive_void_count,
        is_frozen=is_frozen,
    )
    r.save_vault(record)
    return record


class VaultService:
    """Class wrapper providing dependency-injected access to vault velocity operations."""

    def __init__(self, repo: Repository, clock: Optional[Callable[[], int]] = None):
        self.repo = repo
        self.clock = clock or (lambda: int(time.time()))

    def register_vault(
        self,
        vault_id: str,
        buyer_did: str,
        max_amount_per_task_micros: int = 25_000_000,
        max_daily_budget_micros: int = 100_000_000,
        current_daily_spent_micros: int = 0,
        window_reset_timestamp: int = 0,
        consecutive_void_count: int = 0,
        is_frozen: int = 0,
    ) -> VaultVelocityRecord:
        return register_vault(
            vault_id=vault_id,
            buyer_did=buyer_did,
            max_amount_per_task_micros=max_amount_per_task_micros,
            max_daily_budget_micros=max_daily_budget_micros,
            current_daily_spent_micros=current_daily_spent_micros,
            window_reset_timestamp=window_reset_timestamp,
            consecutive_void_count=consecutive_void_count,
            is_frozen=is_frozen,
            repo=self.repo,
        )

    def get_vault(self, vault_id: str) -> Optional[VaultVelocityRecord]:
        return self.repo.get_vault(vault_id)

    def check_vault_allowance(
        self, vault_id: str, requested_amount_micros: int, now_epoch: Optional[int] = None
    ) -> Tuple[bool, str]:
        now = self.clock() if now_epoch is None else now_epoch
        return check_vault_allowance(vault_id, requested_amount_micros, repo=self.repo, now_epoch=now)

    def record_vault_settlement(
        self, vault_id: str, captured_amount_micros: int, is_void: bool, now_epoch: Optional[int] = None
    ) -> None:
        now = self.clock() if now_epoch is None else now_epoch
        record_vault_settlement(vault_id, captured_amount_micros, is_void, repo=self.repo, now_epoch=now)

    def reset_circuit_breaker(self, vault_id: str) -> Optional[VaultVelocityRecord]:
        return reset_vault_circuit_breaker(vault_id, repo=self.repo)
