"""
backend/app/schemas_v2.py
Pydantic v2 schemas for Module 1 (A2A Autonomous Negotiation) and Module 4 (Cryptographic Audit Proofs).
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AgentIdentity(BaseModel):
    agent_id: str = Field(..., description="Unique agent identifier (e.g., did:key:z6Mku...)")
    public_key_hex: str = Field(..., description="Ed25519 public key hex string")
    reputation_score: float = Field(default=1.0, ge=0.0, le=1.0)


class SlaContractV2(BaseModel):
    contract_id: str = Field(..., description="Unique contract identifier")
    buyer: AgentIdentity
    worker: AgentIdentity
    task_description: str
    runtime_language: str = Field(default="python", pattern="^(python|typescript|rust)$")
    test_suite_source: str = Field(default="")
    deliverable_schema: Dict[str, Any] = Field(default_factory=dict)
    agreed_budget_micros: int = Field(..., ge=1_000_000, le=100_000_000)
    max_execution_seconds: float = Field(default=5.0, ge=1.0, le=30.0)
    buyer_signature: str = Field(..., description="Ed25519 signature from buyer agent")
    worker_signature: str = Field(..., description="Ed25519 signature from worker agent")


class A2ANegotiateRequest(BaseModel):
    buyer_pref: Dict[str, Any] = Field(..., description="Buyer preferences (max_budget_micros, max_timeout_seconds)")
    worker_pref: Dict[str, Any] = Field(..., description="Worker preferences (min_budget_micros, min_timeout_seconds)")
    alpha: float = Field(default=0.5, ge=0.0, le=1.0, description="Buyer price sensitivity weight")
    beta: float = Field(default=0.5, ge=0.0, le=1.0, description="Worker price sensitivity weight")
    task_description: Optional[str] = Field(default="Autonomous Agent Deliverable", description="Task description")
    runtime_language: Optional[str] = Field(default="python", description="Target runtime")
    buyer_agent_id: Optional[str] = Field(default=None, description="Buyer DID or agent ID")
    worker_agent_id: Optional[str] = Field(default=None, description="Worker DID or agent ID")
    auto_sign: Optional[bool] = Field(default=False, description="Whether to automatically sign generated offer")


class VerifyIdentityRequest(BaseModel):
    public_key_hex: str = Field(..., description="Ed25519 public key hex string or did:key format")
    challenge: Any = Field(..., description="Challenge payload (string or dict) that was signed")
    signature_hex: str = Field(..., description="Ed25519 signature hex string")
    agent_id: Optional[str] = Field(default=None, description="Optional DID or agent ID")


class MerkleProofResponse(BaseModel):
    agreement_id: str
    leaf_index: int
    leaf_name: str
    leaf_data: Any
    leaf_hash: str
    proof_hashes: List[str]
    merkle_root: str
    verified: bool
