"""
tests/test_vault_velocity.py
Unit and integration tests for PayPal Vaulting v3 & Velocity Circuit Breaker Engine.
Verifies task limits, rolling 24h daily budgets, AST security void counters, and circuit breakers.
"""

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import Repository
from backend.app.main import create_app
from backend.app.schemas import FsmState, SlaContractCreate, VaultVelocityModel
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.orchestrator import EscrowOrchestrator
from backend.app.services.paypal_mock import MockPayPalEscrowEngine
from backend.app.services.scenario_presets import SCENARIOS
from backend.app.services.vault_service import (
    VaultCircuitBreakerError,
    VaultService,
    check_vault_allowance,
    record_vault_settlement,
    register_vault,
    reset_vault_circuit_breaker,
)

NOW = datetime(2026, 11, 2, 12, 0, 0, tzinfo=timezone.utc)
EPOCH_NOW = int(NOW.timestamp())


@pytest.fixture()
def repo(tmp_path):
    return Repository.from_url(f"sqlite:///{tmp_path}/vault_test.db")


@pytest.fixture()
def env(tmp_path):
    cfg = Settings(
        _env_file=None,
        PAYPAL_MODE="mock",
        SANDBOX_MODE="subprocess",
        DATABASE_URL=f"sqlite:///{tmp_path}/api_vault.db",
    )
    repo = Repository.from_url(cfg.DATABASE_URL)
    engine = MockPayPalEscrowEngine()
    app = create_app(cfg, engine=engine, repo=repo)
    with TestClient(app) as client:
        yield client, app, repo, engine


def test_vault_registration_and_retrieval(repo):
    vault = register_vault(
        vault_id="vault_buyer_001",
        buyer_did="did:key:z6MkuTq",
        max_amount_per_task_micros=20_000_000,
        max_daily_budget_micros=80_000_000,
        repo=repo,
    )
    assert vault.vault_id == "vault_buyer_001"
    assert vault.buyer_did == "did:key:z6MkuTq"
    assert vault.max_amount_per_task_micros == 20_000_000
    assert vault.max_daily_budget_micros == 80_000_000
    assert vault.current_daily_spent_micros == 0
    assert vault.consecutive_void_count == 0
    assert vault.is_frozen == 0

    fetched = repo.get_vault("vault_buyer_001")
    assert fetched is not None
    assert fetched.vault_id == "vault_buyer_001"
    assert fetched.max_amount_per_task_micros == 20_000_000


def test_draw_within_task_limits_and_daily_budgets_passes(repo):
    register_vault(
        vault_id="vault_draw_ok",
        buyer_did="did:key:z6MkuOk",
        max_amount_per_task_micros=25_000_000,
        max_daily_budget_micros=100_000_000,
        repo=repo,
    )

    allowed, reason = check_vault_allowance("vault_draw_ok", 10_000_000, repo=repo, now_epoch=EPOCH_NOW)
    assert allowed is True
    assert reason == "ALLOWANCE_APPROVED"

    allowed, reason = check_vault_allowance("vault_draw_ok", 25_000_000, repo=repo, now_epoch=EPOCH_NOW)
    assert allowed is True
    assert reason == "ALLOWANCE_APPROVED"


def test_exceeding_single_task_budget_rejected(repo):
    register_vault(
        vault_id="vault_task_limit",
        buyer_did="did:key:z6MkuLimit",
        max_amount_per_task_micros=25_000_000,
        max_daily_budget_micros=100_000_000,
        repo=repo,
    )

    allowed, reason = check_vault_allowance(
        "vault_task_limit", 25_000_001, repo=repo, now_epoch=EPOCH_NOW
    )
    assert allowed is False
    assert reason == "EXCEEDS_TASK_LIMIT"

    allowed, reason = check_vault_allowance(
        "vault_task_limit", 50_000_000, repo=repo, now_epoch=EPOCH_NOW
    )
    assert allowed is False
    assert reason == "EXCEEDS_TASK_LIMIT"


def test_cumulative_draws_exceeding_24h_budget_rejected(repo):
    register_vault(
        vault_id="vault_daily_limit",
        buyer_did="did:key:z6MkuDaily",
        max_amount_per_task_micros=25_000_000,
        max_daily_budget_micros=100_000_000,
        repo=repo,
    )

    # 4 draws of $25 each ($100 total)
    for i in range(4):
        allowed, _ = check_vault_allowance("vault_daily_limit", 25_000_000, repo=repo, now_epoch=EPOCH_NOW)
        assert allowed is True
        record_vault_settlement("vault_daily_limit", 25_000_000, is_void=False, repo=repo, now_epoch=EPOCH_NOW)

    v = repo.get_vault("vault_daily_limit")
    assert v.current_daily_spent_micros == 100_000_000

    # 5th draw of $5 exceeds $100 daily budget
    allowed, reason = check_vault_allowance("vault_daily_limit", 5_000_000, repo=repo, now_epoch=EPOCH_NOW)
    assert allowed is False
    assert reason == "EXCEEDS_DAILY_BUDGET"


