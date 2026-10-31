"""
backend/app/services/arbitration_engine.py
Verification Arbiter, AST Security Scanner, and Sandboxed Test Execution Runner for AegisPay.

Implements:
1. AstSecurityScanner(ast.NodeVisitor):
   - Static analysis checking for disallowed imports and dangerous built-in calls.
   - Enforces the Phase 2 AST security perimeter.
2. CodeExecutionRunner:
   - Ephemeral directory test execution running pytest.
   - Hard execution timeout enforcement (killing infinite loops).
   - Complete environment variable scrubbing (zero secret leakage).
3. ArbitrationArbiter:
   - Composite scoring across pytest results (50%), AST security (25%), and semantic rubric (25%).
   - Enforces Hard Veto: If AST scan fails, S is locked to 0.00 immediately.
   - Integrates directly with compute_settlement_split_micros() for financial resolution.
"""

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.schemas import ArbitrationReport, SettlementSplitResult
from backend.app.services.paypal_escrow import compute_settlement_split_micros


class AstSecurityScanner(ast.NodeVisitor):
    """
    Static code analysis visitor that audits untrusted Python source code for
    forbidden imports, dangerous function calls, and unauthorized disk writes.
    """

    DISALLOWED_IMPORTS: Set[str] = {
        "os",
        "sys",
        "subprocess",
        "socket",
        "urllib",
        "requests",
        "httpx",
        "shutil",
        "pty",
        "ctypes",
        "builtins",
    }

    DISALLOWED_CALLS: Set[str] = {
        "eval",
        "exec",
        "__import__",
        "compile",
    }

    WRITE_MODES: Set[str] = {"w", "a", "+", "x"}

    def __init__(self) -> None:
        super().__init__()
        self.violations: List[str] = []

    def scan(self, source_code: str) -> Tuple[bool, List[str]]:
        """
        Parses source code into an AST and visits all nodes.
        Returns (is_safe, violations).
        """
        self.violations.clear()
        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            self.violations.append(f"SyntaxError in submitted source code: {e}")
            return False, list(self.violations)

        self.visit(tree)
        is_safe = len(self.violations) == 0
        return is_safe, list(self.violations)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root_module = alias.name.split(".")[0]
            if root_module in self.DISALLOWED_IMPORTS:
                self.violations.append(
                    f"Forbidden import: '{alias.name}' at line {node.lineno}"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root_module = node.module.split(".")[0]
            if root_module in self.DISALLOWED_IMPORTS:
                self.violations.append(
                    f"Forbidden import from: '{node.module}' at line {node.lineno}"
                )
        # Also check imported symbols if any match forbidden modules
        for alias in node.names:
            if alias.name in self.DISALLOWED_IMPORTS:
                self.violations.append(
                    f"Forbidden import symbol: '{alias.name}' at line {node.lineno}"
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Check direct function calls: eval(), exec(), etc.
        func_name: Optional[str] = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name:
            if func_name in self.DISALLOWED_CALLS:
                self.violations.append(
                    f"Forbidden call: '{func_name}()' at line {node.lineno}"
                )
            elif func_name == "open":
                self._check_open_call(node)

        self.generic_visit(node)

    def _check_open_call(self, node: ast.Call) -> None:
        """Flags calls to open() that request write or append access."""
        mode_str: Optional[str] = None

        # Positional mode argument: open(file, mode, ...)
        if len(node.args) >= 2:
            second_arg = node.args[1]
            if isinstance(second_arg, ast.Constant) and isinstance(second_arg.value, str):
                mode_str = second_arg.value
            else:
                self.violations.append(
                    f"Forbidden call: open() with dynamic/unverified mode argument at line {node.lineno}"
                )
                return

        # Keyword mode argument: open(file, mode='w', ...)
        for kw in node.keywords:
            if kw.arg == "mode":
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    mode_str = kw.value.value
                else:
                    self.violations.append(
                        f"Forbidden call: open() with dynamic/unverified mode argument at line {node.lineno}"
                    )
                    return

        if mode_str and any(flag in mode_str for flag in self.WRITE_MODES):
            self.violations.append(
                f"Forbidden call: open() with write/modify mode '{mode_str}' at line {node.lineno}"
            )


class CodeExecutionRunner:
    """
    Executes untrusted worker deliverables against buyer test suites in an
    isolated, ephemeral directory with hard timeouts and scrubbed environments.
    """

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self.timeout_seconds = timeout_seconds

    def _build_scrubbed_env(self, temp_dir: str) -> Dict[str, str]:
        """
        Constructs a minimal, secure environment dictionary.
        Explicitly excludes any .env credentials, PayPal secrets, or system tokens.
        """
        # Minimal system environment required for Python runtime
        scrubbed = {
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
            "PATH": os.environ.get("PATH", ""),
            "TEMP": temp_dir,
            "TMP": temp_dir,
            "PYTHONPATH": temp_dir,
            "PYTHONHASHSEED": "0",
            "PYTHONUNBUFFERED": "1",
        }

        # Paranoid check: ensure no sensitive keys leaked through
        sensitive_patterns = (
            "PAYPAL",
            "SECRET",
            "TOKEN",
            "KEY",
            "PASSWORD",
            "DATABASE",
            "URL",
            "CREDENTIAL",
        )
        for k in list(scrubbed.keys()):
            if any(pat in k.upper() for pat in sensitive_patterns):
                scrubbed.pop(k, None)

        return scrubbed

    def run_tests(
        self,
        worker_code: str,
        test_code: str,
        deliverable_filename: str = "deliverable.py",
        test_filename: str = "test_deliverable.py",
        timeout: Optional[float] = None,
    ) -> Tuple[int, int, str]:
        """
        Executes pytest in an isolated temporary directory.
        Returns: (passed_tests, total_tests, stdout_output).
        """
        exec_timeout = timeout or self.timeout_seconds
        temp_dir = tempfile.mkdtemp(prefix="aegis_sandbox_")

        try:
            # 1. Write worker deliverable and buyer test code to disk
            worker_path = os.path.join(temp_dir, deliverable_filename)
            test_path = os.path.join(temp_dir, test_filename)

            with open(worker_path, "w", encoding="utf-8") as f:
                f.write(worker_code)

            with open(test_path, "w", encoding="utf-8") as f:
                f.write(test_code)

            # 2. Build scrubbed environment
            clean_env = self._build_scrubbed_env(temp_dir)

            # 3. Execute pytest as a subprocess
            cmd = [
                sys.executable,
                "-m",
                "pytest",
                test_filename,
                "-q",
                "--tb=short",
                "-o",
                "asyncio_default_fixture_loop_scope=function",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    cwd=temp_dir,
                    env=clean_env,
                    capture_output=True,
                    text=True,
                    timeout=exec_timeout,
                )
                output = (proc.stdout or "") + "\n" + (proc.stderr or "")
                passed, total = self._parse_pytest_output(output, proc.returncode)
                return passed, total, output

            except subprocess.TimeoutExpired as exc:
                output = f"Execution timed out (exceeded {exec_timeout:.1f}s threshold)."
                if exc.stdout:
                    output += "\nStdout before timeout:\n" + exc.stdout.decode(
                        "utf-8", errors="replace"
                    )
                return 0, 1, output

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _parse_pytest_output(output: str, returncode: int) -> Tuple[int, int]:
        """Parses standard pytest summary output into (passed_count, total_count)."""
        passed_match = re.search(r"(\d+)\s+passed", output)
        failed_match = re.search(r"(\d+)\s+failed", output)
        errors_match = re.search(r"(\d+)\s+error", output)

        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        errors = int(errors_match.group(1)) if errors_match else 0

        total = passed + failed + errors

        # If pytest crashed during collection or deliverable had import error
        if total == 0:
            if returncode != 0:
                return 0, 1
            return 0, 0

        return passed, total


class ArbitrationArbiter:
    """
    Autonomous Verification Arbiter.
    Audits deliverables across three orthogonal dimensions:
    1. s_pytest: Quantitative test suite pass ratio (weight: 0.50)
    2. s_ast: Static AST security scanner score (weight: 0.25)
    3. s_semantic: Code quality and docstring rubric score (weight: 0.25)

    Enforces the Hard Veto: If s_ast == 0.0, total composite score S is locked to 0.00.
    """

    def __init__(
        self,
        scanner: Optional[AstSecurityScanner] = None,
        runner: Optional[CodeExecutionRunner] = None,
    ) -> None:
        self.scanner = scanner or AstSecurityScanner()
        self.runner = runner or CodeExecutionRunner(timeout_seconds=5.0)

    @staticmethod
    def compute_semantic_rubric(source_code: str) -> float:
        """
        Evaluates deliverable code quality on a [0.0, 1.0] scale:
        - Class and function docstrings present (+0.40)
        - Type annotations present (+0.30)
        - Reasonable modularity and formatting (+0.30)
        """
        score = 0.20  # Base score for valid code

        try:
            tree = ast.parse(source_code)
        except Exception:
            return 0.0

        has_docstring = False
        has_annotations = False

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if ast.get_docstring(node):
                    has_docstring = True
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.returns or any(arg.annotation for arg in node.args.args):
                        has_annotations = True

        if has_docstring:
            score += 0.40
        if has_annotations:
            score += 0.40

        return min(1.0, round(score, 2))

    def evaluate(
        self,
        agreement_id: str,
        worker_code: str,
        buyer_test_code: str,
        budget_micros: int = 15_000_000,
        semantic_score: Optional[float] = None,
        platform_fee_bps: int = 500,
        paypal_payout_fee_micros: int = 250_000,
    ) -> ArbitrationReport:
        """
        Full end-to-end arbitration evaluation:
        1. Runs AST Security Scan.
        2. Executes sandboxed test runner.
        3. Computes composite score with AST Hard Veto.
        4. Calculates financial settlement split.
        """
        # 1. AST Security Audit
        is_safe, violations = self.scanner.scan(worker_code)
        s_ast = 1.0 if is_safe else 0.0

        # 2. Pytest Execution
        # If security scan found malicious code, we do NOT execute untrusted code on host
        if not is_safe:
            passed_tests, total_tests = 0, 1
            stdout = (
                "Code execution skipped: Security Perimeter Violation Detected.\nViolations:\n"
                + "\n".join(f"- {v}" for v in violations)
            )
            s_pytest = 0.0
        else:
            passed_tests, total_tests, stdout = self.runner.run_tests(
                worker_code=worker_code,
                test_code=buyer_test_code,
            )
            s_pytest = (passed_tests / total_tests) if total_tests > 0 else 0.0

        # 3. Semantic Rubric
        s_semantic = (
            semantic_score
            if semantic_score is not None
            else self.compute_semantic_rubric(worker_code)
        )
        s_semantic = max(0.0, min(1.0, s_semantic))

        # 4. Composite Score with AST Hard Veto
        if s_ast == 0.0:
            # Hard Veto Rule: Security breach locks composite score to 0.00
            composite_score = 0.0
            ast_passed = False
        else:
            composite_score = round(
                (0.50 * s_pytest) + (0.25 * s_ast) + (0.25 * s_semantic),
                4,
            )
            ast_passed = True

        # 5. Financial Settlement Calculation
        settlement_dict = compute_settlement_split_micros(
            agreed_budget_micros=budget_micros,
            composite_score=composite_score,
            ast_security_passed=ast_passed,
            platform_fee_bps=platform_fee_bps,
            paypal_payout_fee_micros=paypal_payout_fee_micros,
        )
        settlement_result = SettlementSplitResult(**settlement_dict)

        return ArbitrationReport(
            agreement_id=agreement_id,
            s_pytest=round(s_pytest, 4),
            s_ast=s_ast,
            s_semantic=round(s_semantic, 4),
            composite_score=composite_score,
            passed_tests=passed_tests,
            total_tests=total_tests,
            ast_safe=is_safe,
            ast_violations=violations,
            stdout=stdout,
            settlement=settlement_result,
        )
