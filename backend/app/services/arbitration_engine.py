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
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.schemas import (
    ArbitrationReport,
    SettlementSplitResult,
    TestAssertionItem,
)
from backend.app.services.llm_judge import HeuristicJudge, SemanticJudge
from backend.app.services.node_runner import (
    JsSecurityScanner,
    NodeCodeRunner,
    NodeExecutionRunner,
    NodeExecutionTimeoutError,
    NodeRunner,
)
from backend.app.services.paypal_escrow import compute_settlement_split_micros

logger = logging.getLogger(__name__)


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
        "io",
        "_thread",
        "threading",
        "multiprocessing",
        "shlex",
        "resource",
        "signal",
        "posix",
        "aiohttp",
        "urllib3",
    }

    DISALLOWED_CALLS: Set[str] = {
        "eval",
        "exec",
        "__import__",
        "compile",
        "globals",
        "locals",
    }

    WRITE_MODES: Set[str] = {"w", "a", "+", "x"}

    def __init__(self) -> None:
        super().__init__()
        self.violations: List[str] = []
        self.scanned_imports: List[str] = []
        self.blocked_calls: List[str] = []

    def scan(self, source_code: str) -> Tuple[bool, List[str]]:
        """
        Parses source code into an AST and visits all nodes.
        Returns (is_safe, violations).
        """
        self.violations.clear()
        self.scanned_imports.clear()
        self.blocked_calls.clear()
        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            self.violations.append(f"SyntaxError in submitted source code: {e}")
            self.blocked_calls.append(f"SyntaxError: {e}")
            return False, list(self.violations)

        self.visit(tree)
        is_safe = len(self.violations) == 0
        return is_safe, list(self.violations)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root_module = alias.name.split(".")[0]
            if alias.name not in self.scanned_imports:
                self.scanned_imports.append(alias.name)
            if root_module in self.DISALLOWED_IMPORTS:
                self.violations.append(
                    f"Forbidden import: '{alias.name}' at line {node.lineno}"
                )
                self.blocked_calls.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root_module = node.module.split(".")[0]
            if node.module not in self.scanned_imports:
                self.scanned_imports.append(node.module)
            if root_module in self.DISALLOWED_IMPORTS:
                self.violations.append(
                    f"Forbidden import from: '{node.module}' at line {node.lineno}"
                )
                self.blocked_calls.append(node.module)
        # Also check imported symbols if any match forbidden modules
        for alias in node.names:
            if alias.name in self.DISALLOWED_IMPORTS:
                self.violations.append(
                    f"Forbidden import symbol: '{alias.name}' at line {node.lineno}"
                )
                self.blocked_calls.append(alias.name)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in ("__builtins__", "globals", "locals"):
            self.violations.append(
                f"Forbidden identifier: '{node.id}' at line {node.lineno}"
            )
            self.blocked_calls.append(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in ("__builtins__", "__subclasses__", "__bases__", "__globals__", "__code__"):
            self.violations.append(
                f"Forbidden attribute access: '{node.attr}' at line {node.lineno}"
            )
            self.blocked_calls.append(node.attr)
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
                self.blocked_calls.append(f"{func_name}()")
            elif func_name == "open":
                self._check_open_call(node)

        self.generic_visit(node)

    def _check_open_call(self, node: ast.Call) -> None:
        """Flags calls to open() that target restricted system paths or request write/append access."""
        # Check target filepath
        if node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                filepath = first_arg.value.strip().lower()
                sensitive_prefixes = (
                    "/etc",
                    "/proc",
                    "/sys",
                    "/dev",
                    "/var",
                    "/root",
                    "/bin",
                    "/usr",
                    "/boot",
                    "c:\\windows",
                    "c:/windows",
                    "c:\\winnt",
                    "c:/winnt",
                )
                if (
                    any(filepath.startswith(p) for p in sensitive_prefixes)
                    or ".." in filepath
                    or filepath.startswith(("/", "\\"))
                ):
                    self.violations.append(
                        f"Forbidden call: open() with restricted/sensitive path '{first_arg.value}' at line {node.lineno}"
                    )
                    self.blocked_calls.append(f"open('{first_arg.value}')")
                    return

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
                "-v",
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
                    raw = exc.stdout
                    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                    output += "\nStdout before timeout:\n" + text
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

    @staticmethod
    def _parse_test_assertions(output: str) -> List[TestAssertionItem]:
        """Parses individual test items, status, and failure messages from pytest output."""
        assertions: List[TestAssertionItem] = []
        matches = list(re.finditer(r"(?:[^\s:]+::)?(test_\w+)\s+(PASSED|FAILED|ERROR)", output))
        for m in matches:
            test_name = m.group(1)
            status_str = m.group(2)
            error_msg: Optional[str] = None
            if status_str in ("FAILED", "ERROR"):
                fail_pat = rf"_{{{3,}}}\s+{re.escape(test_name)}\s+_{{{3,}}}(.*?)(?:_{{{3,}}}|\Z)"
                fail_match = re.search(fail_pat, output, re.DOTALL)
                if fail_match:
                    trace_text = fail_match.group(1).strip()
                    lines = [line.strip() for line in trace_text.splitlines() if line.strip()]
                    error_msg = lines[-1] if lines else trace_text[:200]
                else:
                    error_msg = f"Test {test_name} failed assertion."
            assertions.append(
                TestAssertionItem(
                    name=test_name,
                    status="PASSED" if status_str == "PASSED" else "FAILED",
                    duration_ms=10.0,
                    error_message=error_msg,
                )
            )
        if not assertions:
            passed_match = re.search(r"(\d+)\s+passed", output)
            failed_match = re.search(r"(\d+)\s+failed", output)
            p_cnt = int(passed_match.group(1)) if passed_match else 0
            f_cnt = int(failed_match.group(1)) if failed_match else 0
            for i in range(p_cnt):
                assertions.append(TestAssertionItem(name=f"test_assertion_{i+1:02d}", status="PASSED", duration_ms=10.0))
            for i in range(f_cnt):
                assertions.append(TestAssertionItem(name=f"test_assertion_{p_cnt+i+1:02d}", status="FAILED", duration_ms=10.0, error_message="Assertion failed"))
        return assertions


class DockerExecutionRunner:
    """
    Runs pytest inside an ephemeral hardened Docker container (dossier Fix #6).
    Same run_tests() interface and return type as CodeExecutionRunner.
    """

    HARDENING_FLAGS: List[str] = [
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--memory", "256m",
        "--memory-swap", "256m",
        "--cpus", "0.5",
        "--pids-limit", "64",
        "--security-opt", "no-new-privileges:true",
        "--cap-drop", "ALL",
    ]

    def __init__(
        self,
        timeout_seconds: float = 5.0,
        image: str = "aegispay-sandbox-runner:latest",
        docker_bin: str = "docker",
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.image = image
        self.docker_bin = docker_bin

    def run_tests(
        self,
        worker_code: str,
        test_code: str,
        deliverable_filename: str = "deliverable.py",
        test_filename: str = "test_deliverable.py",
        timeout: Optional[float] = None,
    ) -> Tuple[int, int, str]:
        """Returns (passed_tests, total_tests, output), like CodeExecutionRunner."""
        exec_timeout = timeout or self.timeout_seconds
        temp_dir = tempfile.mkdtemp(prefix="aegis_job_")
        container_name = f"aegis-sbx-{os.path.basename(temp_dir)}"

        try:
            # mkdtemp is 0700; the non-root container user needs read access.
            os.chmod(temp_dir, 0o755)
            for name, content in (
                (deliverable_filename, worker_code),
                (test_filename, test_code),
            ):
                path = os.path.join(temp_dir, name)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                os.chmod(path, 0o644)

            cmd = [
                self.docker_bin, "run", "--rm",
                "--name", container_name,
                *self.HARDENING_FLAGS,
                "-v", f"{temp_dir}:/workspace:ro",
                self.image,
                # Image ENTRYPOINT is pytest.
                f"/workspace/{test_filename}",
                "--tb=short", "-q", "-p", "no:cacheprovider",
                "--rootdir", "/workspace",
            ]

            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=exec_timeout
                )
            except subprocess.TimeoutExpired as exc:
                self._kill_container(container_name)
                output = f"Execution timed out (exceeded {exec_timeout:.1f}s threshold)."
                if exc.stdout:
                    raw = exc.stdout
                    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                    output += "\nStdout before timeout:\n" + text
                return 0, 1, output
            except OSError as exc:
                return 0, 1, f"Docker execution failed to start: {exc}"

            output = (proc.stdout or "") + "\n" + (proc.stderr or "")
            passed, total = CodeExecutionRunner._parse_pytest_output(output, proc.returncode)
            return passed, total, output
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _kill_container(self, container_name: str) -> None:
        try:
            subprocess.run(
                [self.docker_bin, "kill", container_name],
                capture_output=True, text=True, timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("docker kill %s failed: %s", container_name, exc)


def _docker_available(image: str, docker_bin: str = "docker") -> bool:
    """True if the daemon answers `docker info` and the sandbox image exists."""
    try:
        info = subprocess.run([docker_bin, "info"], capture_output=True, text=True, timeout=10)
        if info.returncode != 0:
            return False
        img = subprocess.run(
            [docker_bin, "image", "inspect", image], capture_output=True, text=True, timeout=10
        )
        return img.returncode == 0
    except Exception:  # noqa: BLE001
        return False


SubprocessCodeRunner = CodeExecutionRunner
DockerCodeRunner = DockerExecutionRunner


def get_execution_runner(settings: Any, timeout_seconds: float = 5.0, runtime: str = "python") -> Any:
    """Selects a runner by runtime and settings.SANDBOX_MODE: docker | subprocess | auto (default)."""
    r = runtime.strip().lower() if runtime else "python"
    if r in ("javascript", "typescript", "js", "ts", "node"):
        return NodeCodeRunner(timeout_seconds=timeout_seconds)

    mode = str(getattr(settings, "SANDBOX_MODE", "auto")).strip().lower()
    image = getattr(settings, "SANDBOX_IMAGE", "aegispay-sandbox-runner:latest")
    if mode == "docker":
        return DockerExecutionRunner(timeout_seconds=timeout_seconds, image=image)
    if mode == "subprocess":
        return CodeExecutionRunner(timeout_seconds=timeout_seconds)
    if mode != "auto":
        logger.warning("Unknown SANDBOX_MODE %r; treating as 'auto'", mode)
    if _docker_available(image):
        return DockerExecutionRunner(timeout_seconds=timeout_seconds, image=image)
    logger.warning(
        "Docker sandbox unavailable (daemon or image '%s' missing); falling back to the "
        "NON-ISOLATED subprocess runner",
        image,
    )
    return CodeExecutionRunner(timeout_seconds=timeout_seconds)


class ArbitrationArbiter:
    """
    Autonomous Verification Arbiter.
    Audits deliverables across three orthogonal dimensions:
    1. s_pytest / s_test: Quantitative test suite pass ratio
    2. s_ast: Static AST security scanner score (weight: 0.25)
    3. s_semantic: Code quality and docstring rubric score (weight: 0.25)

    Enforces the Hard Veto: If s_ast == 0.0, total composite score S is locked to 0.00.
    Supports polyglot execution sandboxes across Python, TypeScript, and JavaScript.
    """

    def __init__(
        self,
        scanner: Optional[AstSecurityScanner] = None,
        runner: Optional[Any] = None,
        judge: Optional[SemanticJudge] = None,
        consensus_arbiter: Optional[Any] = None,
        node_runner: Optional[Any] = None,
        js_scanner: Optional[Any] = None,
    ) -> None:
        self.scanner = scanner or AstSecurityScanner()
        self.js_scanner = js_scanner or JsSecurityScanner()
        self.runner = runner or CodeExecutionRunner(timeout_seconds=5.0)
        self.node_runner = node_runner or NodeCodeRunner(timeout_seconds=5.0)
        self.judge: SemanticJudge = judge or HeuristicJudge()
        self.consensus_arbiter = consensus_arbiter

    @staticmethod
    def detect_runtime(worker_code: str, test_code: str = "", runtime: Optional[str] = None) -> str:
        """
        Normalizes or detects execution runtime: 'python' | 'typescript' | 'javascript'.
        """
        if runtime:
            r = runtime.strip().lower()
            if r in ("typescript", "ts"):
                return "typescript"
            if r in ("javascript", "js", "node"):
                return "javascript"
            if r in ("python", "py"):
                return "python"
            return r

        sample = f"{worker_code}\n{test_code}"
        # Check TypeScript indicators
        if re.search(r"\b(interface\s+\w+|type\s+\w+\s*=|:\s*(?:string|number|boolean|any|void)\b|<[A-Z]\w*>)", sample):
            return "typescript"
        # Check JavaScript indicators
        if re.search(r"\b(const\s+\w+|let\s+\w+|function\s+\w+|module\.exports|require\(|console\.log|export\s+(?:default|function|const))\b", sample):
            return "javascript"
        return "python"

    @staticmethod
    def compute_semantic_rubric(source_code: str, runtime: str = "python") -> float:
        """
        Evaluates deliverable code quality on a [0.0, 1.0] scale:
        - Python: docstrings (+0.40), type annotations (+0.40), base (+0.20)
        - JS/TS: JSDoc/block comments (+0.40), types/exports (+0.40), base (+0.20)
        """
        r = runtime.strip().lower() if runtime else "python"
        if r in ("javascript", "typescript", "js", "ts", "node"):
            return ArbitrationArbiter._compute_js_rubric(source_code)

        try:
            tree = ast.parse(source_code)
        except Exception:
            # If it fails to parse as Python, see if it has JS/TS syntax
            if re.search(r"\b(function|const|let|export|interface)\b", source_code):
                return ArbitrationArbiter._compute_js_rubric(source_code)
            return 0.0

        score = 0.20  # Base score for valid code
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

    @staticmethod
    def _compute_js_rubric(source_code: str) -> float:
        score = 0.20
        # JSDoc or block documentation comments
        if re.search(r"/\*\*[\s\S]*?\*/|//\s+@param|//\s+@returns", source_code):
            score += 0.40
        elif re.search(r"//\s{2,}\S+", source_code):
            score += 0.20

        # Type annotations, interfaces, or structured modular exports
        if re.search(r"\b(interface\s+\w+|type\s+\w+\s*=|:\s*(?:string|number|boolean|any|void)\b|<[A-Z]\w*>|module\.exports|export\s+)", source_code):
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
        runtime: Optional[str] = None,
    ) -> ArbitrationReport:
        """
        Full end-to-end arbitration evaluation:
        1. Runs AST Security Scan (Python or JS/TS).
        2. Executes sandboxed test runner (pytest or node).
        3. Computes composite score with AST Hard Veto.
        4. Calculates financial settlement split.
        """
        target_runtime = self.detect_runtime(worker_code, buyer_test_code, runtime)
        is_node = target_runtime in ("javascript", "typescript", "js", "ts", "node")

        # 1. AST Security Audit
        if is_node:
            is_safe, violations = self.js_scanner.scan(worker_code)
            scanned_imports = list(self.js_scanner.scanned_imports)
            runner_to_use = self.node_runner if not isinstance(self.runner, NodeCodeRunner) else self.runner
        else:
            is_safe, violations = self.scanner.scan(worker_code)
            scanned_imports = list(self.scanner.scanned_imports)
            runner_to_use = self.runner
        s_ast = 1.0 if is_safe else 0.0

        # 2. Test Execution
        # If security scan found malicious code, we do NOT execute untrusted code on host
        if not is_safe:
            passed_tests, total_tests = 0, 1
            stdout = (
                "Code execution skipped: Security Perimeter Violation Detected.\nViolations:\n"
                + "\n".join(f"- {v}" for v in violations)
            )
            s_pytest = 0.0
            test_assertions = [
                TestAssertionItem(
                    name="security_perimeter_ast_audit",
                    status="FAILED",
                    duration_ms=0.0,
                    error_message="; ".join(violations),
                )
            ]
        else:
            passed_tests, total_tests, stdout = runner_to_use.run_tests(
                worker_code=worker_code,
                test_code=buyer_test_code,
            )
            s_pytest = (passed_tests / total_tests) if total_tests > 0 else 0.0
            parser = getattr(runner_to_use, "_parse_test_assertions", None) or CodeExecutionRunner._parse_test_assertions
            test_assertions = parser(stdout)
            if not test_assertions and total_tests > 0:
                for i in range(passed_tests):
                    test_assertions.append(TestAssertionItem(name=f"test_{i+1:02d}", status="PASSED", duration_ms=12.0))
                for i in range(total_tests - passed_tests):
                    test_assertions.append(TestAssertionItem(name=f"test_{passed_tests+i+1:02d}", status="FAILED", duration_ms=12.0, error_message="Assertion failed"))

        # 3. Semantic Rubric
        rationale: Optional[str] = None
        if semantic_score is not None:
            s_semantic = semantic_score
        else:
            if is_node and isinstance(self.judge, HeuristicJudge):
                s_semantic = self.compute_semantic_rubric(worker_code, runtime=target_runtime)
            else:
                judged = self.judge.judge(worker_code)
                s_semantic = judged.score
                rationale = judged.rationale
        s_semantic = max(0.0, min(1.0, s_semantic))
        if rationale:
            stdout = f"{stdout}\n[semantic judge rationale] {rationale}"

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
            test_assertions=test_assertions,
            scanned_imports=scanned_imports,
        )

    def arbitrate(
        self,
        agreement_id: str,
        worker_code: str,
        buyer_test_code: str,
        budget_micros: int = 15_000_000,
        semantic_score: Optional[float] = None,
        platform_fee_bps: int = 500,
        paypal_payout_fee_micros: int = 250_000,
        consensus_arbiter: Optional[Any] = None,
        runtime: Optional[str] = None,
    ) -> ArbitrationReport:
        """
        Adversarial Dual-Model Consensus Arbitration Engine (Day 5 & Day 6):
        Computes composite score: S = S_AST * (0.60 * S_test + 0.40 * S_semantic)
        Strictly enforces Hard Veto Rule:
        If S_AST == 0.0, S is locked to 0.00 immediately, bypassing test execution and LLM costs.
        """
        target_runtime = self.detect_runtime(worker_code, buyer_test_code, runtime)
        is_node = target_runtime in ("javascript", "typescript", "js", "ts", "node")

        # 1. AST Security Audit
        if is_node:
            is_safe, violations = self.js_scanner.scan(worker_code)
            scanned_imports = list(self.js_scanner.scanned_imports)
            runner_to_use = self.node_runner if not isinstance(self.runner, NodeCodeRunner) else self.runner
        else:
            is_safe, violations = self.scanner.scan(worker_code)
            scanned_imports = list(self.scanner.scanned_imports)
            runner_to_use = self.runner
        s_ast = 1.0 if is_safe else 0.0

        if not is_safe:
            # Hard Veto: bypass test execution and LLM costs immediately
            passed_tests, total_tests = 0, 1
            stdout = (
                "Code execution skipped: Security Perimeter Violation Detected.\nViolations:\n"
                + "\n".join(f"- {v}" for v in violations)
            )
            s_pytest = 0.0
            s_semantic = 0.0
            composite_score = 0.0
            ast_passed = False
            test_assertions = [
                TestAssertionItem(
                    name="security_perimeter_ast_audit",
                    status="FAILED",
                    duration_ms=0.0,
                    error_message="; ".join(violations),
                )
            ]
        else:
            # 2. Test Execution
            passed_tests, total_tests, stdout = runner_to_use.run_tests(
                worker_code=worker_code,
                test_code=buyer_test_code,
            )
            s_pytest = (passed_tests / total_tests) if total_tests > 0 else 0.0
            parser = getattr(runner_to_use, "_parse_test_assertions", None) or CodeExecutionRunner._parse_test_assertions
            test_assertions = parser(stdout)
            if not test_assertions and total_tests > 0:
                for i in range(passed_tests):
                    test_assertions.append(TestAssertionItem(name=f"test_{i+1:02d}", status="PASSED", duration_ms=12.0))
                for i in range(total_tests - passed_tests):
                    test_assertions.append(TestAssertionItem(name=f"test_{passed_tests+i+1:02d}", status="FAILED", duration_ms=12.0, error_message="Assertion failed"))

            # 3. Dual-Model Consensus Semantic Grader
            arb = consensus_arbiter or getattr(self, "consensus_arbiter", None) or self.judge
            if semantic_score is not None:
                s_semantic = max(0.0, min(1.0, float(semantic_score)))
            else:
                if is_node and isinstance(arb, HeuristicJudge):
                    s_semantic = self.compute_semantic_rubric(worker_code, runtime=target_runtime)
                else:
                    judged = arb.judge(worker_code)
                    s_semantic = max(0.0, min(1.0, float(judged.score)))
                    if judged.rationale:
                        stdout = f"{stdout}\n[consensus arbiter rationale] {judged.rationale}"

            # 4. Composite Score: S = S_AST * (0.60 * S_test + 0.40 * S_semantic)
            raw_score = s_ast * ((0.60 * s_pytest) + (0.40 * s_semantic))
            composite_score = round(max(0.0, min(1.0, raw_score)), 4)
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
            test_assertions=test_assertions,
            scanned_imports=scanned_imports,
        )


ArbitrationEngine = ArbitrationArbiter

