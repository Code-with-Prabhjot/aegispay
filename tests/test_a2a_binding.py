"""A2A signatures must cover the payout email and test suite, and identify the agent that signed."""

import uuid

import pytest

from backend.app.schemas import FsmState, SlaContractCreate
from backend.app.services import fsm
from backend.app.services.a2a import (
    Ed25519Agent,
    counter_propose_sla,
    create_sla_offer,
    extract_signing_terms,
    key_belongs_to_agent,
    sign_sla_agreement,
    validate_dual_signatures,
)
from backend.app.services.orchestrator import PolicyViolationError
from tests.test_trust_policy import SUITE, make_orch, settings

EMAIL = "worker@sandbox.paypal.com"


def signed_fields(buyer, worker, email=EMAIL, suite=SUITE, buyer_id=None, worker_id=None):
    """Contract fields signed by both agents over the canonical terms."""
    aid = f"agr_sig_{uuid.uuid4().hex[:8]}"
    buyer_id = buyer_id or buyer.agent_id
    worker_id = worker_id or worker.agent_id
    terms = extract_signing_terms({
        "agreement_id": aid, "task_description": "Signed rate limiter", "budget_micros": 12_000_000,
        "timeout_seconds": 3600, "buyer_agent_id": buyer_id, "worker_agent_id": worker_id,
        "worker_paypal_email": email, "test_suite": suite,
    })
    return dict(
        agreement_id=aid, buyer_agent_id=buyer_id, worker_agent_id=worker_id, worker_paypal_email=email,
        budget_micros=12_000_000, task_description="Signed rate limiter", timeout_seconds=3600,
        test_suite=suite, buyer_signature=buyer.sign(terms), worker_signature=worker.sign(terms),
        buyer_public_key=buyer.public_key_hex, worker_public_key=worker.public_key_hex,
    )


# ---------------------------------------------------------------------------------- signed terms
def test_signed_terms_cover_the_payout_email_and_the_test_suite():
    base = {"agreement_id": "a", "task_description": "t", "budget_micros": 1, "timeout_seconds": 1,
            "buyer_agent_id": "b", "worker_agent_id": "w"}
    plain = extract_signing_terms(base)
    assert extract_signing_terms({**base, "worker_paypal_email": EMAIL}) != plain
    assert extract_signing_terms({**base, "test_suite": SUITE}) != plain
    assert extract_signing_terms({**base, "test_suite": SUITE})["test_suite_sha256"] == \
        extract_signing_terms({**base, "test_suite_source": SUITE})["test_suite_sha256"]


def test_email_is_normalised_before_signing():
    base = {"agreement_id": "a", "task_description": "t", "budget_micros": 1, "timeout_seconds": 1,
            "buyer_agent_id": "b", "worker_agent_id": "w"}
    assert extract_signing_terms({**base, "worker_paypal_email": " Worker@Sandbox.PayPal.com "}) == \
        extract_signing_terms({**base, "worker_paypal_email": EMAIL})


def test_handshake_with_payout_email_and_suite_validates_and_resists_tampering():
    buyer, worker = Ed25519Agent.generate(), Ed25519Agent.generate()
    offer = create_sla_offer(buyer=buyer, worker_agent_id=worker.agent_id, task_description="Rate limiter",
                             budget_micros=10_000_000, worker_paypal_email=EMAIL, test_suite_source=SUITE)
    counter = counter_propose_sla(worker, offer)
    final = sign_sla_agreement(buyer, counter, role="buyer")
    assert validate_dual_signatures(final) is True

    for field, value in (("worker_paypal_email", "attacker@evil.example"),
                         ("test_suite_source", "def test_x():\n    assert True\n")):
        tampered = {**final, field: value}
        assert validate_dual_signatures(tampered) is False, field


def test_worker_can_set_the_payout_email_when_countersigning():
    buyer, worker = Ed25519Agent.generate(), Ed25519Agent.generate()
    offer = create_sla_offer(buyer=buyer, worker_agent_id=worker.agent_id, task_description="Rate limiter",
                             budget_micros=10_000_000)
    counter = counter_propose_sla(worker, offer, worker_paypal_email=EMAIL)
    assert counter["worker_paypal_email"] == EMAIL
    final = sign_sla_agreement(buyer, counter, role="buyer")
    assert validate_dual_signatures(final) is True


async def test_swapping_the_payout_email_after_signing_is_rejected():
    buyer, worker = Ed25519Agent.generate(), Ed25519Agent.generate()
    orch = make_orch()
    good = signed_fields(buyer, worker)
    assert (await orch.create_contract(SlaContractCreate(**good))).fsm_state == FsmState.PAYPAL_AUTH_LOCKED

    swapped = signed_fields(buyer, worker)
    swapped["worker_paypal_email"] = "attacker@evil.example"
    with pytest.raises(fsm.FsmError, match="A2A signature validation failed"):
        await orch.create_contract(SlaContractCreate(**swapped))


