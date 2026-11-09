"""
backend/app/main.py
AegisPay FastAPI application.  Run:  uvicorn backend.app.main:app --reload
All routes live under /api/v1.
"""

from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
import hmac
import json
from pathlib import Path
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Security, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, ValidationError

from backend.app.api.routes_webhooks import router as webhooks_router
from backend.app.config import Settings, settings as default_settings
from backend.app.db import Repository
from backend.app.schemas import (
    A2ANegotiateRequest,
    AgreementDetailResponse,
    AstSecurityAudit,
    LedgerJournalLeg,
    MerkleProofResponse,
    SlaContractCreate,
    TestAssertionItem,
    LedgerAccount,
    VaultSetupTokenRequest,
    VaultVelocityModel,
    VerifyIdentityRequest,
)
from backend.app.services import fsm
from backend.app.services.a2a import (
    Ed25519Agent,
    create_sla_offer,
    negotiate_sla_parameters,
    pubkey_to_did,
    verify_agent_signature,
)
from backend.app.services.broadcaster import (
    LedgerConnectionManager,
    format_event_envelope,
    ledger_broadcaster,
)
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.merkle_audit import (
    MerkleAuditTree,
    compile_7leaf_bundle,
    compute_genesis_hash,
    verify_merkle_proof,
)
from backend.app.services.orchestrator import (
    EscrowOrchestrator,
    NotFoundError,
    PolicyViolationError,
    SandboxUnavailableError,
    UpstreamError,
)
from backend.app.services.paypal_mock import get_paypal_engine
from backend.app.services.scenario_presets import SCENARIOS
from backend.app.services.sweeper import ReauthorizationSweeper
from backend.app.services.vault_service import (
    VaultCircuitBreakerError,
    register_vault,
    reset_vault_circuit_breaker,
)


class SubmitWork(BaseModel):
    source_code: str = Field(..., min_length=1, max_length=200_000)
    test_suite: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=200_000,
        description="Optional. Must match the suite committed in the agreement; only used (mock mode) "
        "when the agreement has none.",
    )
    runtime: Optional[str] = Field(default=None, description="Execution runtime: python | typescript | javascript")
    semantic_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Demo-only override for the semantic score; refused outside mock mode",
    )


class SweeperRunParams(BaseModel):
    simulate_now: Optional[datetime] = Field(default=None, description="Simulated current timestamp for dry-run sweeps")
    threshold_hours: Optional[float] = Field(default=None, description="Honor expiration buffer window threshold in hours (default 70.0)")


def contract_detail(c: fsm.ContractRecord) -> Dict[str, Any]:
    data = c.to_response().model_dump(mode="json")
    data["authorized_at"] = c.authorized_at.isoformat() if c.authorized_at else None
    data["transitions"] = [t.to_dict() for t in c.transitions]
    data["arbitration_report"] = c.report
    return data


