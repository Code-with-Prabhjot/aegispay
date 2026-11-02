"""
backend/app/main.py
AegisPay FastAPI application.  Run:  uvicorn backend.app.main:app --reload
All routes live under /api/v1.
"""

from contextlib import asynccontextmanager
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.app.api.routes_webhooks import router as webhooks_router
from backend.app.config import Settings, settings as default_settings
from backend.app.db import Repository
from backend.app.schemas import (
    AgreementDetailResponse,
    AstSecurityAudit,
    LedgerJournalLeg,
    SlaContractCreate,
    TestAssertionItem,
    VaultVelocityModel,
)
from backend.app.services import fsm
from backend.app.services.broadcaster import (
    LedgerConnectionManager,
    format_event_envelope,
    ledger_broadcaster,
)
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.orchestrator import (
    EscrowOrchestrator,
    NotFoundError,
    UpstreamError,
)
from backend.app.services.paypal_mock import get_paypal_engine
from backend.app.services.scenario_presets import SCENARIOS
from backend.app.services.vault_service import (
    VaultCircuitBreakerError,
    register_vault,
    reset_vault_circuit_breaker,
)


class SubmitWork(BaseModel):
    source_code: str = Field(..., min_length=1, max_length=200_000)
    test_suite: str = Field(..., min_length=1, max_length=200_000)


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

    return AgreementDetailResponse(
        **base,
        test_assertions=assertions,
        ast_security_audit=ast_audit,
        ledger_journal=journal_legs,
        transitions=[t.to_dict() for t in c.transitions],
        arbitration_report=c.report,
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    broadcaster_mgr = broadcaster or ledger_broadcaster
    repository = repo or Repository.from_url(cfg.DATABASE_URL)
    paypal = engine or get_paypal_engine(cfg)
    ledger = DoubleEntryLedgerEngine.from_repository(repository, broadcaster=broadcaster_mgr)
    orch = EscrowOrchestrator(repository, ledger, paypal, cfg, broadcaster=broadcaster_mgr)
    app.state.repo = repository
    app.state.orchestrator = orch
    app.state.ledger = ledger
    app.state.broadcaster = broadcaster_mgr

    api = APIRouter(prefix="/api/v1")

    def _handle(exc: Exception) -> HTTPException:
        if isinstance(exc, NotFoundError):
            return HTTPException(404, f"Not found: {exc}")
        if isinstance(exc, fsm.AuthorizationExpiredError):
            return HTTPException(409, str(exc))
        if isinstance(exc, fsm.FsmError):
            return HTTPException(409, str(exc))
        if isinstance(exc, UpstreamError):
            return HTTPException(502, str(exc))
        raise exc

    @api.get("/health")
    async def health() -> Dict[str, Any]:
        mock = bool(getattr(paypal, "is_mock", False))
        return {"status": "ok", "paypal_mode": "mock" if mock else cfg.PAYPAL_MODE, "mock": mock}

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
            c = await orch.submit_work(agreement_id, body.source_code, body.test_suite)
        except (NotFoundError, fsm.FsmError, UpstreamError) as exc:
            raise _handle(exc)
        return contract_detail(c)

    @api.post("/contracts/{agreement_id}/check-expiry")
    async def check_expiry(agreement_id: str) -> Dict[str, Any]:
        try:
            return contract_detail(await orch.check_expiry(agreement_id))
        except NotFoundError as exc:
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

    @api.get("/mcp/manifest")
    async def get_mcp_manifest() -> Dict[str, Any]:
        manifest_path = Path(__file__).resolve().parent.parent.parent / "aegispay_mcp.json"
        if manifest_path.exists():
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        raise HTTPException(500, "MCP manifest file not found")

    @app.get("/mcp/manifest")
    async def get_mcp_manifest_root() -> Dict[str, Any]:
        return await get_mcp_manifest()

    class McpToolCall(BaseModel):
        name: str
        arguments: Dict[str, Any]

    @api.post("/mcp/execute")
    async def execute_mcp_tool(call: McpToolCall) -> Dict[str, Any]:
        try:
            if call.name == "aegispay_propose_agreement":
                payload = SlaContractCreate(**call.arguments)
                contract = await orch.create_contract(payload)
                return {"result": contract_detail(contract)}

            elif call.name == "aegispay_submit_deliverable":
                contract = await orch.submit_work(
                    agreement_id=call.arguments["agreement_id"],
                    source_code=call.arguments["source_code"],
                    test_suite=call.arguments["test_suite"],
                )
                return {"result": contract_detail(contract)}

            elif call.name == "aegispay_check_escrow_status":
                agreement_id = call.arguments["agreement_id"]
                contract = orch.get(agreement_id)
                detail = build_agreement_detail_response(contract, repository, ledger)
                return {"result": detail.model_dump(mode="json")}

            else:
                raise HTTPException(404, f"Unknown MCP tool '{call.name}'")
        except Exception as exc:
            raise _handle(exc)

    async def _ws_ledger_handler(websocket: WebSocket) -> None:
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

    api.add_api_websocket_route("/ws/ledger", _ws_ledger_handler)
    api.add_api_websocket_route("/ws/dashboard", _ws_ledger_handler)
    app.add_api_websocket_route("/ws/dashboard", _ws_ledger_handler)

    api.include_router(webhooks_router)
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
