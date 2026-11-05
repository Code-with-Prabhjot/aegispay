"""
tests/test_a2a_and_merkle.py
Comprehensive test suite for:
- Module 1: Machine-to-Machine (A2A) Autonomous Negotiation & Ed25519 Identity Protocol
- Module 4: Enterprise Cryptographic Hash Chains & 7-Leaf Merkle Tree Audit Proofs
"""

import json
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import Database, Repository
from backend.app.main import create_app
from backend.app.schemas import FsmState, SlaContractCreate
from backend.app.services import fsm
from backend.app.services.a2a import (
    Ed25519Agent,
    b58decode,
    b58encode,
    counter_propose_sla,
    create_sla_offer,
    did_to_pubkey,
    extract_signing_terms,
    negotiate_sla_parameters,
    pubkey_to_did,
    sign_sla_agreement,
    validate_dual_signatures,
    verify_agent_signature,
)
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.merkle_audit import (
    LEAF_NAMES,
    MerkleAuditTree,
    compile_7leaf_bundle,
    compute_genesis_hash,
    compute_transition_hash,
    hash_leaf,
    verify_hash_chain,
    verify_merkle_proof,
)
from backend.app.services.orchestrator import EscrowOrchestrator
from backend.app.services.paypal_mock import MockPayPalEscrowEngine


@pytest.fixture
def test_repo(tmp_path):
    db_file = tmp_path / "test_a2a_merkle.db"
    repo = Repository.from_url(f"sqlite:///{db_file}")
    return repo


@pytest.fixture
def mock_paypal():
    return MockPayPalEscrowEngine()


@pytest.fixture
def test_ledger(test_repo):
    return DoubleEntryLedgerEngine.from_repository(test_repo)


@pytest.fixture
def test_orch(test_repo, test_ledger, mock_paypal):
    settings = Settings(PAYPAL_MODE="mock", DATABASE_URL=test_repo.db.url)
    return EscrowOrchestrator(
        repo=test_repo,
        ledger=test_ledger,
        engine=mock_paypal,
        settings=settings,
    )


@pytest.fixture
def test_client(test_repo, mock_paypal):
    settings = Settings(PAYPAL_MODE="mock", DATABASE_URL=test_repo.db.url)
    app = create_app(settings=settings, engine=mock_paypal, repo=test_repo)
    return TestClient(app)


# ======================================================================
# Task 1: Ed25519 Agent Identity & A2A Negotiation Tests
# ======================================================================

def test_ed25519_key_generation_and_did_serialization():
    agent = Ed25519Agent.generate()
    assert agent.public_key_hex is not None
    assert len(agent.public_key_hex) == 64  # 32 bytes hex
    assert len(agent.public_key_bytes) == 32

    # DID format validation
    did = agent.public_key_did
    assert did.startswith("did:key:z6Mk")
    assert agent.agent_id == did

    # Roundtrip conversion
    recovered_pub = did_to_pubkey(did)
    assert recovered_pub == agent.public_key_bytes
    assert pubkey_to_did(recovered_pub) == did

    # From private hex roundtrip
    priv_hex = agent._private_key.private_bytes_raw().hex()
    reloaded = Ed25519Agent.from_private_hex(priv_hex)
    assert reloaded.public_key_hex == agent.public_key_hex
    assert reloaded.public_key_did == agent.public_key_did


def test_base58_edge_cases():
    data = b"\x00\x00\x01\x02\x03\xff"
    encoded = b58encode(data)
    decoded = b58decode(encoded)
    assert decoded == data

    with pytest.raises(ValueError):
        b58decode("0OIl")  # Invalid chars in base58