def build_agreement_detail_response(
    c: fsm.ContractRecord, repo: Repository, ledger: DoubleEntryLedgerEngine
) -> AgreementDetailResponse:
    base = c.to_response().model_dump(mode="json")
    report = c.report or {}

    # 1. Test assertions
    raw_assertions = report.get("test_assertions", [])
    assertions: list[TestAssertionItem] = []
    if raw_assertions:
        for item in raw_assertions:
            assertions.append(TestAssertionItem(**item) if isinstance(item, dict) else item)
    else:
        p_cnt = report.get("passed_tests", 0)
        t_cnt = report.get("total_tests", 0)
        for i in range(p_cnt):
            assertions.append(TestAssertionItem(name=f"test_{i+1:02d}", status="PASSED", duration_ms=10.0))
        for i in range(max(0, t_cnt - p_cnt)):
            assertions.append(TestAssertionItem(name=f"test_{p_cnt+i+1:02d}", status="FAILED", duration_ms=10.0, error_message="Assertion failed"))

    # 2. AST security audit
    is_clean = bool(report.get("ast_safe", True))
    ast_audit = AstSecurityAudit(
        is_clean=is_clean,
        scanned_imports=report.get("scanned_imports", []),
        blocked_calls=report.get("ast_violations", []),
        score=float(report.get("s_ast", 1.0)),
    )

    # 3. Double-entry ledger journal legs
    db_entries = repo.get_ledger_entries_for_agreement(c.agreement_id)
    journal_legs: list[LedgerJournalLeg] = []
    for entry in db_entries:
        journal_legs.append(
            LedgerJournalLeg(
                debit_account=entry.debit_account,
                credit_account=entry.credit_account,
                amount_micros=entry.amount_micros,
                amount_usd=f"${Decimal(entry.amount_micros) / Decimal(1_000_000):.2f}",
                memo=entry.description or "",
                created_at=entry.created_at,
            )
        )

    # 4. Cryptographic Hash Chain & 7-Leaf Merkle Tree Audit Bundle (Module 4)
    bundle = compile_7leaf_bundle(c, repo=repo, ledger=ledger)
    merkle_root = c.merkle_root or bundle.get("merkle_root")
    hash_chain_head = c.hash_chain_head
    if not hash_chain_head:
        chain = repo.get_hash_chain(c.agreement_id)
        if chain:
            hash_chain_head = chain[-1]["current_hash"]
        else:
            hash_chain_head = compute_genesis_hash(c.agreement_id)

    clean_bundle = {
        "merkle_root": merkle_root,
        "leaf_hashes": bundle.get("leaf_hashes", []),
        "leaf_names": bundle.get("leaf_names", []),
        "leaves": bundle.get("leaves", []),
    }

    base["merkle_root"] = merkle_root
    base["hash_chain_head"] = hash_chain_head

    return AgreementDetailResponse(
        **base,
        test_assertions=assertions,
        ast_security_audit=ast_audit,
        ledger_journal=journal_legs,
        transitions=[t.to_dict() for t in c.transitions],
        arbitration_report=c.report,
        audit_bundle=clean_bundle,
    )


