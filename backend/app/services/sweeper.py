"""
backend/app/services/sweeper.py
Module 3, Section 5.3: Automated 72-Hour Re-Authorization Sweeper for AegisPay.

PayPal authorizations guarantee funds during the initial 72-hour honor period.
For asynchronous, long-running agent tasks:
- Scans active contracts in PAYPAL_AUTH_LOCKED or SANDBOX_EXECUTING whose
  created_at timestamp is within 2 hours of honor period expiration (age >= 70 hours).
- If contract has an associated vaulted payment_token (PayPal Vaulting v3):
  * Automatically invokes reauthorize or voids aging authorization and issues fresh hold.
  * Records the re-authorization transaction in the double-entry journal and audit hash chain.
- If contract has no vaulted token and deadline has passed:
  * Gracefully voids authorization via PayPal API, transitions FSM to ESCROW_VOIDED_REFUNDED,
    and releases escrow hold back to the buyer vault.
"""

from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from backend.app.db import Repository
from backend.app.schemas import FsmState, LedgerAccount
from backend.app.services import fsm
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.merkle_audit import (
    compile_7leaf_bundle,
    compute_genesis_hash,
    compute_transition_hash,
)
from backend.app.services.orchestrator import extract_authorization_id

logger = logging.getLogger(__name__)

BUYER = LedgerAccount.BUYER_VAULT.value
ESCROW = LedgerAccount.PAYPAL_ESCROW_HOLD.value

ACTIVE_STATES = frozenset({FsmState.PAYPAL_AUTH_LOCKED, FsmState.SANDBOX_EXECUTING})


def _get_age_seconds(dt: Optional[datetime], now: datetime) -> float:
    if dt is None:
        return 0.0
    if dt.tzinfo is None and now.tzinfo is not None:
        dt = dt.replace(tzinfo=timezone.utc)
    elif dt.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds())


