"""
tests/test_ast_and_sandbox.py
Comprehensive Test Suite for Phase 2 Verification Arbiter, AST Security Scanner,
Sandboxed Code Runner, and 3-Scenario Evaluation Engine.

Tests:
1. Clean code passes AST security scan with 0 violations.
2. Malicious imports (socket, subprocess, os, sys, etc.) are caught with specific violation messages.
3. Dangerous calls (eval, exec, compile, __import__, open in write/append mode) are rejected.
4. Open in read mode ('r', 'rb', or default) is permitted.
5. CodeExecutionRunner hard timeout kills infinite loops (while True: pass).
6. Environment scrubbing prevents worker code from accessing sensitive env vars (PAYPAL_CLIENT_SECRET).
7. ArbitrationArbiter enforces the AST Hard Veto: S = 0.00 even if test pass ratio is 100%.
8. Semantic rubric calculation rewards docstrings and type annotations.
9. Verification of all 3 preset scenarios:
   - SCENARIO_CLEAN_PASS: 10/10 tests, S = 0.975 -> FULL_CAPTURE ($15.00 / $0.00).
   - SCENARIO_PARTIAL_DEFECT: 7/10 tests, S = 0.75 -> PARTIAL_CAPTURE ($10.50 / $4.50).
   - SCENARIO_MALICIOUS_INJECTION: S = 0.00 -> VOID_AUTHORIZATION ($0.00 / $15.00).
"""

import os
import pytest

from backend.app.services.arbitration_engine import (
    ArbitrationArbiter,
    AstSecurityScanner,
    CodeExecutionRunner,
)
from backend.app.services.scenario_presets import (
    SCENARIO_CLEAN_PASS,
    SCENARIO_MALICIOUS_INJECTION,
    SCENARIO_PARTIAL_DEFECT,
    run_preset_scenario,
)


# ==============================================================================
# 1. AST SECURITY SCANNER TESTS
# ==============================================================================

def test_ast_scanner_clean_code():
    """Verifies that legitimate, compliant Python code passes AST inspection cleanly."""
    clean_code = """
import math
import time
from typing import List, Dict

def calculate_stats(numbers: List[float]) -> Dict[str, float]:
    '''Calculates basic summary statistics.'''
    if not numbers:
        return {"mean": 0.0, "total": 0.0}
    total = sum(numbers)
    return {"mean": total / len(numbers), "total": total}
"""
    scanner = AstSecurityScanner()
    is_safe, violations = scanner.scan(clean_code)

    assert is_safe is True
    assert len(violations) == 0


@pytest.mark.parametrize(
    "forbidden_code,expected_keyword",
    [
        ("import socket", "socket"),
        ("import subprocess", "subprocess"),
        ("import os", "os"),
        ("import sys", "sys"),
        ("import shutil", "shutil"),
        ("import requests", "requests"),
        ("import httpx", "httpx"),
        ("import urllib.request", "urllib"),
        ("import ctypes", "ctypes"),
        ("import pty", "pty"),
        ("from subprocess import Popen", "subprocess"),
        ("from os import environ", "os"),
        ("from socket import AF_INET", "socket"),
    ],
)
def test_ast_scanner_disallowed_imports(forbidden_code: str, expected_keyword: str):
    """Verifies that all disallowed import modules are flagged with specific logs."""
    scanner = AstSecurityScanner()
    is_safe, violations = scanner.scan(forbidden_code)

    assert is_safe is False
    assert len(violations) >= 1
    assert any(expected_keyword in v for v in violations)


@pytest.mark.parametrize(
    "dangerous_call_code,expected_call",
    [
        ("res = eval('2 + 2')", "eval"),
        ("exec('import math')", "exec"),
        ("m = __import__('math')", "__import__"),
        ("code = compile('1+1', '<string>', 'eval')", "compile"),
    ],
)
def test_ast_scanner_disallowed_calls(dangerous_call_code: str, expected_call: str):
    """Verifies that dangerous dynamic execution functions are strictly rejected."""
    scanner = AstSecurityScanner()
    is_safe, violations = scanner.scan(dangerous_call_code)

    assert is_safe is False
    assert len(violations) >= 1
    assert any(expected_call in v for v in violations)