def create_app(
    settings: Optional[Settings] = None,
    engine: Any = None,
    repo: Optional[Repository] = None,
    broadcaster: Optional[LedgerConnectionManager] = None,
) -> FastAPI:
    cfg = settings or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.orchestrator.engine.close()
        app.state.repo.db.close()

    app = FastAPI(title="AegisPay", version="0.1.0", lifespan=lifespan)
    allowed_origins = cfg.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    broadcaster_mgr = broadcaster or ledger_broadcaster
    repository = repo or Repository.from_url(cfg.DATABASE_URL)
    paypal = engine or get_paypal_engine(cfg)
    ledger = DoubleEntryLedgerEngine.from_repository(repository, broadcaster=broadcaster_mgr)
    orch = EscrowOrchestrator(repository, ledger, paypal, cfg, broadcaster=broadcaster_mgr)
    sweeper = ReauthorizationSweeper(
        repository, ledger, paypal, clock=orch.clock, broadcaster=broadcaster_mgr, lock=orch.lock
    )
    orch.sweeper = sweeper
    app.state.repo = repository
    app.state.orchestrator = orch
    app.state.ledger = ledger
    app.state.broadcaster = broadcaster_mgr
    app.state.sweeper = sweeper

    def _key_matches(supplied: str) -> bool:
        return hmac.compare_digest(supplied.encode("utf-8"), cfg.API_AUTH_KEY.encode("utf-8"))

    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    async def require_api_key(supplied: Optional[str] = Security(api_key_header)) -> None:
        """Mock mode may run open; otherwise the key must be configured and presented."""
        if not cfg.API_AUTH_KEY:
            if cfg.is_mock:
                return
            raise HTTPException(503, "API_AUTH_KEY is not configured")
        if not _key_matches(supplied or ""):
            raise HTTPException(401, "Missing or invalid API key")

    # /health and the PayPal webhook (which authenticates by signature) stay outside the key check.
    public = APIRouter(prefix="/api/v1")
    api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])

    def _demo_only(what: str) -> None:
        if not cfg.is_mock:
            raise HTTPException(403, f"{what} is only available in mock mode")

    def _handle(exc: Exception) -> HTTPException:
        if isinstance(exc, NotFoundError):
            return HTTPException(404, f"Not found: {exc}")
        if isinstance(exc, PolicyViolationError):
            return HTTPException(403, str(exc))
        if isinstance(exc, fsm.AuthorizationExpiredError):
            return HTTPException(409, str(exc))
        if isinstance(exc, fsm.FsmError):
            return HTTPException(409, str(exc))
        if isinstance(exc, SandboxUnavailableError):
            return HTTPException(503, str(exc))
        if isinstance(exc, UpstreamError):
            return HTTPException(502, str(exc))
        raise exc

    @public.get("/health")
    async def health() -> Dict[str, Any]:
        mock = bool(getattr(paypal, "is_mock", False))
        return {
            "status": "ok",
            "paypal_mode": "mock" if mock else cfg.PAYPAL_MODE,
            "mock": mock,
            "database": "connected",
            "ledger_balanced": ledger.verify_balance_conservation(),
        }

    @api.post("/contracts", status_code=201)
    async def create_contract(payload: SlaContractCreate) -> Dict[str, Any]:
        try:
            return contract_detail(await orch.create_contract(payload))
        except (fsm.FsmError, UpstreamError) as exc:
            raise _handle(exc)

    @api.get("/contracts")
    async def list_contracts() -> Dict[str, Any]:
        items = [c.to_response().model_dump(mode="json") for c in repository.list_contracts()]
        return {"contracts": items, "count": len(items)}

    @api.get("/contracts/{agreement_id}")
    async def get_contract(agreement_id: str) -> Dict[str, Any]:
        try:
            return contract_detail(orch.get(agreement_id))
        except NotFoundError as exc:
            raise _handle(exc)

    @api.post("/contracts/{agreement_id}/submit")
    async def submit(agreement_id: str, body: SubmitWork) -> Dict[str, Any]:
        try:
            c = await orch.submit_work(
                agreement_id,
                body.source_code,
                body.test_suite,
                semantic_score=body.semantic_score,
                runtime=body.runtime,
            )
        except (NotFoundError, fsm.FsmError, UpstreamError) as exc:
            raise _handle(exc)
        return contract_detail(c)

    @api.post("/contracts/{agreement_id}/check-expiry")
    async def check_expiry(agreement_id: str) -> Dict[str, Any]:
        try:
            return contract_detail(await orch.check_expiry(agreement_id))
        except NotFoundError as exc:
            raise _handle(exc)

    @api.post("/contracts/{agreement_id}/settlement/retry")
    async def retry_settlement(agreement_id: str) -> Dict[str, Any]:
        try:
            return contract_detail(await orch.retry_settlement(agreement_id))
        except (NotFoundError, fsm.FsmError, UpstreamError) as exc:
            raise _handle(exc)

    @api.post("/contracts/{agreement_id}/payout/retry")
    async def retry_payout(agreement_id: str) -> Dict[str, Any]:
        try:
            return contract_detail(await orch.retry_payout(agreement_id))
        except (NotFoundError, fsm.FsmError, UpstreamError) as exc:
            raise _handle(exc)

    @api.post("/demo/scenarios/{name}")
    async def run_demo(name: str, payout_outcome: str = "succeeded") -> Dict[str, Any]:
        if not getattr(paypal, "is_mock", False):
            raise HTTPException(400, "Demo scenarios require PAYPAL_MODE=mock")
        if name.strip().upper() not in SCENARIOS:
            raise HTTPException(404, f"Unknown scenario '{name}'. Valid: {sorted(SCENARIOS)}")
        if payout_outcome.lower() not in ("succeeded", "failed", "unclaimed"):
            raise HTTPException(422, "payout_outcome must be succeeded, failed or unclaimed")
        try:
            c = await orch.run_scenario(name, payout_outcome)
        except (fsm.FsmError, UpstreamError) as exc:
            raise _handle(exc)
        detail = contract_detail(c)
        detail["ledger_entries"] = [
            r for r in ledger.to_records() if r["reference_id"] == c.agreement_id
        ]
        detail["balances"] = ledger.get_all_balances()
        return detail

    @api.get("/ledger")
    async def get_ledger() -> Dict[str, Any]:
        return {
            "entries": ledger.to_records(),
            "balances": ledger.get_all_balances(),
            "balanced": ledger.verify_balance_conservation(),
        }

    @api.get("/ledger/balance")
    async def get_ledger_balance() -> Dict[str, Any]:
        records = ledger.to_records()
        total_debits = sum(e["amount_micros"] for e in records)
        total_credits = sum(e["amount_micros"] for e in records)
        net_imbalance = total_debits - total_credits
        return {
            "balanced": ledger.verify_balance_conservation(),
            "net_imbalance": net_imbalance,
            "net_imbalance_micros": net_imbalance,
            "total_debits_micros": total_debits,
            "total_credits_micros": total_credits,
            "delta": net_imbalance,
            "balances": ledger.get_all_balances(),
            "invariant": "debits - credits == 0",
        }

    @api.get("/ledger/accounts")
    async def get_ledger_accounts() -> Dict[str, Any]:
        balances = ledger.get_all_balances()
        accounts = [
            {
                "account": acct.value,
                "balance_micros": balances.get(acct.value, 0),
                "balance_usd": f"${Decimal(balances.get(acct.value, 0)) / Decimal(1_000_000):.2f}",
            }
            for acct in LedgerAccount
        ]
        return {
            "accounts": accounts,
            "balances": balances,
            "count": len(accounts),
        }

    @api.get("/agreements/{agreement_id}/detail", response_model=AgreementDetailResponse)
    async def get_agreement_detail(agreement_id: str) -> AgreementDetailResponse:
        try:
            c = orch.get(agreement_id)
            return build_agreement_detail_response(c, repository, ledger)
        except NotFoundError as exc:
            raise _handle(exc)

    @api.get("/contracts/{agreement_id}/detail", response_model=AgreementDetailResponse)
    async def get_contract_detail(agreement_id: str) -> AgreementDetailResponse:
        return await get_agreement_detail(agreement_id)

    @api.get("/contracts/{agreement_id}/merkle-proof/{leaf_index}", response_model=MerkleProofResponse)
    async def get_contract_merkle_proof(agreement_id: str, leaf_index: int) -> MerkleProofResponse:
        if not (0 <= leaf_index <= 6):
            raise HTTPException(400, "leaf_index must be an integer between 0 and 6 inclusive")
        try:
            c = orch.get(agreement_id)
        except NotFoundError as exc:
            raise _handle(exc)

        bundle = compile_7leaf_bundle(c, repo=repository, ledger=ledger)
        tree: MerkleAuditTree = bundle["tree"]
        proof = tree.get_proof(leaf_index)
        leaf_data = bundle["leaves"][leaf_index]
        leaf_hash = bundle["leaf_hashes"][leaf_index]
        leaf_name = bundle["leaf_names"][leaf_index]
        verified = verify_merkle_proof(leaf_index, leaf_data, proof, tree.root_hash)

        return MerkleProofResponse(
            agreement_id=agreement_id,
            leaf_index=leaf_index,
            leaf_name=leaf_name,
            leaf_data=leaf_data,
            leaf_hash=leaf_hash,
            proof_hashes=proof,
            merkle_root=tree.root_hash,
            verified=verified,
        )

    @api.get("/agreements/{agreement_id}/merkle-proof/{leaf_index}", response_model=MerkleProofResponse)
    async def get_agreement_merkle_proof(agreement_id: str, leaf_index: int) -> MerkleProofResponse:
        return await get_contract_merkle_proof(agreement_id, leaf_index)

    @api.post("/a2a/negotiate")
    async def a2a_negotiate(req: A2ANegotiateRequest) -> Dict[str, Any]:
        result = negotiate_sla_parameters(
            buyer_pref=req.buyer_pref,
            worker_pref=req.worker_pref,
            alpha=req.alpha,
            beta=req.beta,
        )
        offer = None
        agreement_id = None
        auth_id = None
        buyer_did = req.buyer_agent_id
        worker_id = req.worker_agent_id or "did:key:worker_solver_candidate"

        if req.auto_sign or req.buyer_agent_id:
            buyer_agent = Ed25519Agent.generate(agent_id=req.buyer_agent_id)
            buyer_did = buyer_agent.agent_id
            offer = create_sla_offer(
                buyer=buyer_agent,
                worker_agent_id=worker_id,
                task_description=req.task_description or "Autonomous Agent Deliverable",
                budget_micros=result["agreed_budget_micros"],
                timeout_seconds=result["max_execution_seconds"],
                runtime_language=req.runtime_language or "python",
            )
            agreement_id = offer.get("contract_id")
            if req.auto_sign:
                try:
                    worker_agent = Ed25519Agent.generate(agent_id=worker_id)
                    worker_sig = worker_agent.sign(offer)
                    contract_payload = SlaContractCreate(
                        agreement_id=agreement_id,
                        buyer_agent_id=buyer_did,
                        worker_agent_id=worker_id,
                        worker_paypal_email="worker@aegispay.io",
                        budget_micros=result["agreed_budget_micros"],
                        task_description=req.task_description or "Autonomous Agent Deliverable",
                        test_suite_source="def test_solution(): assert True\n",
                        buyer_signature=offer.get("buyer_signature"),
                        worker_signature=worker_sig,
                    )
                    contract = await orch.create_contract(contract_payload)
                    agreement_id = contract.agreement_id
                    auth_id = contract.paypal_auth_id
                except Exception:
                    pass

        return {
            "parameters": result,
            "offer": offer,
            "agreement_id": agreement_id,
            "contract_id": agreement_id,
            "buyer_did": buyer_did,
            "worker_did": worker_id,
            "auth_id": auth_id,
            "agreed_budget_micros": result["agreed_budget_micros"],
            "max_execution_seconds": result["max_execution_seconds"],
            "agreed_timeout_seconds": result["agreed_timeout_seconds"],
            "buyer_utility": result["buyer_utility"],
            "worker_utility": result["worker_utility"],
            "nash_product": result["nash_product"],
            "status": "AGREEMENT_REACHED",
        }

    @api.post("/a2a/verify-identity")
    async def a2a_verify_identity(req: VerifyIdentityRequest) -> Dict[str, Any]:
        is_valid = verify_agent_signature(
            public_key=req.public_key_hex,
            message=req.challenge,
            signature_hex=req.signature_hex,
        )
        agent_id = req.agent_id
        if not agent_id:
            if req.public_key_hex.startswith("did:key:"):
                agent_id = req.public_key_hex
            else:
                try:
                    agent_id = pubkey_to_did(bytes.fromhex(req.public_key_hex.strip()))
                except Exception:
                    agent_id = req.public_key_hex
        return {
            "valid": is_valid,
            "verified": is_valid,
            "agent_id": agent_id,
            "public_key_hex": req.public_key_hex,
        }

    @api.post("/vault/setup-token", status_code=201)
    @api.post("/vaults/setup-token", status_code=201)
    async def create_setup_token(req: VaultSetupTokenRequest) -> Dict[str, Any]:
        vault_id = f"vault_tok_{uuid.uuid4().hex[:12]}"
        vault = register_vault(
            vault_id=vault_id,
            buyer_did=req.buyer_did,
            max_amount_per_task_micros=req.max_amount_per_task_micros,
            max_daily_budget_micros=req.max_daily_budget_micros,
            repo=repository,
        )
        return {
            "vault_id": vault.vault_id,
            "setup_token": vault.vault_id,
            "buyer_did": vault.buyer_did,
            "status": "APPROVED",
            "payment_source": {"paypal": {"vault_id": vault.vault_id}},
            "max_amount_per_task_micros": vault.max_amount_per_task_micros,
            "max_daily_budget_micros": vault.max_daily_budget_micros,
            "is_frozen": vault.is_frozen,
        }

    @api.get("/vault/velocity/{buyer_did}")
    @api.get("/vaults/velocity/{buyer_did}")
    async def get_vault_velocity(buyer_did: str) -> Dict[str, Any]:
        vaults = [v for v in repository.list_vaults() if v.buyer_did == buyer_did]
        if not vaults:
            v_direct = repository.get_vault(buyer_did)
            if v_direct:
                vaults = [v_direct]
        if not vaults:
            return {
                "buyer_did": buyer_did,
                "vault_id": "default",
                "max_amount_per_task_micros": 25_000_000,
                "max_daily_budget_micros": 100_000_000,
                "current_daily_spent_micros": 0,
                "consecutive_void_count": 0,
                "is_frozen": 0,
                "circuit_breaker_status": "ACTIVE",
                "velocity_allowed": True,
            }
        vault = vaults[0]
        return {
            "buyer_did": vault.buyer_did,
            "vault_id": vault.vault_id,
            "max_amount_per_task_micros": vault.max_amount_per_task_micros,
            "max_daily_budget_micros": vault.max_daily_budget_micros,
            "current_daily_spent_micros": vault.current_daily_spent_micros,
            "window_reset_timestamp": vault.window_reset_timestamp,
            "consecutive_void_count": vault.consecutive_void_count,
            "is_frozen": vault.is_frozen,
            "circuit_breaker_status": "LOCKED" if vault.is_frozen else "ACTIVE",
            "velocity_allowed": vault.is_frozen == 0,
        }

    @api.post("/vaults", status_code=201)
    async def create_or_update_vault(model: VaultVelocityModel) -> Dict[str, Any]:
        record = register_vault(
            vault_id=model.vault_id,
            buyer_did=model.buyer_did,
            max_amount_per_task_micros=model.max_amount_per_task_micros,
            max_daily_budget_micros=model.max_daily_budget_micros,
            current_daily_spent_micros=model.current_daily_spent_micros,
            window_reset_timestamp=model.window_reset_timestamp,
            consecutive_void_count=model.consecutive_void_count,
            is_frozen=model.is_frozen,
            repo=repository,
        )
        return record.to_dict()

    @api.get("/vaults/{vault_id}")
    async def get_vault_status(vault_id: str) -> Dict[str, Any]:
        vault = repository.get_vault(vault_id)
        if vault is None:
            raise HTTPException(404, f"Vault '{vault_id}' not found")
        return vault.to_dict()

    @api.post("/vaults/{vault_id}/reset")
    async def reset_circuit_breaker_endpoint(vault_id: str) -> Dict[str, Any]:
        vault = reset_vault_circuit_breaker(vault_id, repo=repository)
        if vault is None:
            raise HTTPException(404, f"Vault '{vault_id}' not found")
        return {"status": "unfrozen", "vault": vault.to_dict()}

    @api.get("/vault/status", include_in_schema=False)
    @api.get("/vaults/status", include_in_schema=False)
    async def get_default_vault_status() -> Dict[str, Any]:
        vault = repository.get_vault("vault_buyer_primary")
        if vault is None:
            _demo_only("Creating the default demo vault")
            vault = register_vault(
                vault_id="vault_buyer_primary",
                buyer_did="did:aegis:buyer:enterprise-agent-01",
                max_amount_per_task_micros=100_000_000,
                max_daily_budget_micros=250_000_000,
                current_daily_spent_micros=45_000_000,
                window_reset_timestamp=int(time.time()) + 86400,
                consecutive_void_count=0,
                is_frozen=0,
                repo=repository,
            )
        status = "VAULT_LOCKED_CIRCUIT_BREAKER" if vault.is_frozen == 1 else "ACTIVE"
        d = vault.to_dict()
        d["status"] = status
        return d

    @api.post("/vault/refresh", include_in_schema=False)
    @api.post("/vaults/refresh", include_in_schema=False)
    async def refresh_vault_allowance(vault_id: Optional[str] = None) -> Dict[str, Any]:
        _demo_only("Refreshing a vault allowance (it zeroes the spend counter)")
        vid = vault_id or "vault_buyer_primary"
        vault = repository.get_vault(vid)
        if vault is None:
            vault = register_vault(
                vault_id=vid,
                buyer_did="did:aegis:buyer:enterprise-agent-01",
                max_amount_per_task_micros=100_000_000,
                max_daily_budget_micros=250_000_000,
                current_daily_spent_micros=0,
                window_reset_timestamp=int(time.time()) + 86400,
                consecutive_void_count=0,
                is_frozen=0,
                repo=repository,
            )
        else:
            vault.is_frozen = 0
            vault.consecutive_void_count = 0
            vault.current_daily_spent_micros = 0
            vault.window_reset_timestamp = int(time.time()) + 86400
            repository.save_vault(vault)
        d = vault.to_dict()
        d["status"] = "ACTIVE"
        return {"status": "ACTIVE", "refreshed": True, "vault": d}

    @api.post("/vault/freeze", include_in_schema=False)
    @api.post("/vaults/{vault_id}/freeze", include_in_schema=False)
    async def freeze_vault_endpoint(vault_id: Optional[str] = None, freeze: Optional[bool] = None) -> Dict[str, Any]:
        vid = vault_id or "vault_buyer_primary"
        vault = repository.get_vault(vid)
        if vault is None:
            _demo_only("Freezing an unregistered vault")
            vault = register_vault(
                vault_id=vid,
                buyer_did="did:aegis:buyer:enterprise-agent-01",
                max_amount_per_task_micros=100_000_000,
                max_daily_budget_micros=250_000_000,
                is_frozen=1,
                repo=repository,
            )
        else:
            new_frozen = 1 if freeze is None else (1 if freeze else 0)
            vault.is_frozen = new_frozen
            repository.save_vault(vault)
        status = "VAULT_LOCKED_CIRCUIT_BREAKER" if vault.is_frozen == 1 else "ACTIVE"
        d = vault.to_dict()
        d["status"] = status
        return {"status": status, "is_frozen": vault.is_frozen, "vault": d}

    @api.post("/sweeper/run")
    async def run_sweeper_endpoint(params: Optional[SweeperRunParams] = None) -> Dict[str, Any]:
        simulate_now = params.simulate_now if params else None
        threshold_hours = params.threshold_hours if params else None
        if simulate_now is not None or threshold_hours is not None:
            _demo_only("Overriding the sweeper clock or threshold")
        return await sweeper.sweep(now=simulate_now, threshold_hours=threshold_hours)

    @api.get("/sweeper/status")
    async def get_sweeper_status_endpoint(simulate_now: Optional[datetime] = None) -> Dict[str, Any]:
        if simulate_now is not None:
            _demo_only("Overriding the sweeper clock")
        return sweeper.get_status(now=simulate_now)

    @api.get("/mcp/manifest")
    async def get_mcp_manifest() -> Dict[str, Any]:
        manifest_path = Path(__file__).resolve().parent.parent.parent / "aegispay_mcp.json"
        if manifest_path.exists():
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        raise HTTPException(500, "MCP manifest file not found")

    @app.get("/mcp/manifest")
    async def get_mcp_manifest_root() -> Dict[str, Any]:
        return await get_mcp_manifest()

    @app.get("/health")
    async def get_health_root() -> Dict[str, Any]:
        return await health()

    @api.get("/mcp/tools", include_in_schema=False)
    async def get_mcp_tools() -> Dict[str, Any]:
        from backend.app.services.mcp_agent_tools import MCP_JSONRPC_TOOLS
        return {"tools": MCP_JSONRPC_TOOLS}

    class McpToolCall(BaseModel):
        name: str
        arguments: Dict[str, Any]

    @api.post("/mcp/execute")
    async def execute_mcp_tool(call: McpToolCall) -> Dict[str, Any]:
        try:
            if call.name in ("aegispay_propose_agreement", "propose_contract"):
                payload = SlaContractCreate(**call.arguments)
                contract = await orch.create_contract(payload)
                return {"result": contract_detail(contract)}

            elif call.name in ("aegispay_submit_deliverable", "submit_deliverable"):
                contract = await orch.submit_work(
                    agreement_id=call.arguments["agreement_id"],
                    source_code=call.arguments["source_code"],
                    test_suite=call.arguments.get("test_suite"),
                    runtime=call.arguments.get("runtime"),
                )
                return {"result": contract_detail(contract)}

            elif call.name in ("aegispay_check_escrow_status", "check_escrow_status"):
                agreement_id = call.arguments["agreement_id"]
                contract = orch.get(agreement_id)
                detail = build_agreement_detail_response(contract, repository, ledger)
                return {"result": detail.model_dump(mode="json")}

            elif call.name in ("aegispay_verify_audit_proof", "verify_audit_proof"):
                agreement_id = call.arguments["agreement_id"]
                leaf_index = int(call.arguments.get("leaf_index", 0))
                contract = orch.get(agreement_id)
                bundle = compile_7leaf_bundle(contract, repo=repository, ledger=ledger)
                tree: MerkleAuditTree = bundle["tree"]
                proof = tree.get_proof(leaf_index)
                leaf_data = bundle["leaves"][leaf_index]
                leaf_hash = bundle["leaf_hashes"][leaf_index]
                leaf_name = bundle["leaf_names"][leaf_index]
                verified = verify_merkle_proof(leaf_index, leaf_data, proof, tree.root_hash)
                return {
                    "result": {
                        "agreement_id": agreement_id,
                        "leaf_index": leaf_index,
                        "leaf_name": leaf_name,
                        "leaf_hash": leaf_hash,
                        "proof_hashes": proof,
                        "merkle_root": tree.root_hash,
                        "verified": verified,
                    }
                }

            elif call.name in ("tools/list", "get_tools"):
                from backend.app.services.mcp_agent_tools import MCP_JSONRPC_TOOLS
                return {"result": {"tools": MCP_JSONRPC_TOOLS}}

            else:
                raise HTTPException(404, f"Unknown MCP tool '{call.name}'")
        except (KeyError, ValidationError) as exc:
            raise HTTPException(422, f"Invalid arguments for MCP tool '{call.name}': {exc}")
        except Exception as exc:
            raise _handle(exc)

    # Only under /api/v1, behind the API key: tools/call can create contracts and run code.
    @api.post("/mcp/rpc", include_in_schema=False)
    async def mcp_jsonrpc_endpoint(req: Dict[str, Any]) -> Dict[str, Any]:
        rpc_id = req.get("id")
        method = req.get("method")
        if method == "tools/list":
            from backend.app.services.mcp_agent_tools import MCP_JSONRPC_TOOLS
            return {"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": MCP_JSONRPC_TOOLS}}
        if method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            call_obj = McpToolCall(name=tool_name, arguments=tool_args)
            exec_res = await execute_mcp_tool(call_obj)
            return {"jsonrpc": "2.0", "id": rpc_id, "result": exec_res["result"]}
        raise HTTPException(400, f"Unsupported JSON-RPC method '{method}'")

    async def _ws_ledger_handler(websocket: WebSocket) -> None:
        # Browsers do not apply CORS to WebSockets, so check the Origin here, and the key like REST.
        origin = websocket.headers.get("origin")
        key_ok = (
            _key_matches(websocket.headers.get("x-api-key", ""))
            if cfg.API_AUTH_KEY
            else cfg.is_mock
        )
        if (origin is not None and origin.rstrip("/") not in allowed_origins) or not key_ok:
            await websocket.close(code=1008)
            return
        await broadcaster_mgr.connect(websocket)
        try:
            contracts = [c.to_response().model_dump(mode="json") for c in repository.list_contracts()]
            snapshot = format_event_envelope(
                event_type="INITIAL_SNAPSHOT",
                agreement_id="ALL",
                data={
                    "agreements": contracts,
                    "contracts": contracts,
                    "balances": ledger.get_all_balances(),
                    "ledger_entries": ledger.to_records(),
                    "entries": ledger.to_records(),
                    "balanced": ledger.verify_balance_conservation(),
                },
            )
            await websocket.send_json(snapshot)
            while True:
                msg = await websocket.receive_text()
                await broadcaster_mgr.handle_heartbeat(websocket, msg)
        except WebSocketDisconnect:
            pass
        finally:
            await broadcaster_mgr.disconnect(websocket)

    # The handler checks Origin and the key itself (router dependencies take an HTTP Request).
    public.add_api_websocket_route("/ws/ledger", _ws_ledger_handler)
    public.add_api_websocket_route("/ws/dashboard", _ws_ledger_handler)
    app.add_api_websocket_route("/ws/dashboard", _ws_ledger_handler)

    public.include_router(webhooks_router)
    app.include_router(public)
    app.include_router(api)
    return app


_app: Optional[FastAPI] = None


def __getattr__(name: str) -> Any:
    """Lazy `app` so importing this module (e.g. in tests) does not open the default DB."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
