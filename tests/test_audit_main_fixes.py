"""Regression tests for the defects found in the audit of main at c1964a9."""

import subprocess
from types import SimpleNamespace
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.db import Repository
from backend.app.main import create_app
from backend.app.schemas import FsmState
from backend.app.services import arbitration_engine as ae
from backend.app.services import fsm
from backend.app.services.arbitration_engine import ArbitrationArbiter, CodeExecutionRunner
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.paypal_mock import MockPayPalEscrowEngine
from backend.app.services.vault_service import (
    register_vault,
    release_vault_funds,
    reserve_vault_funds,
)
from tests.test_settlement_recovery import FlakyEngine
from tests.test_trust_policy import P, SUITE, api_body, make_orch, payload, settings

KEY = "test-api-key"
H = {"X-API-Key": KEY}


def app_client(tmp_path, **over):
    over.setdefault("DATABASE_URL", f"sqlite:///{tmp_path}/audit.db")
    app = create_app(settings(**over), engine=MockPayPalEscrowEngine())
    return TestClient(app), app


# ----------------------------------------------------------------- MCP JSON-RPC requires the API key
def test_root_mcp_rpc_is_gone_and_api_copy_needs_the_key(tmp_path):
    client, _ = app_client(tmp_path, PAYPAL_MODE="sandbox", API_AUTH_KEY=KEY)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "propose_contract", "arguments": api_body()}}
    with client:
        assert client.post("/mcp/rpc", json=call).status_code in (404, 405)
        assert client.post(f"{P}/mcp/rpc", json=call).status_code == 401
        listed = client.post(f"{P}/mcp/rpc", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                             headers=H)
        assert listed.status_code == 200
        assert client.get(f"{P}/contracts", headers=H).json()["count"] == 0


# ---------------------------------------------------------------------- ledger balance is a real audit
def test_ledger_audit_detects_a_tampered_balance():
    ledger = DoubleEntryLedgerEngine()
    ledger.deposit("buyer_vault", 10_000_000, "a1")
    ledger.record_transfer("paypal_escrow_hold", "buyer_vault", 10_000_000, "a1")
    assert ledger.verify_balance_conservation()
    ledger._balances["worker_payable"] += 1  # money appears from nowhere
    audit = ledger.audit_balances()
    assert audit["balanced"] is False
    assert audit["net_imbalance_micros"] == 1
    assert audit["account_drift_micros"] == {"worker_payable": 1}


def test_ledger_audit_survives_a_reload(tmp_path):
    repo = Repository.from_url(f"sqlite:///{tmp_path}/l.db")
    ledger = DoubleEntryLedgerEngine.from_repository(repo)
    ledger.deposit("buyer_vault", 5_000_000, "a1")
    ledger.record_transfer("paypal_escrow_hold", "buyer_vault", 5_000_000, "a1")
    reloaded = DoubleEntryLedgerEngine.from_repository(repo)
    assert reloaded.audit_balances()["balanced"] is True
    assert reloaded.audit_balances()["total_deposited_micros"] == 5_000_000


def test_ledger_balance_endpoint_reports_drift(tmp_path):
    client, app = app_client(tmp_path)
    with client:
        client.post(f"{P}/demo/scenarios/clean_pass")
        ok = client.get(f"{P}/ledger/balance").json()
        assert ok["balanced"] is True and ok["net_imbalance"] == 0
        app.state.ledger._balances["platform_fee_revenue"] += 7
        bad = client.get(f"{P}/ledger/balance").json()
        assert bad["balanced"] is False and bad["net_imbalance"] == 7