def test_ed25519_signing_and_signature_verification():
    agent = Ed25519Agent.generate()
    payload = {"task": "build-arbiter", "budget_micros": 15000000, "deadline": 5.0}

    sig_hex = agent.sign(payload)
    assert len(sig_hex) == 128  # 64 bytes hex

    # Verify via agent method
    assert agent.verify(payload, sig_hex) is True

    # Verify via standalone helper using hex key
    assert verify_agent_signature(agent.public_key_hex, payload, sig_hex) is True

    # Verify via standalone helper using DID key
    assert verify_agent_signature(agent.agent_id, payload, sig_hex) is True

    # Tampered payload fails
    tampered_payload = {"task": "build-arbiter", "budget_micros": 99999999, "deadline": 5.0}
    assert verify_agent_signature(agent.public_key_hex, tampered_payload, sig_hex) is False

    # Tampered signature fails
    tampered_sig = sig_hex[:-4] + "0000"
    assert verify_agent_signature(agent.public_key_hex, payload, tampered_sig) is False

    # String and bytes payloads
    msg_str = "AegisPay Escrow Authorization"
    sig_str = agent.sign(msg_str)
    assert verify_agent_signature(agent.public_key_hex, msg_str, sig_str) is True
    assert verify_agent_signature(agent.public_key_hex, "Tampered", sig_str) is False


def test_nash_bargaining_mediation_symmetric():
    buyer_pref = {"max_budget_micros": 20_000_000, "max_timeout_seconds": 10.0}
    worker_pref = {"min_budget_micros": 5_000_000, "min_timeout_seconds": 2.0}

    result = negotiate_sla_parameters(buyer_pref, worker_pref, alpha=0.5, beta=0.5)

    assert result["status"] == "AGREEMENT_REACHED"
    # When alpha == beta == 0.5, budget equilibrates at 50% of B_max = 10,000,000 micros
    assert result["agreed_budget_micros"] == 10_000_000
    # Both parties maximize utility at minimum turnaround time (2.0s)
    assert result["max_execution_seconds"] == 2.0
    assert result["buyer_utility"] > 0.0
    assert result["worker_utility"] > 0.0
    assert result["nash_product"] > 0.0


def test_nash_bargaining_mediation_worker_floor_clamping():
    # Worker refuses any task below $16.00 (16,000,000 micros)
    buyer_pref = {"max_budget_micros": 20_000_000, "max_timeout_seconds": 10.0}
    worker_pref = {"min_budget_micros": 16_000_000, "min_timeout_seconds": 3.0}

    result = negotiate_sla_parameters(buyer_pref, worker_pref, alpha=0.5, beta=0.5)
    assert result["agreed_budget_micros"] >= 16_000_000
    assert result["max_execution_seconds"] == 3.0


def test_a2a_3way_handshake_flow_and_dual_signatures():
    buyer = Ed25519Agent.generate()
    worker = Ed25519Agent.generate()

    # Step 1: SLA Offer
    offer = create_sla_offer(
        buyer=buyer,
        worker_agent_id=worker.agent_id,
        task_description="Build FastAPI Rate Limiter",
        budget_micros=10_000_000,
        timeout_seconds=5.0,
    )
    assert offer["buyer_signature"] is not None
    assert offer["worker_signature"] is None
    # Unsigned by worker -> dual validation fails
    assert validate_dual_signatures(offer) is False

    # Step 2: Worker counter-proposes
    counter = counter_propose_sla(
        worker=worker,
        offer=offer,
        counter_budget_micros=12_000_000,
        counter_timeout_seconds=6.0,
    )
    assert counter["worker_signature"] is not None

    # Step 3: Buyer accepts counter proposal and re-signs
    final_agreement = sign_sla_agreement(buyer, counter, role="buyer")
    assert validate_dual_signatures(final_agreement) is True

    # Tampering with agreement terms invalidates both signatures
    tampered = dict(final_agreement)
    tampered["agreed_budget_micros"] = 50_000_000
    assert validate_dual_signatures(tampered) is False