def test_ast_scanner_disallowed_open_write_modes():
    """Verifies that open() with write or append modes is caught and rejected."""
    scanner = AstSecurityScanner()

    # Write mode positional
    is_safe_1, v_1 = scanner.scan("f = open('output.txt', 'w')")
    assert is_safe_1 is False
    assert any("open() with write/modify mode" in v for v in v_1)

    # Append mode keyword
    is_safe_2, v_2 = scanner.scan("f = open('output.txt', mode='a')")
    assert is_safe_2 is False
    assert any("open() with write/modify mode" in v for v in v_2)

    # Binary write mode
    is_safe_3, v_3 = scanner.scan("f = open('payload.bin', 'wb')")
    assert is_safe_3 is False
    assert any("open() with write/modify mode" in v for v in v_3)


def test_ast_scanner_allows_open_read_modes():
    """Verifies that open() in standard read mode is not flagged as a write violation."""
    scanner = AstSecurityScanner()

    # Read mode positional
    is_safe_1, v_1 = scanner.scan("f = open('data.json', 'r')")
    assert is_safe_1 is True
    assert len(v_1) == 0

    # Default read mode (no mode argument)
    is_safe_2, v_2 = scanner.scan("f = open('data.json')")
    assert is_safe_2 is True
    assert len(v_2) == 0


def test_ast_scanner_syntax_error():
    """Verifies that invalid Python syntax is handled gracefully without crashing."""
    scanner = AstSecurityScanner()
    is_safe, violations = scanner.scan("def broken_syntax( { return None")

    assert is_safe is False
    assert len(violations) == 1
    assert "SyntaxError" in violations[0]


# ==============================================================================
# 2. SANDBOXED CODE RUNNER & ENVIRONMENT SCRUBBING
# ==============================================================================

def test_code_runner_timeout_infinite_loop():
    """
    Verifies that worker code containing an infinite loop is killed cleanly
    within the configured timeout threshold (1.5 seconds) without hanging pytest.
    """
    infinite_loop_code = """
import time

class InfiniteService:
    def execute(self):
        while True:
            time.sleep(0.01)
"""
    test_code = """
from deliverable import InfiniteService

def test_service():
    svc = InfiniteService()
    svc.execute()
"""
    runner = CodeExecutionRunner(timeout_seconds=1.5)
    passed, total, output = runner.run_tests(infinite_loop_code, test_code)

    assert passed == 0
    assert total >= 1
    assert "Execution timed out" in output


def test_code_runner_environment_scrubbing():
    """
    Verifies that sensitive environment variables in the parent process
    (like PAYPAL_CLIENT_SECRET) are completely stripped and inaccessible in the sandbox.
    """
    # Inject a simulated secret into the current process environment
    secret_key = "PAYPAL_CLIENT_SECRET"
    secret_val = "SANDBOX_SUPER_SECRET_PAYPAL_API_KEY_12345"
    os.environ[secret_key] = secret_val

    try:
        worker_code = """
# Deliverable trying to read parent secrets
def get_secret(var_name: str) -> str:
    # Cannot use os.environ directly because os is disallowed,
    # but tests running deliverable verify the environment
    return ""
"""
        test_code = f"""
import os

def test_secret_is_empty():
    val = os.environ.get('{secret_key}')
    assert val is None, f"Leaked secret into sandbox: {{val}}"
"""
        runner = CodeExecutionRunner(timeout_seconds=5.0)
        passed, total, output = runner.run_tests(worker_code, test_code)

        assert passed == 1
        assert total == 1
    finally:
        os.environ.pop(secret_key, None)


# ==============================================================================
# 3. ARBITRATION ARBITER & AST HARD VETO
# ==============================================================================

def test_arbiter_ast_hard_veto_enforcement():
    """
    Verifies the AST Hard Veto rule:
    Even if unit tests pass 100% (e.g. 5/5), if the code contains an AST security violation,
    the composite score MUST be strictly locked to 0.00 and authorization voided.
    """
    malicious_code = """
import subprocess

def add(a: int, b: int) -> int:
    return a + b
"""
    test_code = """
from deliverable import add

def test_add():
    assert add(2, 3) == 5
"""
    arbiter = ArbitrationArbiter()
    report = arbiter.evaluate(
        agreement_id="veto_test_1",
        worker_code=malicious_code,
        buyer_test_code=test_code,
        budget_micros=15_000_000,
    )

    assert report.ast_safe is False
    assert len(report.ast_violations) >= 1
    assert any("subprocess" in v for v in report.ast_violations)

    # Score must be 0.00 despite functional correctness
    assert report.s_ast == 0.0
    assert report.composite_score == 0.0

    # Settlement must be VOID_AUTHORIZATION with 100% released back to buyer
    assert report.settlement.decision == "VOID_AUTHORIZATION"
    assert report.settlement.capture_micros == 0
    assert report.settlement.buyer_released_micros == 15_000_000