# ------------------------------------------------------------------ a2a auto_sign creates a contract
def test_auto_sign_creates_a_locked_contract(tmp_path):
    client, app = app_client(tmp_path)
    body = {"buyer_pref": {"max_budget_micros": 15_000_000, "max_timeout_seconds": 8.0},
            "worker_pref": {"min_budget_micros": 5_000_000, "min_timeout_seconds": 2.0},
            "auto_sign": True}
    with client:
        res = client.post(f"{P}/a2a/negotiate", json=body)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["contract_created"] is True and data["auth_id"]
        stored = client.get(f"{P}/contracts/{data['agreement_id']}").json()
        assert stored["fsm_state"] == "PAYPAL_AUTH_LOCKED"
        assert stored["buyer_signature"] and stored["worker_signature"]
        assert stored["buyer_agent_id"] == data["buyer_did"].strip()


def test_auto_sign_is_refused_outside_mock_mode(tmp_path):
    client, _ = app_client(tmp_path, PAYPAL_MODE="sandbox", API_AUTH_KEY=KEY)
    body = {"buyer_pref": {"max_budget_micros": 15_000_000}, "worker_pref": {}, "auto_sign": True}
    with client:
        assert client.post(f"{P}/a2a/negotiate", json=body, headers=H).status_code == 403


# ------------------------------------------------------------------------- PAYPAL_MODE validation
def test_misspelled_paypal_mode_is_rejected_instead_of_going_live():
    with pytest.raises(ValueError, match="PAYPAL_MODE"):
        settings(PAYPAL_MODE="sandbx")