def test_24h_rolling_window_resets_budget(repo):
    register_vault(
        vault_id="vault_window_reset",
        buyer_did="did:key:z6MkuReset",
        max_amount_per_task_micros=25_000_000,
        max_daily_budget_micros=100_000_000,
        current_daily_spent_micros=100_000_000,
        window_reset_timestamp=EPOCH_NOW + 1000,
        repo=repo,
    )

    # Before window expiration: rejected
    allowed, reason = check_vault_allowance(
        "vault_window_reset", 10_000_000, repo=repo, now_epoch=EPOCH_NOW + 500
    )
    assert allowed is False
    assert reason == "EXCEEDS_DAILY_BUDGET"

    # After window expiration (EPOCH_NOW + 1001): rolling reset triggered
    allowed, reason = check_vault_allowance(
        "vault_window_reset", 10_000_000, repo=repo, now_epoch=EPOCH_NOW + 1001
    )
    assert allowed is True
    assert reason == "ALLOWANCE_APPROVED"

    v = repo.get_vault("vault_window_reset")
    assert v.current_daily_spent_micros == 0
    assert v.window_reset_timestamp == EPOCH_NOW + 1001 + 86400


def test_consecutive_voids_trip_circuit_breaker(repo):
    register_vault(
        vault_id="vault_cb_trip",
        buyer_did="did:key:z6MkuTrip",
        repo=repo,
    )

    # 1st void
    record_vault_settlement("vault_cb_trip", 0, is_void=True, repo=repo)
    v1 = repo.get_vault("vault_cb_trip")
    assert v1.consecutive_void_count == 1
    assert v1.is_frozen == 0
    allowed, _ = check_vault_allowance("vault_cb_trip", 10_000_000, repo=repo)
    assert allowed is True

    # 2nd void
    record_vault_settlement("vault_cb_trip", 0, is_void=True, repo=repo)
    v2 = repo.get_vault("vault_cb_trip")
    assert v2.consecutive_void_count == 2
    assert v2.is_frozen == 0
    allowed, _ = check_vault_allowance("vault_cb_trip", 10_000_000, repo=repo)
    assert allowed is True

    # 3rd consecutive void -> TRIPS circuit breaker!
    record_vault_settlement("vault_cb_trip", 0, is_void=True, repo=repo)
    v3 = repo.get_vault("vault_cb_trip")
    assert v3.consecutive_void_count == 3
    assert v3.is_frozen == 1

    # Now any allowance request must be rejected with CIRCUIT_BREAKER_ACTIVE
    allowed, reason = check_vault_allowance("vault_cb_trip", 1_000_000, repo=repo)
    assert allowed is False
    assert reason == "CIRCUIT_BREAKER_ACTIVE"


def test_settlement_resets_consecutive_void_count(repo):
    register_vault(
        vault_id="vault_void_cleared",
        buyer_did="did:key:z6MkuClear",
        repo=repo,
    )

    # 2 voids occur
    record_vault_settlement("vault_void_cleared", 0, is_void=True, repo=repo)
    record_vault_settlement("vault_void_cleared", 0, is_void=True, repo=repo)
    assert repo.get_vault("vault_void_cleared").consecutive_void_count == 2

    # Successful settlement occurs -> resets consecutive_void_count to 0
    record_vault_settlement("vault_void_cleared", 15_000_000, is_void=False, repo=repo)
    v = repo.get_vault("vault_void_cleared")
    assert v.consecutive_void_count == 0
    assert v.is_frozen == 0
    assert v.current_daily_spent_micros == 15_000_000


def test_frozen_vault_rejects_subsequent_requests_until_reset(repo):
    register_vault(
        vault_id="vault_unfreeze_test",
        buyer_did="did:key:z6MkuUnfreeze",
        is_frozen=1,
        consecutive_void_count=3,
        repo=repo,
    )

    # Rejection while frozen
    allowed, reason = check_vault_allowance("vault_unfreeze_test", 5_000_000, repo=repo)
    assert allowed is False
    assert reason == "CIRCUIT_BREAKER_ACTIVE"

    # Even 1 year later without reset, still frozen
    allowed, reason = check_vault_allowance("vault_unfreeze_test", 5_000_000, repo=repo, now_epoch=EPOCH_NOW + 31536000)
    assert allowed is False
    assert reason == "CIRCUIT_BREAKER_ACTIVE"

    # Operator or agent triggers unfreeze
    reset = reset_vault_circuit_breaker("vault_unfreeze_test", repo=repo)
    assert reset is not None
    assert reset.is_frozen == 0
    assert reset.consecutive_void_count == 0

    # Authorization now approved
    allowed, reason = check_vault_allowance("vault_unfreeze_test", 5_000_000, repo=repo, now_epoch=EPOCH_NOW)
    assert allowed is True
    assert reason == "ALLOWANCE_APPROVED"


