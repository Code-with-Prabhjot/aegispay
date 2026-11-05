"""
backend/app/services/a2a.py
Module 1: Machine-to-Machine (A2A) Autonomous Negotiation & Ed25519 Identity Protocol.
Implements:
1. Ed25519Agent cryptographic identity, did:key serialization, and verification.
2. Nash Bargaining Mediation Utility (Section 3.2 of Master Architecture Dossier).
3. 3-Way A2A Handshake Flow (SLA_OFFER -> SLA_COUNTER/ACCEPT -> dual-signed agreement).
"""

import json
import uuid
from typing import Any, Dict, Optional, Tuple, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from backend.app.schemas_v2 import AgentIdentity, SlaContractV2

# Base58 BTC alphabet
B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ED25519_MULTICODEC_PREFIX = b"\xed\x01"


def b58encode(data: bytes) -> str:
    """Encodes bytes to base58btc string."""
    n = int.from_bytes(data, "big")
    res = []
    while n > 0:
        n, rem = divmod(n, 58)
        res.append(B58_ALPHABET[rem])
    # Preserve leading zero bytes
    pad = 0
    for byte in data:
        if byte == 0:
            pad += 1
        else:
            break
    return (B58_ALPHABET[0] * pad) + "".join(reversed(res))


def b58decode(s: str) -> bytes:
    """Decodes base58btc string to bytes."""
    n = 0
    for char in s:
        idx = B58_ALPHABET.find(char)
        if idx == -1:
            raise ValueError(f"Invalid character '{char}' in base58 string")
        n = n * 58 + idx
    # Convert integer to bytes
    res = []
    while n > 0:
        n, rem = divmod(n, 256)
        res.append(rem)
    res = bytes(reversed(res))
    # Preserve leading zeros
    pad = 0
    for char in s:
        if char == B58_ALPHABET[0]:
            pad += 1
        else:
            break
    return (b"\x00" * pad) + res


def pubkey_to_did(raw_public_bytes: bytes) -> str:
    """Encodes a 32-byte Ed25519 public key into a W3C did:key string (did:key:z6Mk...)."""
    if len(raw_public_bytes) != 32:
        raise ValueError(f"Ed25519 public key must be exactly 32 bytes, got {len(raw_public_bytes)}")
    prefixed = ED25519_MULTICODEC_PREFIX + raw_public_bytes
    return f"did:key:z{b58encode(prefixed)}"


def did_to_pubkey(did: str) -> bytes:
    """Decodes a W3C did:key string back to 32-byte Ed25519 public key."""
    if not did.startswith("did:key:z"):
        raise ValueError(f"Expected did:key format starting with 'did:key:z', got: {did}")
    b58_str = did[len("did:key:z"):]
    decoded = b58decode(b58_str)
    if not decoded.startswith(ED25519_MULTICODEC_PREFIX):
        raise ValueError("Invalid multicodec prefix for Ed25519 public key")
    raw_pub = decoded[len(ED25519_MULTICODEC_PREFIX):]
    if len(raw_pub) != 32:
        raise ValueError(f"Extracted public key length {len(raw_pub)} != 32")
    return raw_pub