@pytest.mark.asyncio
async def test_orchestrator_rejects_invalid_a2a_signatures(test_orch):
    buyer = Ed25519Agent.generate()
    worker = Ed25519Agent.generate()

    # Create offer but provide an invalid forged worker signature
    invalid_payload = SlaContractCreate(
        agreement_id="agr_a2a_invalid_sig",
        buyer_agent_id=buyer.agent_id,
        worker_agent_id=worker.agent_id,
        worker_paypal_email="worker@sandbox.paypal.com",
        budget_micros=10_000_000,
        task_description="Test invalid A2A signature",
        timeout_seconds=3600,
        buyer_signature=buyer.sign({"bad": "terms"}),
        worker_signature="deadbeef" * 16,
        buyer_public_key=buyer.public_key_hex,
        worker_public_key=worker.public_key_hex,
    )

    with pytest.raises(fsm.FsmError) as exc_info:
        await test_orch.create_contract(invalid_payload)
    assert "A2A signature validation failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_orchestrator_accepts_valid_a2a_signatures(test_orch):
    buyer = Ed25519Agent.generate()
    worker = Ed25519Agent.generate()

    agreement_id = "agr_a2a_valid_sig"
    signing_terms = extract_signing_terms({
        "agreement_id": agreement_id,
        "task_description": "Valid A2A SLA contract",
        "budget_micros": 12_000_000,
        "timeout_seconds": 3600,
        "buyer_agent_id": buyer.agent_id,
        "worker_agent_id": worker.agent_id,
        "worker_paypal_email": "worker@sandbox.paypal.com",  # the payout address is part of what is signed
    })

    b_sig = buyer.sign(signing_terms)
    w_sig = worker.sign(signing_terms)

    valid_payload = SlaContractCreate(
        agreement_id=agreement_id,
        buyer_agent_id=buyer.agent_id,
        worker_agent_id=worker.agent_id,
        worker_paypal_email="worker@sandbox.paypal.com",
        budget_micros=12_000_000,
        task_description="Valid A2A SLA contract",
        timeout_seconds=3600,
        buyer_signature=b_sig,
        worker_signature=w_sig,
        buyer_public_key=buyer.public_key_hex,
        worker_public_key=worker.public_key_hex,
    )

    contract = await test_orch.create_contract(valid_payload)
    assert contract.fsm_state == FsmState.PAYPAL_AUTH_LOCKED
    assert contract.buyer_signature == b_sig
    assert contract.worker_signature == w_sig


# ======================================================================
# Task 2: Cryptographic Hash Chains & 7-Leaf Merkle Proofs Tests
# ======================================================================

def test_hash_chain_genesis_and_rolling_hash(test_repo):
    agreement_id = "agr_hash_chain_test"
    # Seed contract record first to satisfy foreign key
    contract = fsm.ContractRecord(
        agreement_id=agreement_id,
        buyer_agent_id="buyer1",
        worker_agent_id="worker1",
        worker_paypal_email="worker@paypal.com",
        budget_micros=10_000_000,
        task_description="Hash chain test",
        timeout_seconds=3600,
    )
    test_repo.save_contract(contract)

    h0 = compute_genesis_hash(agreement_id)
    assert len(h0) == 64
    assert h0 == compute_genesis_hash(agreement_id)

    # Next transition
    ts = "2026-11-05T12:00:00Z"
    h1 = compute_transition_hash(h0, ts, "PAYPAL_AUTH_LOCKED", {"hold": True})
    assert len(h1) == 64
    assert h1 != h0

    # Test repository hash chain recording
    test_repo.append_hash_chain_entry(
        agreement_id=agreement_id,
        state="AGREEMENT_DRAFTED",
        prev_hash="0" * 64,
        current_hash=h0,
        payload_json=json.dumps({"genesis": True}),
        created_at=test_repo.db.query("SELECT datetime('now') AS now")[0]["now"],
    )
    test_repo.append_hash_chain_entry(
        agreement_id=agreement_id,
        state="PAYPAL_AUTH_LOCKED",
        prev_hash=h0,
        current_hash=h1,
        payload_json=json.dumps({"hold": True}),
        created_at=ts,
    )

    assert verify_hash_chain(agreement_id, test_repo) is True