# --------------------------------------------------------------------------- configurable CORS
def test_cors_origins_are_configurable(tmp_path):
    client, _ = app_client(tmp_path, CORS_ALLOWED_ORIGINS="https://dash.example.com, http://localhost:3000/")
    with client:
        r = client.get(f"{P}/health", headers={"Origin": "https://dash.example.com"})
        assert r.headers["access-control-allow-origin"] == "https://dash.example.com"
        r = client.get(f"{P}/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in r.headers


# ------------------------------------------------------------------------- health checks the DB
def test_health_reports_a_dead_database(tmp_path):
    client, app = app_client(tmp_path)
    with client:
        with mock.patch.object(app.state.repo.db, "query", side_effect=RuntimeError("gone")):
            resp = client.get(f"{P}/health")
            assert resp.status_code == 503
            body = resp.json()
    assert body["database"] == "disconnected" and body["status"] == "degraded"


# ----------------------------------------------------------------------------- vault route guards
def test_existing_vault_cannot_be_overwritten_and_setup_token_is_demo_only(tmp_path):
    client, app = app_client(tmp_path, PAYPAL_MODE="sandbox", API_AUTH_KEY=KEY)
    vault = {"vault_id": "v1", "buyer_did": "did:key:buyer", "current_daily_spent_micros": 9_000_000}
    with client:
        assert client.post(f"{P}/vaults", json=vault, headers=H).status_code == 201  # registering is fine
        zeroed = dict(vault, current_daily_spent_micros=0)
        assert client.post(f"{P}/vaults", json=zeroed, headers=H).status_code == 403
        assert app.state.repo.get_vault("v1").current_daily_spent_micros == 9_000_000
        setup = {"buyer_did": "did:key:x", "max_amount_per_task_micros": 1, "max_daily_budget_micros": 1}
        assert client.post(f"{P}/vault/setup-token", json=setup, headers=H).status_code == 403


def test_velocity_of_an_unknown_buyer_is_not_reported_as_allowed(tmp_path):
    client, _ = app_client(tmp_path)
    with client:
        assert client.get(f"{P}/vault/velocity/did:key:nobody").status_code == 404


# ------------------------------------------------------------- vault window rollover accounting
def test_releasing_a_hold_from_a_previous_window_does_not_eat_the_new_window(tmp_path):
    repo = Repository.from_url(f"sqlite:///{tmp_path}/v.db")
    t0 = 1_800_000_000
    register_vault("v", "b", max_daily_budget_micros=100_000_000, max_amount_per_task_micros=50_000_000,
                   repo=repo)
    reserve_vault_funds("v", 5_000_000, repo=repo, now_epoch=t0)
    t1 = t0 + 86_400 + 60  # the 24h window has rolled over
    reserve_vault_funds("v", 7_000_000, repo=repo, now_epoch=t1)
    release_vault_funds("v", 5_000_000, repo=repo, reserved_at_epoch=t0)
    assert repo.get_vault("v").current_daily_spent_micros == 7_000_000
    release_vault_funds("v", 7_000_000, repo=repo, reserved_at_epoch=t1)
    assert repo.get_vault("v").current_daily_spent_micros == 0


# ---------------------------------------------------- payout failure after capture is recoverable
async def test_payout_failure_after_capture_is_listed_and_retryable():
    engine = FlakyEngine(fail_payout=1)
    orch = make_orch(engine=engine)
    from backend.app.services.sweeper import ReauthorizationSweeper
    sweeper = ReauthorizationSweeper(orch.repo, orch.ledger, engine, clock=orch.clock, lock=orch.lock)

    c = await orch.create_contract(payload(test_suite=SUITE))
    done = await orch.submit_work(c.agreement_id, "def f(): ...")  # no 502: the capture stands
    assert done.fsm_state == FsmState.PAYPAL_CAPTURED
    assert sweeper.get_status()["unpaid_captures"] == [c.agreement_id]
    with pytest.raises(fsm.FsmError, match="payout/retry"):
        await orch.retry_settlement(c.agreement_id)

    paid = await orch.retry_payout(c.agreement_id)
    assert paid.fsm_state == FsmState.PAYOUT_DISPATCHED
    assert sweeper.get_status()["unpaid_captures"] == []
    assert orch.ledger.verify_balance_conservation()
    with pytest.raises(fsm.FsmError):
        await orch.retry_payout(c.agreement_id)  # PAYOUT_DISPATCHED is not retryable


# --------------------------------------------------------- sweeper sees every active hold
def test_sweeper_query_has_no_newest_first_cutoff(tmp_path):
    repo = Repository.from_url(f"sqlite:///{tmp_path}/s.db")
    base = datetime(2026, 11, 1, tzinfo=timezone.utc)
    for i in range(1_005):
        repo.save_contract(fsm.ContractRecord(
            agreement_id=f"a{i:04d}", buyer_agent_id="b", worker_agent_id="w",
            worker_paypal_email="w@example.com", budget_micros=1_000_000, task_description="task",
            timeout_seconds=60, fsm_state=FsmState.PAYPAL_AUTH_LOCKED,
            created_at=base + timedelta(seconds=i), updated_at=base + timedelta(seconds=i),
        ))
    found = repo.list_contracts_in_states([FsmState.PAYPAL_AUTH_LOCKED])
    assert len(found) == 1_005
    assert found[0].agreement_id == "a0000"  # oldest first, so the next to expire is never dropped


# ------------------------------------------------------------------ sandbox runner infrastructure
def _completed(rc, out=""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=out, stderr="")


def test_missing_pytest_is_an_infrastructure_error_not_a_zero_score():
    with mock.patch.object(ae.subprocess, "run",
                           return_value=_completed(1, "No module named pytest")):
        with pytest.raises(ae.SandboxInfrastructureError):
            CodeExecutionRunner().run_tests("x = 1\n", "def test_a(): pass\n")


async def test_runner_infrastructure_failure_voids_without_blaming_the_worker():
    arbiter = ArbitrationArbiter(runner=CodeExecutionRunner())
    arbiter.is_isolated = lambda *a, **k: True
    orch = make_orch(arbiter=arbiter)
    c = await orch.create_contract(payload(test_suite=SUITE))
    with mock.patch.object(ae.subprocess, "run", return_value=_completed(4, "usage error")):
        done = await orch.submit_work(c.agreement_id, "def f():\n    return 1\n")
    assert done.fsm_state == FsmState.ESCROW_VOIDED_REFUNDED
    assert "Arbiter failure (SandboxInfrastructureError)" in done.report["stdout"]
    assert "not a verdict on the worker" in done.report["stdout"]


def test_startup_time_is_not_charged_to_the_test_budget():
    seen = {}

    def fake_run(cmd, **kw):
        import os
        seen["timeout"] = kw["timeout"]
        with open(os.path.join(kw["cwd"], "conftest.py"), encoding="utf-8") as f:
            seen["conftest"] = f.read()
        return _completed(0, "1 passed in 0.01s")

    with mock.patch.object(ae.subprocess, "run", side_effect=fake_run):
        assert CodeExecutionRunner(timeout_seconds=2.0).run_tests("x = 1\n", "def test_a(): pass\n")[:2] == (1, 1)
    assert seen["timeout"] == 2.0 + ae.STARTUP_GRACE_SECONDS
    assert "Timer(2.0" in seen["conftest"] and str(ae.TEST_TIMEOUT_EXIT_CODE) in seen["conftest"]


def test_scrubbed_env_lets_the_child_import_pytest(tmp_path):
    env = CodeExecutionRunner()._build_scrubbed_env(str(tmp_path))
    out = subprocess.run(
        [ae.sys.executable, "-c", "import pytest; print('ok')"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert out.stdout.strip() == "ok", out.stderr


# -------------------------------------------------------------------------- WebSocket tickets
def test_websocket_accepts_a_fresh_ticket_and_rejects_forgeries(tmp_path):
    client, _ = app_client(tmp_path, PAYPAL_MODE="sandbox", API_AUTH_KEY=KEY)
    origin = {"origin": "http://localhost:3000"}
    with client:
        assert client.get(f"{P}/ws-ticket").status_code == 401
        ticket = client.get(f"{P}/ws-ticket", headers=H).json()["ticket"]
        with client.websocket_connect(f"{P}/ws/ledger?ticket={ticket}", headers=origin) as ws:
            assert ws.receive_json()["event_type"] == "INITIAL_SNAPSHOT"

        exp, _, sig = ticket.partition(".")
        forged = [f"{exp}.{'0' * len(sig)}", f"{int(time.time()) - 1}.{sig}", "garbage"]
        for bad in forged:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(f"{P}/ws/ledger?ticket={bad}", headers=origin) as ws:
                    ws.receive_json()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"{P}/ws/ledger?ticket={ticket}",
                                          headers={"origin": "https://evil.example"}) as ws:
                ws.receive_json()


def test_ws_ticket_is_null_without_a_key(tmp_path):
    client, _ = app_client(tmp_path)
    with client:
        assert client.get(f"{P}/ws-ticket").json()["ticket"] is None


# --------------------------------------------------- Multi-Model Telemetry Serialization
def test_dual_consensus_telemetry_extracted_in_arbitration_report():
    from backend.app.services.llm_judge import DualConsensusArbiter, JudgeResult

    class MockJudge:
        def __init__(self, score, rationale=""):
            self.score = score
            self.rationale = rationale

        def judge(self, _):
            return JudgeResult(self.score, self.rationale)

    consensus_arb = DualConsensusArbiter(
        gemini_judge=MockJudge(0.92, "Gemini OK"),
        claude_judge=MockJudge(0.88, "Claude OK"),
    )
    arbiter = ArbitrationArbiter(
        judge=consensus_arb,
        runner=SimpleNamespace(run_tests=lambda **k: (1, 1, "test_01 PASSED"), _parse_test_assertions=lambda stdout: []),
    )
    report = arbiter.evaluate("agr_telemetry_1", "def deliverable(): pass", "def test_deliverable(): pass")
    assert report.gemini_score == 0.92
    assert report.claude_score == 0.88
    assert report.divergence == pytest.approx(0.04)
    assert report.divergence_penalty_applied is False

    # Test divergence penalty
    divergent_consensus = DualConsensusArbiter(
        gemini_judge=MockJudge(0.95, "Gemini OK"),
        claude_judge=MockJudge(0.60, "Claude Low"),
    )
    arbiter_div = ArbitrationArbiter(
        judge=divergent_consensus,
        runner=SimpleNamespace(run_tests=lambda **k: (1, 1, "test_01 PASSED"), _parse_test_assertions=lambda stdout: []),
    )
    report_div = arbiter_div.evaluate("agr_telemetry_2", "def deliverable(): pass", "def test_deliverable(): pass")
    assert report_div.gemini_score == 0.95
    assert report_div.claude_score == 0.60
    assert report_div.divergence == pytest.approx(0.35)
    assert report_div.divergence_penalty_applied is True


def test_contracts_submit_and_detail_serialize_multi_model_telemetry(tmp_path):
    from backend.app.services.llm_judge import DualConsensusArbiter, JudgeResult

    class MockJudge:
        def __init__(self, score, rationale=""):
            self.score = score
            self.rationale = rationale

        def judge(self, _):
            return JudgeResult(self.score, self.rationale)

    consensus = DualConsensusArbiter(
        gemini_judge=MockJudge(0.96, "Gemini high"),
        claude_judge=MockJudge(0.91, "Claude high"),
    )
    client, app = app_client(tmp_path)
    app.state.orchestrator.arbiter.judge = consensus

    with client:
        # Create contract
        c_res = client.post(f"{P}/contracts", json=api_body())
        assert c_res.status_code == 201
        agr_id = c_res.json()["agreement_id"]

        # Submit deliverable
        submit_res = client.post(
            f"{P}/contracts/{agr_id}/submit",
            json={"source_code": "def deliverable(): pass", "test_suite": "def test_deliverable(): pass"},
        )
        assert submit_res.status_code == 200
        submit_data = submit_res.json()
        assert submit_data["gemini_score"] == 0.96
        assert submit_data["claude_score"] == 0.91
        assert submit_data["divergence"] == pytest.approx(0.05)
        assert submit_data["divergence_penalty_applied"] is False
        assert submit_data["arbitration_report"]["gemini_score"] == 0.96

        # Fetch contract detail
        detail_res = client.get(f"{P}/contracts/{agr_id}/detail")
        assert detail_res.status_code == 200
        detail_data = detail_res.json()
        assert detail_data["gemini_score"] == 0.96
        assert detail_data["claude_score"] == 0.91
        assert detail_data["divergence"] == pytest.approx(0.05)
        assert detail_data["divergence_penalty_applied"] is False
        assert detail_data["arbitration_report"]["gemini_score"] == 0.96


def test_auto_seed_on_empty_db_lifespan(tmp_path):
    client, app = app_client(tmp_path, AUTO_SEED_ON_STARTUP=True)
    with client:
        res = client.get(f"{P}/contracts").json()
        assert res["count"] == 3
        states = {c["fsm_state"] for c in res["contracts"]}
        # Every seeded capture is paid out, like a clicked scenario; none is left for the sweeper.
        assert sorted(c["fsm_state"] for c in res["contracts"]) == [
            "ESCROW_VOIDED_REFUNDED", "SETTLED_COMPLETE", "SETTLED_COMPLETE"]
        assert client.get(f"{P}/sweeper/status").json()["unpaid_captures"] == []
        # Seeded rows score exactly like the same scenario run from the dashboard.
        seeded = {c["agreement_id"]: c["verification_score"] for c in res["contracts"]}
        for name in ("clean_pass", "partial_defect", "malicious_injection"):
            clicked = client.post(f"{P}/demo/scenarios/{name}").json()["verification_score"]
            assert seeded[f"agr_canonical_{name}"] == clicked
        led = client.get(f"{P}/ledger").json()
        assert led["balanced"] is True


def test_backend_annotations_resolve_without_name_error():
    import importlib
    import inspect
    import pkgutil

    for mod_info in pkgutil.walk_packages(["backend"], prefix="backend."):
        mod = importlib.import_module(mod_info.name)
        inspect.get_annotations(mod)
        for val in list(vars(mod).values()):
            if getattr(val, "__module__", None) != mod_info.name:
                continue
            if inspect.isfunction(val) or inspect.isclass(val):
                inspect.get_annotations(val)
            if inspect.isclass(val):
                for mval in list(vars(val).values()):
                    fn = mval.__func__ if isinstance(mval, (staticmethod, classmethod)) else mval
                    if inspect.isfunction(fn):
                        inspect.get_annotations(fn)



def test_startup_seed_setting_is_honored_outside_pytest(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    client, _ = app_client(tmp_path, AUTO_SEED_ON_STARTUP=False)
    with client:
        assert client.get(f"{P}/contracts").json()["count"] == 0


@pytest.mark.parametrize("mode", ["Mock", " mock "])
def test_startup_seeds_when_mock_mode_is_not_lowercase(tmp_path, mode):
    # The settings validator accepts any case and padding, so seeding must too.
    client, _ = app_client(tmp_path, PAYPAL_MODE=mode, AUTO_SEED_ON_STARTUP=True)
    with client:
        assert client.get(f"{P}/contracts").json()["count"] == 3


@pytest.mark.parametrize("mode", ["sandbox", "live"])
def test_startup_never_seeds_non_mock_payment_modes(tmp_path, mode, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    client, _ = app_client(tmp_path, PAYPAL_MODE=mode, API_AUTH_KEY=KEY, AUTO_SEED_ON_STARTUP=True)
    with client:
        assert client.get(f"{P}/contracts", headers=H).json()["count"] == 0
        assert client.post(f"{P}/demo/scenarios/all", headers=H).status_code == 400


def test_startup_never_seeds_a_real_engine_even_in_mock_config(tmp_path):
    client, app = app_client(tmp_path, AUTO_SEED_ON_STARTUP=True)
    app.state.orchestrator.engine.is_mock = False
    with client:
        assert client.get(f"{P}/contracts").json()["count"] == 0
        assert client.post(f"{P}/demo/scenarios/all").status_code == 400


def test_seed_all_requires_key_and_is_idempotent(tmp_path):
    client, _ = app_client(tmp_path, API_AUTH_KEY=KEY, AUTO_SEED_ON_STARTUP=False)
    with client:
        assert client.post(f"{P}/demo/scenarios/all").status_code == 401
        first = client.post(f"{P}/demo/scenarios/all", headers=H)
        assert first.status_code == 200, first.text
        assert first.json()["count"] == 3
        before = client.get(f"{P}/ledger", headers=H).json()
        again = client.post(f"{P}/demo/scenarios/all", headers=H)
        assert again.status_code == 200, again.text
        assert again.json() == first.json()
        assert client.get(f"{P}/contracts", headers=H).json()["count"] == 3
        assert client.get(f"{P}/ledger", headers=H).json() == before
        assert before["balanced"] is True


@pytest.mark.asyncio
async def test_concurrent_seeding_does_not_duplicate_ledger_entries(tmp_path):
    import asyncio
    from backend.app.services.orchestrator import seed_canonical_scenarios

    _, app = app_client(tmp_path, AUTO_SEED_ON_STARTUP=False)
    orch = app.state.orchestrator
    try:
        results = await asyncio.gather(*(seed_canonical_scenarios(orch) for _ in range(3)))
        assert all([c.agreement_id for c in result] == [c.agreement_id for c in results[0]] for result in results)
        assert len(app.state.repo.list_contracts()) == 3
        journal = app.state.ledger.to_records()
        await seed_canonical_scenarios(orch)
        assert app.state.ledger.to_records() == journal
        assert app.state.ledger.verify_balance_conservation()
    finally:
        await orch.engine.close()
        app.state.repo.db.close()


def test_vault_responses_keep_the_status_field(tmp_path):
    # The dashboard's vault modal reads `status`; the response model must not strip it.
    client, _ = app_client(tmp_path)
    with client:
        created = client.post(f"{P}/vaults", json={"vault_id": "v_status", "buyer_did": "did:key:zStatus"})
        assert created.status_code == 201 and created.json()["status"] == "ACTIVE"
        client.post(f"{P}/vaults/v_status/freeze")
        assert client.get(f"{P}/vaults/v_status").json()["status"] == "VAULT_LOCKED_CIRCUIT_BREAKER"