def test_arbiter_semantic_rubric():
    """Verifies that the semantic rubric properly scores docstrings and type annotations."""
    annotated_code = """
'''Module docstring.'''

def process_data(items: list[int]) -> int:
    '''Calculates sum of items.'''
    return sum(items)
"""
    bare_code = "def process_data(items):\n    return sum(items)\n"

    score_annotated = ArbitrationArbiter.compute_semantic_rubric(annotated_code)
    score_bare = ArbitrationArbiter.compute_semantic_rubric(bare_code)

    assert score_annotated > score_bare
    assert score_annotated >= 0.90
    assert score_bare <= 0.30


# ==============================================================================
# 4. PRESET EVALUATION SCENARIOS VERIFICATION
# ==============================================================================

def test_preset_scenario_clean_pass():
    """
    Verifies SCENARIO_CLEAN_PASS:
    - 10/10 tests pass
    - S_pytest = 1.0, S_ast = 1.0, S_semantic = 0.90
    - Composite score = 0.5*1.0 + 0.25*1.0 + 0.25*0.90 = 0.975 (>= 0.90)
    - Triggers FULL_CAPTURE of $15.00 ($15,000,000 micros)
    """
    report = run_preset_scenario("CLEAN_PASS")

    assert report.ast_safe is True
    assert report.passed_tests == 10
    assert report.total_tests == 10
    assert report.s_pytest == 1.0
    assert report.composite_score == SCENARIO_CLEAN_PASS.expected_composite_score
    assert report.composite_score >= 0.90

    # Settlement asserts
    assert report.settlement.decision == "FULL_CAPTURE"
    assert report.settlement.capture_micros == 15_000_000
    assert report.settlement.buyer_released_micros == 0
    assert report.settlement.capture_usd == "15.00"
    assert report.settlement.buyer_released_usd == "0.00"
    assert report.settlement.worker_net_payout_usd == "14.00"


def test_preset_scenario_partial_defect():
    """
    Verifies SCENARIO_PARTIAL_DEFECT:
    - Exactly 7/10 tests pass (3 edge case failures)
    - S_pytest = 0.70, S_ast = 1.0, S_semantic = 0.60
    - Composite score = 0.5*0.70 + 0.25*1.0 + 0.25*0.60 = 0.75
    - Piecewise linear factor at 0.75 = 0.70 (exact 70% capture)
    - Triggers PARTIAL_CAPTURE: $10.50 captured, $4.50 released to buyer
    """
    report = run_preset_scenario("PARTIAL_DEFECT")

    assert report.ast_safe is True
    assert report.passed_tests == 7
    assert report.total_tests == 10
    assert report.s_pytest == 0.70
    assert report.composite_score == SCENARIO_PARTIAL_DEFECT.expected_composite_score
    assert 0.60 <= report.composite_score < 0.90

    # Settlement asserts
    assert report.settlement.decision == "PARTIAL_CAPTURE"
    assert report.settlement.capture_micros == 10_500_000
    assert report.settlement.buyer_released_micros == 4_500_000
    assert report.settlement.capture_usd == "10.50"
    assert report.settlement.buyer_released_usd == "4.50"
    assert report.settlement.worker_net_payout_usd == "9.73"


def test_preset_scenario_malicious_injection():
    """
    Verifies SCENARIO_MALICIOUS_INJECTION:
    - AST scanner catches socket and subprocess imports
    - Hard veto forces S = 0.00
    - Triggers VOID_AUTHORIZATION: $0.00 captured, $15.00 refunded to buyer
    """
    report = run_preset_scenario("MALICIOUS_INJECTION")

    assert report.ast_safe is False
    assert len(report.ast_violations) >= 2
    assert any("socket" in v for v in report.ast_violations)
    assert any("subprocess" in v for v in report.ast_violations)

    assert report.s_ast == 0.0
    assert report.composite_score == 0.0

    # Settlement asserts
    assert report.settlement.decision == "VOID_AUTHORIZATION"
    assert report.settlement.capture_micros == 0
    assert report.settlement.buyer_released_micros == 15_000_000
    assert report.settlement.capture_usd == "0.00"
    assert report.settlement.buyer_released_usd == "15.00"
    assert report.settlement.worker_net_payout_micros == 0