def test_hash_chain_tamper_detection(test_repo):
    agreement_id = "agr_tamper_test"
    # Seed contract record first to satisfy foreign key
    contract = fsm.ContractRecord(
        agreement_id=agreement_id,
        buyer_agent_id="buyer1",
        worker_agent_id="worker1",
        worker_paypal_email="worker@paypal.com",
        budget_micros=10_000_000,
        task_description="Tamper detection test",
        timeout_seconds=3600,
    )
    test_repo.save_contract(contract)

    h0 = compute_genesis_hash(agreement_id)
    ts1 = "2026-11-05T12:00:00Z"
    h1 = compute_transition_hash(h0, ts1, "PAYPAL_AUTH_LOCKED", {"budget": 1000})

    test_repo.append_hash_chain_entry(
        agreement_id=agreement_id,
        state="AGREEMENT_DRAFTED",
        prev_hash="0" * 64,
        current_hash=h0,
        payload_json=json.dumps({"genesis": True}),
        created_at=ts1,
    )
    test_repo.append_hash_chain_entry(
        agreement_id=agreement_id,
        state="PAYPAL_AUTH_LOCKED",
        prev_hash=h0,
        current_hash=h1,
        payload_json=json.dumps({"budget": 1000}),
        created_at=ts1,
    )
    assert verify_hash_chain(agreement_id, test_repo) is True

    # Tamper with historical payload in database
    test_repo.db.execute(
        "UPDATE audit_hash_chain SET payload_json = ? WHERE agreement_id = ? AND state = ?",
        (json.dumps({"budget": 999999}), agreement_id, "PAYPAL_AUTH_LOCKED"),
    )

    # Verification must now FAIL
    assert verify_hash_chain(agreement_id, test_repo) is False


def test_7leaf_merkle_tree_proofs_and_verification():
    leaves = [
        {"leaf_0": "SLA Contract JSON & Signatures"},
        {"leaf_1": "PayPal Auth & Webhook Receipts"},
        {"leaf_2": "Raw Stdout & Stderr"},
        {"leaf_3": "AST Syntax Tree & Safety Score"},
        {"leaf_4": "Pytest JSON Execution Metrics"},
        {"leaf_5": "Dual-LLM Semantic Transcripts"},
        {"leaf_6": "Balanced Double-Entry Journal Lines"},
    ]

    tree = MerkleAuditTree(leaves)
    assert len(tree.root_hash) == 64
    assert len(tree.leaf_hashes) == 7

    # Verify proofs for each of the 7 leaves
    for idx in range(7):
        proof = tree.get_proof(idx)
        assert len(proof) == 3  # 8-leaf complete binary tree has depth 3
        # Valid proof verifies
        assert verify_merkle_proof(idx, leaves[idx], proof, tree.root_hash) is True
        # Altered leaf data fails
        altered_leaf = dict(leaves[idx])
        altered_leaf["tampered"] = True
        assert verify_merkle_proof(idx, altered_leaf, proof, tree.root_hash) is False

    # Out of bounds
    with pytest.raises(IndexError):
        tree.get_proof(7)


@pytest.mark.asyncio
async def test_full_scenario_merkle_and_hash_chain(test_orch):
    contract = await test_orch.run_scenario("CLEAN_PASS", payout_outcome="succeeded")
    assert contract.fsm_state == FsmState.SETTLED_COMPLETE

    # Verify hash chain was recorded across entire lifecycle
    chain = test_orch.repo.get_hash_chain(contract.agreement_id)
    assert len(chain) >= 5
    assert verify_hash_chain(contract.agreement_id, test_orch.repo) is True

    # Verify Merkle root is populated on contract
    assert contract.merkle_root is not None
    assert len(contract.merkle_root) == 64


# ======================================================================
# API Endpoints Integration Tests
# ======================================================================

def test_api_a2a_negotiate(test_client):
    req_body = {
        "buyer_pref": {"max_budget_micros": 15_000_000, "max_timeout_seconds": 8.0},
        "worker_pref": {"min_budget_micros": 5_000_000, "min_timeout_seconds": 2.0},
        "alpha": 0.5,
        "beta": 0.5,
        "auto_sign": True,
        "buyer_agent_id": "did:key:z6MkTestBuyerClient",
    }
    res = test_client.post("/api/v1/a2a/negotiate", json=req_body)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "AGREEMENT_REACHED"
    assert data["agreed_budget_micros"] > 0
    assert data["max_execution_seconds"] > 0
    assert data["offer"] is not None
    assert data["offer"]["buyer_signature"] is not None