async def test_swapping_the_test_suite_after_signing_is_rejected():
    buyer, worker = Ed25519Agent.generate(), Ed25519Agent.generate()
    orch = make_orch()
    swapped = signed_fields(buyer, worker)
    swapped["test_suite"] = "def test_always_passes():\n    assert True\n"
    with pytest.raises(fsm.FsmError, match="A2A signature validation failed"):
        await orch.create_contract(SlaContractCreate(**swapped))


# -------------------------------------------------------------------------------- identity binding
def test_key_belongs_to_agent():
    a, b = Ed25519Agent.generate(), Ed25519Agent.generate()
    assert key_belongs_to_agent(a.agent_id, a.public_key_hex) is True
    assert key_belongs_to_agent(a.agent_id, a.public_key_did) is True
    assert key_belongs_to_agent(a.agent_id, b.public_key_hex) is False
    assert key_belongs_to_agent("did:key:zNotAKey", a.public_key_hex) is False
    assert key_belongs_to_agent(a.agent_id, "not-hex") is False
    assert key_belongs_to_agent(a.agent_id, "ab" * 5) is False  # wrong length


def test_non_did_agent_ids_are_only_bound_when_explicitly_allowed():
    a = Ed25519Agent.generate()
    assert key_belongs_to_agent("worker-agent-alpha", a.public_key_hex, allow_unbound_ids=True) is True
    assert key_belongs_to_agent("worker-agent-alpha", a.public_key_hex, allow_unbound_ids=False) is False


def test_a_fresh_key_cannot_sign_as_someone_elses_did():
    victim, attacker, worker = Ed25519Agent.generate(), Ed25519Agent.generate(), Ed25519Agent.generate()
    # The attacker names the victim as the buyer but signs with their own key and supplies that key.
    forged = signed_fields(attacker, worker, buyer_id=victim.agent_id)
    assert forged["buyer_agent_id"] == victim.agent_id
    assert validate_dual_signatures(forged) is False
    assert validate_dual_signatures({**forged, "buyer_public_key": victim.public_key_hex}) is False


async def test_orchestrator_rejects_a_forged_buyer_identity():
    victim, attacker, worker = Ed25519Agent.generate(), Ed25519Agent.generate(), Ed25519Agent.generate()
    forged = signed_fields(attacker, worker, buyer_id=victim.agent_id)
    with pytest.raises(fsm.FsmError, match="A2A signature validation failed"):
        await make_orch().create_contract(SlaContractCreate(**forged))


async def test_named_agents_cannot_be_bound_outside_mock_mode():
    buyer, worker = Ed25519Agent.generate(), Ed25519Agent.generate()
    fields = signed_fields(buyer, worker, buyer_id="buyer-agent-1", worker_id="worker-agent-1")
    payload = SlaContractCreate(**fields)
    created = await make_orch().create_contract(payload)  # mock mode: names are accepted
    assert created.fsm_state == FsmState.PAYPAL_AUTH_LOCKED

    fields = signed_fields(buyer, worker, buyer_id="buyer-agent-1", worker_id="worker-agent-1")
    with pytest.raises(fsm.FsmError, match="A2A signature validation failed"):
        await make_orch(cfg=settings(PAYPAL_MODE="sandbox")).create_contract(SlaContractCreate(**fields))


async def test_did_agents_validate_outside_mock_mode():
    buyer, worker = Ed25519Agent.generate(), Ed25519Agent.generate()
    orch = make_orch(cfg=settings(PAYPAL_MODE="sandbox"))
    created = await orch.create_contract(SlaContractCreate(**signed_fields(buyer, worker)))
    assert created.fsm_state == FsmState.PAYPAL_AUTH_LOCKED


# ------------------------------------------------------------------------- signatures can be required
async def test_unsigned_contracts_are_accepted_by_default():
    created = await make_orch().create_contract(SlaContractCreate(
        buyer_agent_id="b", worker_agent_id="w", worker_paypal_email=EMAIL,
        budget_micros=5_000_000, task_description="Unsigned task"))
    assert created.fsm_state == FsmState.PAYPAL_AUTH_LOCKED


async def test_require_flag_rejects_unsigned_and_half_signed_contracts():
    orch = make_orch(cfg=settings(REQUIRE_A2A_SIGNATURES=True))
    unsigned = dict(buyer_agent_id="b", worker_agent_id="w", worker_paypal_email=EMAIL,
                    budget_micros=5_000_000, task_description="Unsigned task")
    with pytest.raises(PolicyViolationError, match="signed"):
        await orch.create_contract(SlaContractCreate(**unsigned))

    buyer, worker = Ed25519Agent.generate(), Ed25519Agent.generate()
    half = signed_fields(buyer, worker)
    half["worker_signature"] = None
    with pytest.raises(PolicyViolationError):
        await orch.create_contract(SlaContractCreate(**half))

    full = signed_fields(buyer, worker)
    assert (await orch.create_contract(SlaContractCreate(**full))).fsm_state == FsmState.PAYPAL_AUTH_LOCKED