class ReauthorizationSweeper:
    """
    Automated 72-Hour Re-Authorization Sweeper service.
    Guarantees financial solvency and authorization freshness for long-running agent SLAs.
    """

    def __init__(
        self,
        repo: Repository,
        ledger: DoubleEntryLedgerEngine,
        engine: Any,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        broadcaster: Optional[Any] = None,
        threshold_hours: float = 70.0,
        honor_period_hours: float = 72.0,
    ):
        self.repo = repo
        self.ledger = ledger
        self.engine = engine
        self.clock = clock
        self.broadcaster = broadcaster
        self.threshold_hours = threshold_hours
        self.honor_period_hours = honor_period_hours

    def _get_active_contracts(self) -> List[fsm.ContractRecord]:
        all_contracts = self.repo.list_contracts(limit=1000)
        return [c for c in all_contracts if c.fsm_state in ACTIVE_STATES]

    def get_status(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Returns current health metrics of active authorizations:
        - total locked volume (micros and USD string)
        - oldest active hold (age in hours and agreement_id)
        - near-expiry count (age >= 70h)
        - overall solvency and balance conservation
        """
        now = now or self.clock()
        active = self._get_active_contracts()
        total_locked_micros = sum(c.budget_micros for c in active)

        oldest_age_hours: Optional[float] = None
        oldest_agreement_id: Optional[str] = None
        near_expiry_count = 0

        if active:
            for c in active:
                age_h = _get_age_seconds(c.created_at or c.authorized_at, now) / 3600.0
                if oldest_age_hours is None or age_h > oldest_age_hours:
                    oldest_age_hours = round(age_h, 2)
                    oldest_agreement_id = c.agreement_id
                if age_h >= self.threshold_hours:
                    near_expiry_count += 1

        is_solvent = self.ledger.verify_balance_conservation()

        return {
            "active_authorizations_count": len(active),
            "total_locked_volume_micros": total_locked_micros,
            "total_locked_volume_usd": f"${Decimal(total_locked_micros) / Decimal(1_000_000):.2f}",
            "oldest_active_hold_age_hours": oldest_age_hours,
            "oldest_active_hold_agreement_id": oldest_agreement_id,
            "near_expiry_count": near_expiry_count,
            "solvency_status": "SOLVENT" if is_solvent else "INSOLVENT",
            "solvency": is_solvent,
            "balanced": is_solvent,
            "timestamp": now.isoformat(),
        }

    async def sweep(
        self,
        now: Optional[datetime] = None,
        threshold_hours: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Performs a synchronous sweep of all active contracts:
        - If age >= 70h (threshold) and has vaulted payment token:
            reauthorizes hold, refreshes timestamps, records double-entry journal & audit chain.
        - If age >= 70h (threshold) and has no vaulted payment token:
            gracefully voids via PayPal API, transitions FSM to ESCROW_VOIDED_REFUNDED,
            releases hold back to buyer vault, and finalizes audit chain & Merkle proof.
        """
        now = now or self.clock()
        threshold = threshold_hours if threshold_hours is not None else self.threshold_hours
        active = self._get_active_contracts()

        candidates: List[fsm.ContractRecord] = []
        for c in active:
            age_h = _get_age_seconds(c.created_at or c.authorized_at, now) / 3600.0
            if age_h >= threshold:
                candidates.append(c)

        refreshed_agreements: List[str] = []
        voided_agreements: List[str] = []

        for c in candidates:
            vault_token = getattr(c, "payment_token", None) or c.vault_id
            if vault_token:
                await self._refresh_vaulted_contract(c, vault_token, now)
                refreshed_agreements.append(c.agreement_id)
            else:
                await self._void_expired_contract(c, now)
                voided_agreements.append(c.agreement_id)

        is_solvent = self.ledger.verify_balance_conservation()
        remaining_active = self._get_active_contracts()
        remaining_locked = sum(c.budget_micros for c in remaining_active)

        summary = {
            "status": "COMPLETED",
            "scanned_count": len(candidates),
            "refreshed_count": len(refreshed_agreements),
            "refreshed_agreements": refreshed_agreements,
            "voided_count": len(voided_agreements),
            "voided_agreements": voided_agreements,
            "solvency_status": "SOLVENT" if is_solvent else "INSOLVENT",
            "solvency": is_solvent,
            "balanced": is_solvent,
            "total_locked_volume_micros": remaining_locked,
            "total_locked_volume_usd": f"${Decimal(remaining_locked) / Decimal(1_000_000):.2f}",
            "timestamp": now.isoformat(),
        }

        logger.info(
            f"Sweeper finished: {len(candidates)} scanned, "
            f"{len(refreshed_agreements)} refreshed, {len(voided_agreements)} voided. "
            f"Solvency: {summary['solvency_status']}."
        )
        return summary

    async def _refresh_vaulted_contract(
        self, c: fsm.ContractRecord, vault_token: str, now: datetime
    ) -> None:
        """
        Refreshes an aging authorization hold using PayPal Vaulting v3 token.
        Preserves strict double-entry balance conservation and appends to cryptographic audit chain.
        """
        old_auth_id = c.paypal_authorization_id
        new_auth_id = None

        if hasattr(self.engine, "reauthorize_authorization") and old_auth_id:
            try:
                res = await self.engine.reauthorize_authorization(
                    c.agreement_id, old_auth_id, c.budget_micros
                )
                new_auth_id = res.get("id")
            except Exception as exc:
                logger.warning(f"Reauthorize API call failed for {c.agreement_id}: {exc}")

        if not new_auth_id:
            try:
                if old_auth_id:
                    await self.engine.void_authorization(c.agreement_id, old_auth_id)
            except Exception:
                pass
            order = await self.engine.create_escrow_authorization_order(
                c.agreement_id,
                c.budget_micros,
                c.task_description,
                vault_id=vault_token,
            )
            new_auth_id = extract_authorization_id(order) or f"AUTH-RENEW-{c.agreement_id}"

        c.paypal_authorization_id = new_auth_id
        c.authorized_at = now
        c.created_at = now
        c.updated_at = now

        # Record re-authorization transaction in the double-entry journal
        # Re-balancing legs: release aging hold, then lock fresh hold
        with self.repo.db.transaction():
            self.ledger.record_transfer(
                debit_account=BUYER,
                credit_account=ESCROW,
                amount_micros=c.budget_micros,
                reference_id=c.agreement_id,
                description=f"72h sweeper: aging hold released for re-authorization ({c.agreement_id})",
            )
            self.ledger.record_transfer(
                debit_account=ESCROW,
                credit_account=BUYER,
                amount_micros=c.budget_micros,
                reference_id=c.agreement_id,
                description=f"72h sweeper: refreshed hold locked for re-authorization via vaulted token {vault_token} ({c.agreement_id})",
            )

        # Record in cryptographic audit hash chain
        prev_hash = c.hash_chain_head or compute_genesis_hash(c.agreement_id)
        payload_dict = {
            "action": "72H_REAUTHORIZATION_SWEPT",
            "reason": "PayPal 72h honor period extension via vaulted payment token",
            "old_authorization_id": old_auth_id,
            "new_authorization_id": new_auth_id,
            "vault_id": vault_token,
            "budget_micros": c.budget_micros,
        }
        new_hash = compute_transition_hash(
            prev_hash, now.isoformat(), c.fsm_state.value, payload_dict
        )
        c.hash_chain_head = new_hash
        self.repo.append_hash_chain_entry(
            agreement_id=c.agreement_id,
            state=c.fsm_state.value,
            prev_hash=prev_hash,
            current_hash=new_hash,
            payload_json=json.dumps(payload_dict),
            created_at=now,
        )

        c.transitions.append(
            fsm.TransitionRecord(
                agreement_id=c.agreement_id,
                from_state=c.fsm_state,
                to_state=c.fsm_state,
                reason=f"72-hour re-authorization hold refreshed via vaulted token {vault_token}: {new_auth_id}",
                created_at=now,
            )
        )
        self.repo.save_contract(c)

        if self.broadcaster:
            self.broadcaster.broadcast_sync({
                "event_type": "SWEEPER_REAUTHORIZED",
                "agreement_id": c.agreement_id,
                "timestamp": now.isoformat(),
                "data": {
                    "agreement_id": c.agreement_id,
                    "old_authorization_id": old_auth_id,
                    "new_authorization_id": new_auth_id,
                    "vault_id": vault_token,
                    "budget_micros": c.budget_micros,
                },
            })

    async def _void_expired_contract(self, c: fsm.ContractRecord, now: datetime) -> None:
        """
        Gracefully voids unrefreshable authorization hold and releases escrow back to buyer vault.
        """
        old_auth_id = c.paypal_authorization_id
        if old_auth_id:
            try:
                await self.engine.void_authorization(c.agreement_id, old_auth_id)
            except Exception as exc:
                logger.warning(f"Graceful PayPal void failed for {c.agreement_id}: {exc}")

        prev_state = c.fsm_state
        fsm.transition(
            c,
            FsmState.ESCROW_VOIDED_REFUNDED,
            reason="72h honor period expired without vaulted token; voided & refunded",
            now=now,
        )

        # Release hold back to buyer vault in double-entry ledger
        with self.repo.db.transaction():
            self.ledger.record_transfer(
                debit_account=BUYER,
                credit_account=ESCROW,
                amount_micros=c.budget_micros,
                reference_id=c.agreement_id,
                description=f"72h sweeper void: hold released to buyer ({c.agreement_id})",
            )

        # Record in cryptographic audit hash chain
        prev_hash = c.hash_chain_head or compute_genesis_hash(c.agreement_id)
        payload_dict = {
            "action": "72H_SWEEPER_VOID_REFUND",
            "reason": "PayPal 72h honor period expired without vaulted payment token",
            "from_state": prev_state.value,
            "to_state": FsmState.ESCROW_VOIDED_REFUNDED.value,
            "voided_authorization_id": old_auth_id,
        }
        new_hash = compute_transition_hash(
            prev_hash, now.isoformat(), FsmState.ESCROW_VOIDED_REFUNDED.value, payload_dict
        )
        c.hash_chain_head = new_hash
        self.repo.append_hash_chain_entry(
            agreement_id=c.agreement_id,
            state=FsmState.ESCROW_VOIDED_REFUNDED.value,
            prev_hash=prev_hash,
            current_hash=new_hash,
            payload_json=json.dumps(payload_dict),
            created_at=now,
        )

        # Finalize 7-leaf Merkle root
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
                    "from_state": prev_state.value,
                    "to_state": FsmState.ESCROW_VOIDED_REFUNDED.value,
                    "reason": "72h honor period expired without vaulted token; voided & refunded",
                    "contract": c.to_response().model_dump(mode="json"),
                },
            })


Sweeper = ReauthorizationSweeper