def test_api_a2a_verify_identity(test_client):
    agent = Ed25519Agent.generate()
    challenge = {"session_token": "aegispay_auth_token_777", "timestamp": "2026-11-05T12:00:00Z"}
    sig = agent.sign(challenge)

    # Valid check
    res = test_client.post("/api/v1/a2a/verify-identity", json={
        "public_key_hex": agent.public_key_hex,
        "challenge": challenge,
        "signature_hex": sig,
    })
    assert res.status_code == 200
    assert res.json()["valid"] is True
    assert res.json()["agent_id"] == agent.agent_id

    # Forged signature check
    bad_res = test_client.post("/api/v1/a2a/verify-identity", json={
        "public_key_hex": agent.public_key_hex,
        "challenge": {"forged": True},
        "signature_hex": sig,
    })
    assert bad_res.status_code == 200
    assert bad_res.json()["valid"] is False


def test_api_contract_detail_and_merkle_proof(test_client):
    # Run CLEAN_PASS demo scenario to reach SETTLED_COMPLETE
    res_demo = test_client.post("/api/v1/demo/scenarios/CLEAN_PASS")
    assert res_demo.status_code == 200
    agreement_id = res_demo.json()["agreement_id"]

    # 1. GET /api/v1/contracts/{id}/detail
    res_detail = test_client.get(f"/api/v1/contracts/{agreement_id}/detail")
    assert res_detail.status_code == 200
    detail = res_detail.json()

    assert detail["merkle_root"] is not None
    assert detail["hash_chain_head"] is not None
    assert detail["audit_bundle"] is not None
    assert len(detail["audit_bundle"]["leaves"]) == 7
    assert len(detail["audit_bundle"]["leaf_names"]) == 7

    # 2. GET /api/v1/contracts/{id}/merkle-proof/{leaf_index}
    for i in range(7):
        res_proof = test_client.get(f"/api/v1/contracts/{agreement_id}/merkle-proof/{i}")
        assert res_proof.status_code == 200
        p_data = res_proof.json()
        assert p_data["leaf_index"] == i
        assert len(p_data["proof_hashes"]) == 3
        assert p_data["verified"] is True
        assert p_data["merkle_root"] == detail["merkle_root"]

    # Out of bounds leaf index
    res_bad = test_client.get(f"/api/v1/contracts/{agreement_id}/merkle-proof/7")
    assert res_bad.status_code == 400

    # Non-existent agreement
    res_404 = test_client.get("/api/v1/contracts/non_existent_agr/merkle-proof/0")
    assert res_404.status_code == 404


def test_nash_bargaining_asymmetric_weights():
    # Worker with high bargaining power (beta=0.8, alpha=0.2)
    b_pref = {"max_budget_micros": 20_000_000, "max_timeout_seconds": 10.0}
    w_pref = {"min_budget_micros": 5_000_000, "min_timeout_seconds": 2.0}

    worker_favored = negotiate_sla_parameters(b_pref, w_pref, alpha=0.2, beta=0.8)
    buyer_favored = negotiate_sla_parameters(b_pref, w_pref, alpha=0.8, beta=0.2)

    # Worker-favored should yield higher agreed budget than buyer-favored
    assert worker_favored["agreed_budget_micros"] > buyer_favored["agreed_budget_micros"]


def test_verify_merkle_proof_invalid_boundary():
    dummy_leaves = [{"l": i} for i in range(7)]
    tree = MerkleAuditTree(dummy_leaves)
    proof = tree.get_proof(0)

    # Index out of bounds
    assert verify_merkle_proof(-1, dummy_leaves[0], proof, tree.root_hash) is False
    assert verify_merkle_proof(7, dummy_leaves[0], proof, tree.root_hash) is False

    # Corrupted proof hash
    bad_proof = list(proof)
    bad_proof[0] = "deadbeef" * 8
    assert verify_merkle_proof(0, dummy_leaves[0], bad_proof, tree.root_hash) is False


def test_a2a_roundtrip_with_did_and_hex_interoperability():
    agent = Ed25519Agent.generate()
    doc = {"action": "PAYPAL_AUTH_RELEASE", "nonce": 42}
    sig = agent.sign(doc)

    # Verify using hex public key
    assert verify_agent_signature(agent.public_key_hex, doc, sig) is True
    # Verify using did:key
    assert verify_agent_signature(agent.public_key_did, doc, sig) is True
    # Corrupted key string returns False
    assert verify_agent_signature("invalid_key_format", doc, sig) is False
    assert verify_agent_signature("did:key:zInvalidBase58", doc, sig) is False

