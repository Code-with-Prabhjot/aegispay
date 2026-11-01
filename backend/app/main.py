"""
AegisPay API. Run from repo root:  uvicorn backend.app.main:app --reload --port 8000

State is in-memory (orchestrator). The DB teammate can persist via EscrowOrchestrator(on_transition=...).
Uses MockPayPalEngine unless USE_MOCK_PAYPAL=false and PayPal keys are set.
"""
import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.app.config import settings
from backend.app.schemas import SlaContractCreate
from backend.app.services.orchestrator import (
    EscrowOrchestrator,
    IllegalTransitionError,
    MockPayPalEngine,
    OrchestratorError,
    UnknownAgreementError,
)
from backend.app.services.scenario_presets import SCENARIOS, get_scenario


def build_paypal():
    if os.getenv("USE_MOCK_PAYPAL", "true").lower() != "false" or not settings.PAYPAL_CLIENT_ID:
        return MockPayPalEngine()
    from backend.app.services.paypal_escrow import PayPalEscrowEngine
    return PayPalEscrowEngine(
        settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET,
        settings.PAYPAL_WEBHOOK_ID, settings.is_sandbox,
    )


app = FastAPI(title="AegisPay", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
orch = EscrowOrchestrator(paypal=build_paypal())


class RunJobRequest(BaseModel):
    worker_code: str = Field(..., min_length=1)
    buyer_test_code: str = Field(..., min_length=1)
    semantic_score: Optional[float] = Field(None, ge=0.0, le=1.0)


class LockRequest(BaseModel):
    fund_buyer_vault: bool = False  # demo only: simulates buyer top-up


async def _guard(coro):
    try:
        return await coro
    except UnknownAgreementError:
        raise HTTPException(404, "Unknown agreement")
    except IllegalTransitionError as e:
        raise HTTPException(409, str(e))
    except OrchestratorError as e:
        raise HTTPException(422, str(e))


@app.get("/health")
def health():
    return {"status": "ok", "paypal": type(orch.paypal).__name__}


@app.post("/agreements")
def create_agreement(req: SlaContractCreate):
    return orch.create_agreement(req)


@app.get("/agreements/{agreement_id}")
def get_agreement(agreement_id: str):
    try:
        return orch.get(agreement_id)
    except UnknownAgreementError:
        raise HTTPException(404, "Unknown agreement")


@app.post("/agreements/{agreement_id}/lock")
async def lock(agreement_id: str, req: LockRequest = LockRequest()):
    return await _guard(orch.lock_escrow(agreement_id, req.fund_buyer_vault))


@app.post("/agreements/{agreement_id}/run")
async def run(agreement_id: str, req: RunJobRequest):
    return await _guard(orch.run_job(agreement_id, req.worker_code, req.buyer_test_code, req.semantic_score))


@app.post("/agreements/{agreement_id}/expire")
async def expire(agreement_id: str):
    return await _guard(orch.expire_authorization(agreement_id))


@app.get("/agreements/{agreement_id}/report")
def report(agreement_id: str):
    r = orch.report(agreement_id)
    if r is None:
        raise HTTPException(404, "No arbitration report yet")
    return r


@app.get("/ledger")
def ledger():
    return {
        "balances": orch.ledger.get_all_balances(),
        "entries": orch.ledger.to_records(),
        "conserved": orch.ledger.verify_balance_conservation(),
    }


@app.get("/scenarios")
def scenarios():
    return list(SCENARIOS.keys())


@app.post("/demo/{scenario}")
async def demo(scenario: str):
    """One call: create -> fund+lock -> run a preset scenario -> return contract, report, balances."""
    try:
        sc = get_scenario(scenario)
    except KeyError as e:
        raise HTTPException(404, str(e))
    c = orch.create_agreement(SlaContractCreate(
        buyer_agent_id="demo-buyer", worker_agent_id="demo-worker",
        worker_paypal_email=settings.__dict__.get("SANDBOX_WORKER_EMAIL") or "worker@example.com",
        budget_micros=sc.budget_micros, task_description=f"Demo scenario {sc.scenario_id}",
    ))
    await _guard(orch.lock_escrow(c.agreement_id, fund_buyer_vault=True))
    c = await _guard(orch.run_job(c.agreement_id, sc.worker_code, sc.buyer_test_code, sc.semantic_score))
    return {"contract": c, "report": orch.report(c.agreement_id), "balances": orch.ledger.get_all_balances()}