def test_vault_service_class_wrapper(repo):
    service = VaultService(repo=repo, clock=lambda: EPOCH_NOW)
    service.register_vault(
        vault_id="vault_svc",
        buyer_did="did:key:z6MkuSvc",
        max_amount_per_task_micros=20_000_000,
    )

    allowed, reason = service.check_vault_allowance("vault_svc", 15_000_000)
    assert allowed is True

    service.record_vault_settlement("vault_svc", 15_000_000, is_void=False)
    v = service.get_vault("vault_svc")
    assert v.current_daily_spent_micros == 15_000_000

    # Trip breaker
    service.record_vault_settlement("vault_svc", 0, is_void=True)
    service.record_vault_settlement("vault_svc", 0, is_void=True)
    service.record_vault_settlement("vault_svc", 0, is_void=True)
    assert service.get_vault("vault_svc").is_frozen == 1

    service.reset_circuit_breaker("vault_svc")
    assert service.get_vault("vault_svc").is_frozen == 0


@pytest.mark.asyncio
async def test_orchestrator_vault_enforcement_and_settlement(tmp_path):
    cfg = Settings(
        _env_file=None,
        PAYPAL_MODE="mock",
        SANDBOX_MODE="subprocess",
        DATABASE_URL=f"sqlite:///{tmp_path}/orch_vault.db",
    )
    repo = Repository.from_url(cfg.DATABASE_URL)
    engine = MockPayPalEscrowEngine()
    ledger = DoubleEntryLedgerEngine(repository=repo)
    orch = EscrowOrchestrator(repo, ledger, engine, cfg, clock=lambda: NOW)

    register_vault(
        vault_id="vault_orch_01",
        buyer_did="did:key:z6MkuOrch",
        max_amount_per_task_micros=25_000_000,
        max_daily_budget_micros=100_000_000,
        repo=repo,
    )

    # 1. Budget exceeding single-task limit fails
    with pytest.raises(VaultCircuitBreakerError) as exc_info:
        await orch.create_contract(
            SlaContractCreate(
                buyer_agent_id="buyer_01",
                worker_agent_id="worker_01",
                worker_paypal_email="worker@example.com",
                budget_micros=30_000_000,  # $30 > $25 max
                task_description="Task exceeding single task allowance",
                vault_id="vault_orch_01",
            )
        )
    assert "EXCEEDS_TASK_LIMIT" in str(exc_info.value)

    # 2. Budget within allowance succeeds
    contract = await orch.create_contract(
        SlaContractCreate(
            agreement_id="agr_vault_valid",
            buyer_agent_id="buyer_01",
            worker_agent_id="worker_01",
            worker_paypal_email="worker@example.com",
            budget_micros=15_000_000,  # $15 <= $25
            task_description="Valid task within vault limits",
            vault_id="vault_orch_01",
        )
    )
    assert contract.fsm_state == FsmState.PAYPAL_AUTH_LOCKED
    assert contract.vault_id == "vault_orch_01"

    # 3. Submit clean pass deliverable -> captures and updates vault daily spent
    scenario = SCENARIOS["CLEAN_PASS"]
    settled = await orch.submit_work(
        contract.agreement_id,
        scenario.worker_code,
        scenario.buyer_test_code,
    )
    assert settled.fsm_state in (FsmState.SETTLED_COMPLETE, FsmState.PAYOUT_DISPATCHED)
    v = repo.get_vault("vault_orch_01")
    assert v.current_daily_spent_micros == 15_000_000
    assert v.consecutive_void_count == 0