def canonicalize_message(message: Union[dict, list, str, bytes]) -> bytes:
    """Normalizes payload into deterministic canonical UTF-8 bytes for cryptographic signing."""
    if isinstance(message, (dict, list)):
        return json.dumps(message, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if isinstance(message, str):
        return message.encode("utf-8")
    if isinstance(message, bytes):
        return message
    return str(message).encode("utf-8")


def verify_agent_signature(
    public_key: str,
    message: Union[dict, list, str, bytes],
    signature_hex: str,
) -> bool:
    """
    Verifies an Ed25519 signature against a public key (provided as hex string or did:key format).
    Returns True if valid, False otherwise.
    """
    try:
        if public_key.startswith("did:key:"):
            pub_bytes = did_to_pubkey(public_key)
        else:
            pub_bytes = bytes.fromhex(public_key.strip())

        if len(pub_bytes) != 32:
            return False

        sig_bytes = bytes.fromhex(signature_hex.strip())
        if len(sig_bytes) != 64:
            return False

        pub = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        payload_bytes = canonicalize_message(message)
        pub.verify(sig_bytes, payload_bytes)
        return True
    except (InvalidSignature, ValueError, Exception):
        return False


class Ed25519Agent:
    """Helper for generating keypairs, DID serialization, signing and verification."""

    def __init__(
        self,
        private_key: Optional[ed25519.Ed25519PrivateKey] = None,
        agent_id: Optional[str] = None,
    ):
        self._private_key = private_key or ed25519.Ed25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()
        self._public_bytes = self._public_key.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self._did = pubkey_to_did(self._public_bytes)
        self.agent_id = agent_id or self._did

    @classmethod
    def generate(cls, agent_id: Optional[str] = None) -> "Ed25519Agent":
        return cls(private_key=ed25519.Ed25519PrivateKey.generate(), agent_id=agent_id)

    @classmethod
    def from_private_bytes(cls, raw_bytes: bytes, agent_id: Optional[str] = None) -> "Ed25519Agent":
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(raw_bytes)
        return cls(private_key=priv, agent_id=agent_id)

    @classmethod
    def from_private_hex(cls, hex_str: str, agent_id: Optional[str] = None) -> "Ed25519Agent":
        return cls.from_private_bytes(bytes.fromhex(hex_str.strip()), agent_id=agent_id)

    @property
    def public_key_bytes(self) -> bytes:
        return self._public_bytes

    @property
    def public_key_hex(self) -> str:
        return self._public_bytes.hex()

    @property
    def public_key_did(self) -> str:
        return self._did

    def sign(self, message: Union[dict, list, str, bytes]) -> str:
        """Signs arbitrary message and returns 64-byte signature as hex string."""
        payload_bytes = canonicalize_message(message)
        sig = self._private_key.sign(payload_bytes)
        return sig.hex()

    def verify(self, message: Union[dict, list, str, bytes], signature_hex: str) -> bool:
        """Verifies signature against this agent's public key."""
        return verify_agent_signature(self.public_key_hex, message, signature_hex)

    def to_identity(self, reputation_score: float = 1.0) -> AgentIdentity:
        return AgentIdentity(
            agent_id=self.agent_id,
            public_key_hex=self.public_key_hex,
            reputation_score=reputation_score,
        )


# ----------------------------------------------------------------------
# Nash Bargaining Mediation Utility (Section 3.2 of Master Architecture Dossier)
# ----------------------------------------------------------------------

def negotiate_sla_parameters(
    buyer_pref: Dict[str, Any],
    worker_pref: Dict[str, Any],
    alpha: float = 0.5,
    beta: float = 0.5,
) -> Dict[str, Any]:
    """
    Computes Nash bargaining equilibrium parameters (B*, T*) maximizing:
        Utility_Buyer  = alpha * (1 - B / B_max) + (1 - alpha) * (1 - T / T_max)
        Utility_Worker = beta  * (B / B_max)     + (1 - beta)  * (1 - T / T_max)
        Objective: Maximize Utility_Buyer * Utility_Worker

    Bounds:
        Budget B in [B_floor, B_ceil] in integer micro-dollars.
        Deadline T in [T_floor, T_ceil] in seconds.
    """
    # Parse buyer preferences
    b_max = float(
        buyer_pref.get("max_budget_micros")
        or buyer_pref.get("budget_max")
        or buyer_pref.get("budget_micros")
        or buyer_pref.get("agreed_budget_micros")
        or 20_000_000
    )
    b_min_buyer = float(buyer_pref.get("min_budget_micros") or buyer_pref.get("budget_min") or 1_000_000)

    t_max = float(
        buyer_pref.get("max_timeout_seconds")
        or buyer_pref.get("timeout_max")
        or buyer_pref.get("timeout_seconds")
        or buyer_pref.get("max_execution_seconds")
        or 10.0
    )
    t_min_buyer = float(buyer_pref.get("min_timeout_seconds") or buyer_pref.get("timeout_min") or 1.0)

    # Parse worker preferences
    b_min_worker = float(
        worker_pref.get("min_budget_micros")
        or worker_pref.get("budget_min")
        or worker_pref.get("budget_micros")
        or 1_000_000
    )
    b_max_worker = float(
        worker_pref.get("max_budget_micros")
        or worker_pref.get("budget_max")
        or b_max
    )

    t_min_worker = float(
        worker_pref.get("min_timeout_seconds")
        or worker_pref.get("timeout_min")
        or worker_pref.get("timeout_seconds")
        or worker_pref.get("max_execution_seconds")
        or 1.0
    )
    t_max_worker = float(
        worker_pref.get("max_timeout_seconds")
        or worker_pref.get("timeout_max")
        or t_max
    )

    # Feasible bounds
    b_floor = max(b_min_worker, b_min_buyer, 1_000_000.0)
    b_ceil = min(b_max, b_max_worker)
    if b_floor > b_ceil:
        b_ceil = b_floor  # Clamp if overlapping bounds are tight

    t_floor = max(t_min_worker, t_min_buyer, 1.0)
    t_ceil = min(t_max, t_max_worker)
    if t_floor > t_ceil:
        t_ceil = t_floor

    # Objective function
    def compute_utilities(b: float, t: float) -> Tuple[float, float, float]:
        u_b = alpha * (1.0 - (b / b_max)) + (1.0 - alpha) * (1.0 - (t / t_max))
        u_w = beta * (b / b_max) + (1.0 - beta) * (1.0 - (t / t_max))
        nash = max(0.0, u_b) * max(0.0, u_w)
        return u_b, u_w, nash

    # Optimize over feasible space
    # In this formulation, both parties gain utility when execution time T is minimized (faster delivery).
    # Budget balance is a concave quadratic in B with peak near b_max / 2 when alpha == beta.
    best_nash = -1.0
    best_b = b_floor
    best_t = t_floor
    best_ub = 0.0
    best_uw = 0.0

    # Test candidate analytical budget points
    # Unconstrained optimum for B: x* = 0.5 + (beta - alpha) * tau / (2 * alpha * beta)
    tau = 1.0 - (t_floor / t_max)
    if alpha > 0 and beta > 0:
        x_opt = 0.5 + ((beta - alpha) * tau) / (2.0 * alpha * beta)
    else:
        x_opt = 0.5
    b_analytical = x_opt * b_max

    # Discretized search over bounds to guarantee global optimum
    candidates_b = [b_floor, b_ceil, max(b_floor, min(b_ceil, b_analytical))]
    # Add grid samples
    steps = 100
    step_size = (b_ceil - b_floor) / max(1, steps)
    for i in range(steps + 1):
        candidates_b.append(b_floor + i * step_size)

    candidates_t = [t_floor, t_ceil]

    for t in candidates_t:
        for b in candidates_b:
            # Round budget to nearest 10,000 micros (1 whole cent)
            b_rounded = round(b / 10_000.0) * 10_000.0
            b_clamped = max(b_floor, min(b_ceil, b_rounded))
            ub, uw, nash = compute_utilities(b_clamped, t)
            if nash > best_nash:
                best_nash = nash
                best_b = b_clamped
                best_t = t
                best_ub = ub
                best_uw = uw

    return {
        "agreed_budget_micros": int(round(best_b)),
        "max_execution_seconds": float(round(best_t, 2)),
        "agreed_timeout_seconds": float(round(best_t, 2)),
        "buyer_utility": round(best_ub, 4),
        "worker_utility": round(best_uw, 4),
        "nash_product": round(best_nash, 4),
        "status": "AGREEMENT_REACHED",
    }


# ----------------------------------------------------------------------
# 3-Way A2A Handshake Flow
# ----------------------------------------------------------------------

def extract_signing_terms(contract_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts canonical terms that both parties cryptographically sign."""
    contract_id = contract_data.get("contract_id") or contract_data.get("agreement_id") or ""
    task_description = contract_data.get("task_description") or ""
    budget_micros = contract_data.get("agreed_budget_micros") or contract_data.get("budget_micros") or 0
    timeout_seconds = contract_data.get("max_execution_seconds") or contract_data.get("timeout_seconds") or 5.0

    buyer_val = contract_data.get("buyer") or contract_data.get("buyer_agent_id") or ""
    buyer_id = buyer_val if isinstance(buyer_val, str) else getattr(buyer_val, "agent_id", "") or buyer_val.get("agent_id", "")

    worker_val = contract_data.get("worker") or contract_data.get("worker_agent_id") or ""
    worker_id = worker_val if isinstance(worker_val, str) else getattr(worker_val, "agent_id", "") or worker_val.get("agent_id", "")

    return {
        "contract_id": contract_id,
        "task_description": task_description,
        "budget_micros": int(budget_micros),
        "timeout_seconds": float(timeout_seconds),
        "buyer_agent_id": buyer_id,
        "worker_agent_id": worker_id,
    }


def create_sla_offer(
    buyer: Ed25519Agent,
    worker_agent_id: str,
    task_description: str,
    budget_micros: int,
    timeout_seconds: float = 5.0,
    contract_id: Optional[str] = None,
    test_suite_source: str = "",
    deliverable_schema: Optional[Dict[str, Any]] = None,
    runtime_language: str = "python",
) -> Dict[str, Any]:
    """
    Step 1: Buyer drafts and signs SLA_OFFER.
    """
    cid = contract_id or f"agr_{uuid.uuid4().hex[:12]}"
    terms = {
        "contract_id": cid,
        "agreement_id": cid,
        "buyer_agent_id": buyer.agent_id,
        "buyer_public_key": buyer.public_key_hex,
        "worker_agent_id": worker_agent_id,
        "task_description": task_description,
        "agreed_budget_micros": budget_micros,
        "budget_micros": budget_micros,
        "max_execution_seconds": timeout_seconds,
        "timeout_seconds": int(timeout_seconds),
        "runtime_language": runtime_language,
        "test_suite_source": test_suite_source,
        "deliverable_schema": deliverable_schema or {},
    }
    signing_terms = extract_signing_terms(terms)
    buyer_sig = buyer.sign(signing_terms)
    terms["buyer_signature"] = buyer_sig
    terms["worker_signature"] = None
    terms["status"] = "SLA_OFFERED"
    return terms


def counter_propose_sla(
    worker: Ed25519Agent,
    offer: Dict[str, Any],
    counter_budget_micros: Optional[int] = None,
    counter_timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Step 2: Worker counter-proposes or signs offer.
    """
    proposal = dict(offer)
    if counter_budget_micros is not None:
        proposal["agreed_budget_micros"] = counter_budget_micros
        proposal["budget_micros"] = counter_budget_micros
    if counter_timeout_seconds is not None:
        proposal["max_execution_seconds"] = counter_timeout_seconds
        proposal["timeout_seconds"] = int(counter_timeout_seconds)

    proposal["worker_agent_id"] = worker.agent_id
    proposal["worker_public_key"] = worker.public_key_hex

    signing_terms = extract_signing_terms(proposal)
    worker_sig = worker.sign(signing_terms)
    proposal["worker_signature"] = worker_sig
    proposal["status"] = "SLA_COUNTER_PROPOSED"
    return proposal


def sign_sla_agreement(
    agent: Ed25519Agent,
    agreement: Dict[str, Any],
    role: str = "worker",
) -> Dict[str, Any]:
    """Signs an existing SLA agreement as buyer or worker."""
    doc = dict(agreement)
    signing_terms = extract_signing_terms(doc)
    sig = agent.sign(signing_terms)
    if role == "buyer":
        doc["buyer_signature"] = sig
        doc["buyer_agent_id"] = agent.agent_id
        doc["buyer_public_key"] = agent.public_key_hex
    else:
        doc["worker_signature"] = sig
        doc["worker_agent_id"] = agent.agent_id
        doc["worker_public_key"] = agent.public_key_hex
    return doc


def validate_dual_signatures(contract_data: Dict[str, Any]) -> bool:
    """
    Verifies that both buyer_signature and worker_signature are present and
    cryptographically valid for the canonical contract terms.
    """
    buyer_sig = contract_data.get("buyer_signature")
    worker_sig = contract_data.get("worker_signature")

    if not buyer_sig or not worker_sig:
        return False

    signing_terms = extract_signing_terms(contract_data)

    # Determine buyer public key (hex or did)
    buyer_pub = (
        contract_data.get("buyer_public_key")
        or (contract_data.get("buyer") or {}).get("public_key_hex")
        or contract_data.get("buyer_agent_id")
    )
    if not buyer_pub:
        return False

    # Determine worker public key (hex or did)
    worker_pub = (
        contract_data.get("worker_public_key")
        or (contract_data.get("worker") or {}).get("public_key_hex")
        or contract_data.get("worker_agent_id")
    )
    if not worker_pub:
        return False

    buyer_valid = verify_agent_signature(buyer_pub, signing_terms, buyer_sig)
    worker_valid = verify_agent_signature(worker_pub, signing_terms, worker_sig)

    return buyer_valid and worker_valid
