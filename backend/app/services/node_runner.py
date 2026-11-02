"""
backend/app/services/node_runner.py
Isolated Node.js & Vitest-compatible sandboxed test execution runner for AegisPay.

Implements Task 1 of the Day 7 Polyglot Execution Sandbox Deliverable:
- Executes untrusted JavaScript/TypeScript worker deliverables against buyer test suites.
- Built-in lightweight Vitest/Jest shim & Node test harness (`node --test`).
- Strict execution timeout enforcement (default: 5.0s) safely terminating infinite loops.
- Rigorous environment variable scrubbing (zero API keys, secrets, or system tokens).
- Structured JSON output with passed, total_tests, passed_tests, failed_tests, score, stdout, stderr, duration_ms.
- AST / Static security scanner for JS/TS (JsSecurityScanner) auditing for forbidden imports and calls.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.schemas import TestAssertionItem

logger = logging.getLogger(__name__)


class NodeExecutionTimeoutError(TimeoutError):
    """Raised when Node.js test execution exceeds the timeout threshold."""
    pass


class JsSecurityScanner:
    """
    Static code analysis scanner auditing untrusted JavaScript / TypeScript deliverables
    for dangerous imports, child processes, filesystem writes, and unsafe dynamic execution.
    """

    DISALLOWED_MODULES: Set[str] = {
        "child_process",
        "fs",
        "net",
        "http",
        "https",
        "dgram",
        "cluster",
        "worker_threads",
        "dns",
        "tls",
        "process",
        "vm",
    }

    DISALLOWED_CALL_PATTERNS = [
        (r"\beval\s*\(", "Forbidden call: 'eval()'"),
        (r"\bFunction\s*\(", "Forbidden constructor: 'Function()'"),
        (r"\bnew\s+Function\s*\(", "Forbidden constructor: 'new Function()'"),
        (r"\bprocess\s*\.\s*(?:exit|kill|abort)\s*\(", "Forbidden call: process termination / control"),
    ]

    def __init__(self) -> None:
        self.violations: List[str] = []
        self.scanned_imports: List[str] = []

    def scan(self, source_code: str) -> Tuple[bool, List[str]]:
        self.violations.clear()
        self.scanned_imports.clear()

        # Check for disallowed imports/requires
        # e.g. require("child_process"), require('node:fs'), import ... from 'net'
        req_pattern = r"(?:require\s*\(\s*['\"](?:node:)?([a-zA-Z0-9_\-\.\/]+)['\"]\s*\)|import\s+(?:.*?from\s+)?['\"](?:node:)?([a-zA-Z0-9_\-\.\/]+)['\"])"
        for match in re.finditer(req_pattern, source_code):
            module_name = match.group(1) or match.group(2)
            if not module_name:
                continue
            root_mod = module_name.split("/")[0].replace("node:", "")
            if module_name not in self.scanned_imports:
                self.scanned_imports.append(module_name)
            if root_mod in self.DISALLOWED_MODULES:
                self.violations.append(f"Forbidden module import: '{module_name}'")

        # Check for disallowed calls
        for pattern, violation_msg in self.DISALLOWED_CALL_PATTERNS:
            if re.search(pattern, source_code):
                self.violations.append(violation_msg)

        is_safe = len(self.violations) == 0
        return is_safe, list(self.violations)


class NodeCodeRunner:
    """
    Executes untrusted worker JS/TS deliverables against buyer test suites in an
    isolated, ephemeral directory with hard timeouts and scrubbed environments.
    """

    def __init__(
        self,
        timeout_seconds: float = 5.0,
        node_bin: Optional[str] = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        default_node = shutil.which("node") or "C:\\Program Files\\nodejs\\node.exe" or "node"
        self.node_bin = node_bin or default_node

    def _build_scrubbed_env(self, temp_dir: str) -> Dict[str, str]:
        """
        Constructs a minimal, secure environment dictionary.
        Explicitly excludes any .env credentials, PayPal secrets, or system tokens.
        """
        scrubbed = {
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
            "PATH": os.environ.get("PATH", ""),
            "TEMP": temp_dir,
            "TMP": temp_dir,
            "NODE_ENV": "test",
            "NODE_OPTIONS": "--no-warnings",
            "HOMEDRIVE": os.environ.get("HOMEDRIVE", "C:"),
            "HOMEPATH": temp_dir,
            "USERPROFILE": temp_dir,
        }

        sensitive_patterns = (
            "PAYPAL",
            "SECRET",
            "TOKEN",
            "KEY",
            "PASSWORD",
            "DATABASE",
            "URL",
            "CREDENTIAL",
            "API",
            "AUTH",
            "PRIVATE",
            "LLM",
            "GEMINI",
            "OPENAI",
            "ANTHROPIC",
            "AWS",
            "STRIPE",
            "SIGNING",
        )
        for k in list(scrubbed.keys()):
            if any(pat in k.upper() for pat in sensitive_patterns):
                scrubbed.pop(k, None)

        return scrubbed

    def _setup_sandbox_files(
        self,
        temp_dir: str,
        worker_code: str,
        test_code: str,
        deliverable_filename: Optional[str] = None,
        test_filename: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Writes worker code, test code, cross-module bridges, and built-in Vitest shims.
        Returns (worker_path, test_path).
        """
        is_ts = (
            (deliverable_filename and deliverable_filename.endswith(".ts"))
            or (test_filename and test_filename.endswith(".ts"))
            or bool(re.search(r"\b(interface\s+\w+|type\s+\w+\s*=|:\s*(?:string|number|boolean|any|void)\b|<[A-Z]\w*>)", worker_code + test_code))
        )

        w_name = deliverable_filename or ("solution.ts" if is_ts else "solution.js")
        t_name = test_filename or ("test_suite.ts" if is_ts else "test_suite.js")

        worker_path = os.path.join(temp_dir, w_name)
        test_path = os.path.join(temp_dir, t_name)

        with open(worker_path, "w", encoding="utf-8") as f:
            f.write(worker_code)

        with open(test_path, "w", encoding="utf-8") as f:
            f.write(test_code)

        # Create bridge files so require('./solution'), require('./solution.js'), require('./solution.ts'),
        # require('./deliverable'), etc. all resolve seamlessly.
        if w_name.endswith(".ts"):
            js_bridge = os.path.join(temp_dir, "solution.js")
            if not os.path.exists(js_bridge):
                with open(js_bridge, "w", encoding="utf-8") as f:
                    f.write(f"module.exports = require('./{w_name}');\n")
        else:
            ts_bridge = os.path.join(temp_dir, "solution.ts")
            if not os.path.exists(ts_bridge):
                with open(ts_bridge, "w", encoding="utf-8") as f:
                    f.write(f"export * from './{w_name}';\n")

        for alias in ("deliverable.js", "deliverable.ts"):
            alias_path = os.path.join(temp_dir, alias)
            if not os.path.exists(alias_path):
                with open(alias_path, "w", encoding="utf-8") as f:
                    f.write(f"module.exports = require('./{w_name}');\n")

        # Set up node_modules/vitest shim
        vitest_dir = os.path.join(temp_dir, "node_modules", "vitest")
        os.makedirs(vitest_dir, exist_ok=True)
        with open(os.path.join(vitest_dir, "package.json"), "w", encoding="utf-8") as f:
            json.dump({
                "name": "vitest",
                "main": "index.js",
                "module": "index.mjs",
                "exports": {
                    ".": {
                        "import": "./index.mjs",
                        "require": "./index.js"
                    }
                }
            }, f)

        vitest_cjs = """const test = require('node:test');
const assert = require('node:assert');

const expect = (actual) => {
  const matcher = {
    toBe: (expected) => assert.strictEqual(actual, expected),
    toEqual: (expected) => assert.deepStrictEqual(actual, expected),
    toBeCloseTo: (expected, precision = 2) => {
      const diff = Math.abs(actual - expected);
      assert.ok(diff < Math.pow(10, -precision) / 2);
    },
    toBeTruthy: () => assert.ok(actual),
    toBeFalsy: () => assert.ok(!actual),
    toBeNull: () => assert.strictEqual(actual, null),
    toBeUndefined: () => assert.strictEqual(actual, undefined),
    toBeDefined: () => assert.notStrictEqual(actual, undefined),
    toBeGreaterThan: (n) => assert.ok(actual > n),
    toBeGreaterThanOrEqual: (n) => assert.ok(actual >= n),
    toBeLessThan: (n) => assert.ok(actual < n),
    toBeLessThanOrEqual: (n) => assert.ok(actual <= n),
    toContain: (item) => {
      if (typeof actual === 'string' || Array.isArray(actual)) {
        assert.ok(actual.includes(item));
      } else {
        throw new Error('toContain requires string or array');
      }
    },
    toThrow: (expected) => {
      if (typeof actual === 'function') {
        assert.throws(actual, expected);
      } else {
        throw new Error('toThrow requires a function');
      }
    },
    toHaveLength: (len) => assert.strictEqual(actual.length, len),
  };
  matcher.not = {
    toBe: (expected) => assert.notStrictEqual(actual, expected),
    toEqual: (expected) => assert.notDeepStrictEqual(actual, expected),
    toBeTruthy: () => assert.ok(!actual),
    toBeFalsy: () => assert.ok(actual),
    toBeNull: () => assert.notStrictEqual(actual, null),
    toBeUndefined: () => assert.notStrictEqual(actual, undefined),
    toBeDefined: () => assert.strictEqual(actual, undefined),
    toContain: (item) => assert.ok(!actual.includes(item)),
  };
  return matcher;
};

module.exports = {
  describe: test.describe,
  it: test.it,
  test: test.test,
  assert: assert,
  expect: expect,
  beforeEach: test.beforeEach,
  afterEach: test.afterEach,
  beforeAll: test.before,
  afterAll: test.after,
  default: {
    describe: test.describe,
    it: test.it,
    test: test.test,
    assert: assert,
    expect: expect,
  }
};
"""
        with open(os.path.join(vitest_dir, "index.js"), "w", encoding="utf-8") as f:
            f.write(vitest_cjs)

        vitest_esm = """import test from 'node:test';
import assert from 'node:assert';

export const describe = test.describe;
export const it = test.it;
export const testFn = test.test;
export { testFn as test };
export { assert };
export const expect = (actual) => {
  const matcher = {
    toBe: (expected) => assert.strictEqual(actual, expected),
    toEqual: (expected) => assert.deepStrictEqual(actual, expected),
    toBeCloseTo: (expected, precision = 2) => {
      const diff = Math.abs(actual - expected);
      assert.ok(diff < Math.pow(10, -precision) / 2);
    },
    toBeTruthy: () => assert.ok(actual),
    toBeFalsy: () => assert.ok(!actual),
    toBeNull: () => assert.strictEqual(actual, null),
    toBeUndefined: () => assert.strictEqual(actual, undefined),
    toBeDefined: () => assert.notStrictEqual(actual, undefined),
    toBeGreaterThan: (n) => assert.ok(actual > n),
    toBeGreaterThanOrEqual: (n) => assert.ok(actual >= n),
    toBeLessThan: (n) => assert.ok(actual < n),
    toBeLessThanOrEqual: (n) => assert.ok(actual <= n),
    toContain: (item) => {
      if (typeof actual === 'string' || Array.isArray(actual)) {
        assert.ok(actual.includes(item));
      } else {
        throw new Error('toContain requires string or array');
      }
    },
    toThrow: (expected) => {
      if (typeof actual === 'function') {
        assert.throws(actual, expected);
      } else {
        throw new Error('toThrow requires a function');
      }
    },
    toHaveLength: (len) => assert.strictEqual(actual.length, len),
  };
  matcher.not = {
    toBe: (expected) => assert.notStrictEqual(actual, expected),
    toEqual: (expected) => assert.notDeepStrictEqual(actual, expected),
    toBeTruthy: () => assert.ok(!actual),
    toBeFalsy: () => assert.ok(actual),
    toBeNull: () => assert.notStrictEqual(actual, null),
    toBeUndefined: () => assert.notStrictEqual(actual, undefined),
    toBeDefined: () => assert.strictEqual(actual, undefined),
    toContain: (item) => assert.ok(!actual.includes(item)),
  };
  return matcher;
};
export const beforeEach = test.beforeEach;
export const afterEach = test.afterEach;
export const beforeAll = test.before;
export const afterAll = test.after;
export default { describe, it, test: testFn, expect, assert };
"""
        with open(os.path.join(vitest_dir, "index.mjs"), "w", encoding="utf-8") as f:
            f.write(vitest_esm)

        # Global bootstrap harness
        boot_code = """const vitest = require('./node_modules/vitest/index.js');
globalThis.describe = vitest.describe;
globalThis.it = vitest.it;
globalThis.test = vitest.test;
globalThis.assert = vitest.assert;
globalThis.expect = vitest.expect;
globalThis.beforeEach = vitest.beforeEach;
globalThis.afterEach = vitest.afterEach;
globalThis.beforeAll = vitest.beforeAll;
globalThis.afterAll = vitest.afterAll;
"""
        with open(os.path.join(temp_dir, "harness_bootstrap.js"), "w", encoding="utf-8") as f:
            f.write(boot_code)

        return worker_path, test_path

    def run(
        self,
        worker_code: str,
        test_code: str,
        deliverable_filename: Optional[str] = None,
        test_filename: Optional[str] = None,
        timeout: Optional[float] = None,
        raise_on_timeout: bool = True,
    ) -> Dict[str, Any]:
        """
        Executes isolated tests in an ephemeral directory using Node.js.
        Returns structured JSON results:
        {
          "passed": bool,
          "total_tests": int,
          "passed_tests": int,
          "failed_tests": int,
          "score": float,
          "stdout": str,
          "stderr": str,
          "duration_ms": float
        }
        """
        exec_timeout = timeout or self.timeout_seconds
        temp_dir = tempfile.mkdtemp(prefix="aegis_node_sandbox_")

        try:
            _, test_path = self._setup_sandbox_files(
                temp_dir=temp_dir,
                worker_code=worker_code,
                test_code=test_code,
                deliverable_filename=deliverable_filename,
                test_filename=test_filename,
            )

            clean_env = self._build_scrubbed_env(temp_dir)
            t_file = os.path.basename(test_path)

            cmd = [
                self.node_bin,
                "--require",
                os.path.join(temp_dir, "harness_bootstrap.js"),
                "--experimental-detect-module",
                "--test",
                t_file,
            ]

            t_start = time.perf_counter()
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=temp_dir,
                    env=clean_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=exec_timeout,
                )
                duration_ms = (time.perf_counter() - t_start) * 1000.0
                stdout = proc.stdout or ""
                stderr = proc.stderr or ""
                returncode = proc.returncode

                passed_cnt, failed_cnt, total_cnt, node_dur = self._parse_node_test_output(
                    stdout + "\n" + stderr, returncode
                )
                if node_dur > 0:
                    duration_ms = node_dur

                score = round(passed_cnt / total_cnt, 4) if total_cnt > 0 else 0.0
                passed = (failed_cnt == 0 and total_cnt > 0 and returncode == 0)

                return {
                    "passed": passed,
                    "total_tests": total_cnt,
                    "passed_tests": passed_cnt,
                    "failed_tests": failed_cnt,
                    "score": score,
                    "stdout": stdout,
                    "stderr": stderr,
                    "duration_ms": round(duration_ms, 2),
                }

            except subprocess.TimeoutExpired as exc:
                duration_ms = (time.perf_counter() - t_start) * 1000.0
                if raise_on_timeout:
                    raise NodeExecutionTimeoutError(
                        f"Execution timed out (exceeded {exec_timeout:.1f}s threshold)."
                    ) from exc

                raw_out = exc.stdout or ""
                raw_err = exc.stderr or ""
                out_txt = raw_out if isinstance(raw_out, str) else raw_out.decode("utf-8", errors="replace")
                err_txt = raw_err if isinstance(raw_err, str) else raw_err.decode("utf-8", errors="replace")

                return {
                    "passed": False,
                    "total_tests": 1,
                    "passed_tests": 0,
                    "failed_tests": 1,
                    "score": 0.0,
                    "stdout": out_txt,
                    "stderr": f"Execution timed out (exceeded {exec_timeout:.1f}s threshold).\n{err_txt}",
                    "duration_ms": round(duration_ms, 2),
                }

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def run_tests(
        self,
        worker_code: str,
        test_code: str,
        deliverable_filename: Optional[str] = None,
        test_filename: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[int, int, str]:
        """
        Compatibility method returning (passed_tests, total_tests, stdout_output).
        Does not raise on timeout so arbiter can safely grade the failure.
        """
        res = self.run(
            worker_code=worker_code,
            test_code=test_code,
            deliverable_filename=deliverable_filename,
            test_filename=test_filename,
            timeout=timeout,
            raise_on_timeout=False,
        )
        combined = res["stdout"]
        if res["stderr"]:
            combined += f"\n{res['stderr']}"
        return res["passed_tests"], res["total_tests"], combined

    @staticmethod
    def _parse_node_test_output(output: str, returncode: int) -> Tuple[int, int, int, float]:
        """
        Parses `node --test` summary output into:
        (passed_tests, failed_tests, total_tests, duration_ms)
        """
        tests_m = re.search(r"(?:ℹ|\u2139|\b)\s*tests\s+(\d+)", output)
        pass_m = re.search(r"(?:ℹ|\u2139|\b)\s*pass\s+(\d+)", output)
        fail_m = re.search(r"(?:ℹ|\u2139|\b)\s*fail\s+(\d+)", output)
        dur_m = re.search(r"(?:ℹ|\u2139|\b)\s*duration_ms\s+([\d\.]+)", output)

        total = int(tests_m.group(1)) if tests_m else 0
        passed = int(pass_m.group(1)) if pass_m else 0
        failed = int(fail_m.group(1)) if fail_m else 0
        dur_ms = float(dur_m.group(1)) if dur_m else 0.0

        if total == 0:
            if returncode != 0:
                return 0, 1, 1, dur_ms
            return 0, 0, 0, dur_ms

        if failed == 0 and passed < total and returncode != 0:
            failed = total - passed

        return passed, failed, total, dur_ms

    @staticmethod
    def _parse_test_assertions(output: str) -> List[TestAssertionItem]:
        """
        Parses individual test items, status, duration, and failure error messages
        from `node --test` output for master-detail inspection.
        """
        assertions: List[TestAssertionItem] = []
        # Try indented subtests first (tests inside suites)
        matches = list(re.finditer(r"^\s{2,}([✔✖])\s+(.+?)\s+\(([\d\.]+)(?:ms|s)\)", output, re.MULTILINE))
        if not matches:
            # Top-level tests (no suites)
            matches = list(re.finditer(r"^([✔✖])\s+(.+?)\s+\(([\d\.]+)(?:ms|s)\)", output, re.MULTILINE))

        seen_names: Set[str] = set()
        for m in matches:
            status_sym = m.group(1)
            name = m.group(2).strip()
            if name in seen_names:
                continue
            seen_names.add(name)
            dur_val = float(m.group(3))
            status = "PASSED" if ("✔" in status_sym or "\u2714" in status_sym) else "FAILED"
            err_msg: Optional[str] = None
            if status == "FAILED":
                fail_pat = rf"✖\s+{re.escape(name)}\s+\([^\)]+\)\s*\n(.*?)(?=\n✖|\nℹ|\Z)"
                fail_match = re.search(fail_pat, output, re.DOTALL)
                if fail_match:
                    lines = [line.strip() for line in fail_match.group(1).splitlines() if line.strip()]
                    err_msg = lines[0] if lines else "Assertion failed"
                else:
                    err_msg = "Assertion failed"
            assertions.append(
                TestAssertionItem(
                    name=name,
                    status=status,
                    duration_ms=dur_val,
                    error_message=err_msg,
                )
            )

        if not assertions:
            pass_m = re.search(r"ℹ\s+pass\s+(\d+)", output)
            fail_m = re.search(r"ℹ\s+fail\s+(\d+)", output)
            p_cnt = int(pass_m.group(1)) if pass_m else 0
            f_cnt = int(fail_m.group(1)) if fail_m else 0
            for i in range(p_cnt):
                assertions.append(TestAssertionItem(name=f"test_assertion_{i+1:02d}", status="PASSED", duration_ms=10.0))
            for i in range(f_cnt):
                assertions.append(TestAssertionItem(name=f"test_assertion_{p_cnt+i+1:02d}", status="FAILED", duration_ms=10.0, error_message="Assertion failed"))

        return assertions


NodeExecutionRunner = NodeCodeRunner
NodeRunner = NodeCodeRunner
