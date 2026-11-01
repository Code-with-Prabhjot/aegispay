"""
backend/app/main.py
AegisPay FastAPI application.  Run:  uvicorn backend.app.main:app --reload
All routes live under /api/v1.
"""

from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.app.api.routes_webhooks import router as webhooks_router
from backend.app.config import Settings, settings as default_settings
from backend.app.db import Repository
from backend.app.schemas import SlaContractCreate
from backend.app.services import fsm
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.orchestrator import (
    EscrowOrchestrator,
    NotFoundError,
    UpstreamError,
)
from backend.app.services.paypal_mock import get_paypal_engine
from backend.app.services.scenario_presets import SCENARIOS


class SubmitWork(BaseModel):
    source_code: str = Field(..., min_length=1, max_length=200_000)
    test_suite: str = Field(..., min_length=1, max_length=200_000)


def contract_detail(c: fsm.ContractRecord) -> Dict[str, Any]:
    data = c.to_response().model_dump(mode="json")
    data["authorized_at"] = c.authorized_at.isoformat() if c.authorized_at else None
    data["transitions"] = [t.to_dict() for t in c.transitions]
    data["arbitration_report"] = c.report
    return data


def create_app(
    settings: Optional[Settings] = None,
    engine: Any = None,
    repo: Optional[Repository] = None,
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

    repository = repo or Repository.from_url(cfg.DATABASE_URL)
    paypal = engine or get_paypal_engine(cfg)
    ledger = DoubleEntryLedgerEngine.from_repository(repository)
    orch = EscrowOrchestrator(repository, ledger, paypal, cfg)
    app.state.repo = repository
    app.state.orchestrator = orch
    app.state.ledger = ledger

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