@pytest.mark.asyncio
async def test_orchestrator_consecutive_voids_trip_circuit_breaker(tmp_path):
    cfg = Settings(
        _env_file=None,
        PAYPAL_MODE="mock",
        SANDBOX_MODE="subprocess",
        DATABASE_URL=f"sqlite:///{tmp_path}/orch_void_cb.db",
    )
    repo = Repository.from_url(cfg.DATABASE_URL)
    engine = MockPayPalEscrowEngine()
    ledger = DoubleEntryLedgerEngine(repository=repo)
    orch = EscrowOrchestrator(repo, ledger, engine, cfg, clock=lambda: NOW)

    register_vault(
        vault_id="vault_adversarial_worker",
        buyer_did="did:key:z6MkuWorkerLoop",
        max_amount_per_task_micros=25_000_000,
        max_daily_budget_micros=100_000_000,
        repo=repo,
    )

    malicious = SCENARIOS["MALICIOUS_INJECTION"]

    # Run 3 malicious submissions to trigger 3 consecutive voids
    for i in range(1, 4):
        c = await orch.create_contract(
            SlaContractCreate(
                agreement_id=f"agr_mal_{i}",
                buyer_agent_id="buyer_01",
                worker_agent_id="worker_malicious",
                worker_paypal_email="mal@example.com",
                budget_micros=10_000_000,
                task_description=f"Malicious attempt {i}",
                vault_id="vault_adversarial_worker",
            )
        )
        res = await orch.submit_work(c.agreement_id, malicious.worker_code, malicious.buyer_test_code)
        assert res.fsm_state == FsmState.ESCROW_VOIDED_REFUNDED

    # Vault should now be frozen!
    v = repo.get_vault("vault_adversarial_worker")
    assert v.consecutive_void_count == 3
    assert v.is_frozen == 1

    # 4th task attempt using this vaulted method must be blocked by the circuit breaker
    with pytest.raises(VaultCircuitBreakerError) as exc_info:
        await orch.create_contract(
            SlaContractCreate(
                agreement_id="agr_mal_blocked",
                buyer_agent_id="buyer_01",
                worker_agent_id="worker_01",
                worker_paypal_email="worker@example.com",
                budget_micros=5_000_000,
                task_description="Blocked due to tripped circuit breaker",
                vault_id="vault_adversarial_worker",
            )
        )
    assert "CIRCUIT_BREAKER_ACTIVE" in str(exc_info.value)


def test_vault_api_endpoints(env):
    client, app, repo, _ = env

    # 1. Create vault via POST /api/v1/vaults
    res = client.post(
        "/api/v1/vaults",
        json={
            "vault_id": "vault_api_test",
            "buyer_did": "did:key:z6MkuApi",
            "max_amount_per_task_micros": 20_000_000,
            "max_daily_budget_micros": 80_000_000,
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["vault_id"] == "vault_api_test"
    assert data["max_amount_per_task_micros"] == 20_000_000

    # 2. Get vault via GET /api/v1/vaults/{vault_id}
    res_get = client.get("/api/v1/vaults/vault_api_test")
    assert res_get.status_code == 200
    assert res_get.json()["buyer_did"] == "did:key:z6MkuApi"

    # 3. Manually freeze vault and test /reset endpoint
    v = repo.get_vault("vault_api_test")
    v.is_frozen = 1
    v.consecutive_void_count = 3
    repo.save_vault(v)

    res_reset = client.post("/api/v1/vaults/vault_api_test/reset")
    assert res_reset.status_code == 200
    assert res_reset.json()["status"] == "unfrozen"
    assert res_reset.json()["vault"]["is_frozen"] == 0
    assert res_reset.json()["vault"]["consecutive_void_count"] == 0


def test_mcp_propose_agreement_with_vault(env):
    client, app, repo, _ = env

    # Register vault with $15 per task limit
    register_vault(
        vault_id="vault_mcp_01",
        buyer_did="did:key:z6MkuMcp",
        max_amount_per_task_micros=15_000_000,
        max_daily_budget_micros=50_000_000,
        repo=repo,
    )

    # 1. Propose agreement exceeding task limit ($20 > $15)
    call_exceed = {
        "name": "aegispay_propose_agreement",
        "arguments": {
            "buyer_agent_id": "agent_buyer_mcp",
            "worker_agent_id": "agent_worker_mcp",
            "worker_paypal_email": "worker@example.com",
            "budget_micros": 20_000_000,
            "task_description": "MCP agreement proposal exceeding task limit",
            "vault_id": "vault_mcp_01",
        },
    }
    res_exceed = client.post("/api/v1/mcp/execute", json=call_exceed)
    assert res_exceed.status_code == 409
    assert "EXCEEDS_TASK_LIMIT" in res_exceed.text

    # 2. Propose agreement within task limit ($10 <= $15)
    call_valid = {
        "name": "aegispay_propose_agreement",
        "arguments": {
            "buyer_agent_id": "agent_buyer_mcp",
            "worker_agent_id": "agent_worker_mcp",
            "worker_paypal_email": "worker@example.com",
            "budget_micros": 10_000_000,
            "task_description": "Valid MCP agreement proposal with vault token",
            "vault_id": "vault_mcp_01",
        },
    }
    res_valid = client.post("/api/v1/mcp/execute", json=call_valid)
    assert res_valid.status_code == 200
    result = res_valid.json()["result"]
    assert result["fsm_state"] == "PAYPAL_AUTH_LOCKED"
    assert result["vault_id"] == "vault_mcp_01"
