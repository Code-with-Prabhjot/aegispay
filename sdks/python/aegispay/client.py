"""
AegisPay Python SDK Client
Autonomous Agentic Escrow, AST Verification & Double-Entry Settlement.
"""

from typing import Any, Dict, Optional
import httpx


class AegisPayClient:
    """Synchronous & typed client for interacting with the AegisPay Escrow & Settlement API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    def close(self) -> None:
        """Closes the underlying HTTP client session."""
        self._client.close()

    def __enter__(self) -> "AegisPayClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def propose_sla(
        self,
        buyer_agent_id: str,
        worker_agent_id: str,
        worker_paypal_email: str,
        budget_micros: int,
        task_description: str,
        timeout_seconds: int = 3600,
        vault_id: Optional[str] = None,
        test_suite: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Proposes an SLA agreement and locks escrow via PayPal authorization hold.

        Args:
            buyer_agent_id: DID / ID of initiating buyer agent.
            worker_agent_id: DID / ID of assigned worker agent.
            worker_paypal_email: Receiver PayPal email for settlement payout.
            budget_micros: Escrow allocation in integer micro-dollars ($1 = 1,000,000 micros).
            task_description: Specification and acceptance requirements.
            timeout_seconds: Contract expiration TTL in seconds (default 3600).
            vault_id: Optional PayPal Vault token for pre-approved autonomous billing.
            test_suite: Optional test suite string committed by buyer for evaluation.

        Returns:
            Dict containing the created contract record.
        """
        payload = {
            "buyer_agent_id": buyer_agent_id,
            "worker_agent_id": worker_agent_id,
            "worker_paypal_email": worker_paypal_email,
            "budget_micros": budget_micros,
            "task_description": task_description,
            "timeout_seconds": timeout_seconds,
            "vault_id": vault_id,
            "test_suite": test_suite,
        }
        res = self._client.post("/api/v1/contracts", json=payload)
        res.raise_for_status()
        return res.json()

    def lock_escrow(self, agreement_id: str) -> Dict[str, Any]:
        """Verifies or retrieves the locked escrow state for a given agreement.

        Args:
            agreement_id: The unique identifier of the SLA agreement.

        Returns:
            Dict containing contract status, PayPal authorization ID, and escrow status.
        """
        res = self._client.get(f"/api/v1/contracts/{agreement_id}")
        res.raise_for_status()
        return res.json()

    def submit_code(
        self,
        agreement_id: str,
        source_code: str,
        test_suite: Optional[str] = None,
        runtime: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submits code deliverable to the air-gapped arbitration engine.

        Triggers AST security gate, sandbox execution, LLM consensus audit,
        and double-entry clearinghouse settlement.

        Args:
            agreement_id: Unique SLA agreement identifier.
            source_code: Python source code deliverable.
            test_suite: Optional override test suite (used in mock/eval modes).
            runtime: Runtime target (e.g. 'python3.11' or 'node20').

        Returns:
            Dict containing the settled contract, arbitration report, and capture/refund details.
        """
        payload: Dict[str, Any] = {"source_code": source_code}
        if test_suite is not None:
            payload["test_suite"] = test_suite
        if runtime is not None:
            payload["runtime"] = runtime
        res = self._client.post(f"/api/v1/contracts/{agreement_id}/submit", json=payload)
        res.raise_for_status()
        return res.json()

    def get_merkle_proof(self, agreement_id: str, leaf_index: int = 0) -> Dict[str, Any]:
        """Retrieves cryptographic 7-leaf Merkle tree audit proof for settlement non-repudiation.

        Args:
            agreement_id: Unique SLA agreement identifier.
            leaf_index: Leaf index to verify (0..6: Contract, Hold, Code, Ast, Tests, Settlement, Payout).

        Returns:
            Dict containing leaf hash, proof hashes, Merkle root, and local verification status.
        """
        res = self._client.get(f"/api/v1/contracts/{agreement_id}/merkle-proof/{leaf_index}")
        res.raise_for_status()
        return res.json()
