#!/usr/bin/env python3
"""
scripts/run_live_demo.py
AegisPay 1-Click Interactive Demo Orchestrator & Multi-Scenario Live Runner.

Launches and supervises:
  - FastAPI Backend (Uvicorn) on port 8000
  - Next.js Mission Control Frontend on port 3000
Waits for /health probe, opens the default browser, and exposes an interactive
terminal console to trigger financial arbitration scenarios with zero latency.
"""

import argparse
import atexit
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

# Windows console encoding fix
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_PROCESS = None
FRONTEND_PROCESS = None


def get_python_exe() -> str:
    """Returns path to python executable, prioritizing local virtual environment."""
    venv_py = ROOT_DIR / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python")
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def terminate_process(proc: subprocess.Popen, name: str) -> None:
    """Gracefully terminates a subprocess and its children."""
    if proc is None or proc.poll() is not None:
        return
    print(f"[*] Stopping {name} (PID: {proc.pid})...")
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            proc.terminate()
            proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def cleanup() -> None:
    """Exit handler to ensure no background port orphans remain."""
    global BACKEND_PROCESS, FRONTEND_PROCESS
    terminate_process(BACKEND_PROCESS, "FastAPI Backend")
    terminate_process(FRONTEND_PROCESS, "Next.js Frontend")


atexit.register(cleanup)


def check_health(url: str, timeout: float = 1.0) -> bool:
    """Probes an HTTP endpoint returning True if 200 OK."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AegisPay-Demo-Probe"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def wait_for_health(url: str, max_wait: float = 20.0, desc: str = "service") -> bool:
    """Polls health endpoint until responsive or timeout expires."""
    start = time.time()
    sys.stdout.write(f"[*] Waiting for {desc} at {url} ")
    sys.stdout.flush()
    while time.time() - start < max_wait:
        if check_health(url):
            sys.stdout.write(" [ONLINE]\n")
            sys.stdout.flush()
            return True
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(0.5)
    sys.stdout.write(" [TIMED OUT]\n")
    sys.stdout.flush()
    return False


def start_backend(port: int = 8000) -> subprocess.Popen:
    """Spawns FastAPI backend subprocess via uvicorn."""
    global BACKEND_PROCESS
    if check_health(f"http://localhost:{port}/health"):
        print(f"[+] Backend is already running on port {port}.")
        return None

    py_exe = get_python_exe()
    cmd = [
        py_exe,
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    print(f"[*] Spawning FastAPI backend on port {port}...")
    BACKEND_PROCESS = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return BACKEND_PROCESS


def start_frontend(port: int = 3000) -> subprocess.Popen:
    """Spawns Next.js dashboard subprocess."""
    global FRONTEND_PROCESS
    if check_health(f"http://localhost:{port}"):
        print(f"[+] Frontend is already running on port {port}.")
        return None

    frontend_dir = ROOT_DIR / "frontend"
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    cmd = [npm_cmd, "run", "dev"]
    print(f"[*] Spawning Next.js frontend on port {port}...")
    FRONTEND_PROCESS = subprocess.Popen(
        cmd,
        cwd=str(frontend_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return FRONTEND_PROCESS


def execute_scenario(scenario_key: str, base_url: str = "http://localhost:8000") -> dict:
    """Triggers scenario execution against AegisPay backend demo API."""
    url = f"{base_url}/api/v1/demo/scenarios/{scenario_key}"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={"Content-Type": "application/json", "User-Agent": "AegisPay-Demo-Orchestrator"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except urllib.error.HTTPError as exc:
        err_msg = exc.read().decode("utf-8")
        print(f"[!] Scenario execution failed HTTP {exc.code}: {err_msg}")
        return {}
    except Exception as exc:
        print(f"[!] Scenario execution error: {exc}")
        return {}


def print_scenario_result(data: dict) -> None:
    """Formats and prints summary of scenario financial outcome."""
    if not data:
        return
    agr_id = data.get("agreement_id", "N/A")
    state = data.get("fsm_state", "N/A")
    report = data.get("arbitration_report") or {}
    score = report.get("composite_score", 0.0)
    ast_safe = report.get("ast_safe", True)
    settlement = report.get("settlement") or {}
    decision = settlement.get("decision", "N/A")
    capture_usd = settlement.get("capture_micros", 0) / 1_000_000
    buyer_released_usd = settlement.get("buyer_released_micros", 0) / 1_000_000

    print("------------------------------------------------------------------------")
    print(f"  Agreement ID      : {agr_id}")
    print(f"  Final FSM State   : {state}")
    print(f"  AST Security Gate : {'PASS (Safe)' if ast_safe else 'HARD VETO (Banned AST)'}")
    print(f"  Composite Score S : {score:.3f}")
    print(f"  Clearing Verdict  : {decision}")
    print(f"  Worker Payout     : ${capture_usd:.2f} USD")
    print(f"  Buyer Refund      : ${buyer_released_usd:.2f} USD")
    print("------------------------------------------------------------------------")


def run_scenario_1(base_url: str) -> None:
    print("\n[▶] Running Scenario 1: Clean Pass (Score 0.96 -> $15.00 Payout)...")
    res = execute_scenario("CLEAN_PASS", base_url)
    print_scenario_result(res)


def run_scenario_2(base_url: str) -> None:
    print("\n[▶] Running Scenario 2: Partial Defect (Score 0.75 -> $10.50 Payout / $4.50 Refund)...")
    res = execute_scenario("PARTIAL_DEFECT", base_url)
    print_scenario_result(res)


def run_scenario_3(base_url: str) -> None:
    print("\n[▶] Running Scenario 3: Malicious Injection (AST Hard Veto -> $0.00 Charged)...")
    res = execute_scenario("MALICIOUS_INJECTION", base_url)
    print_scenario_result(res)


def run_all_scenarios(base_url: str, delay: float = 2.5) -> None:
    print(f"\n[▶] Running All Scenarios Sequentially (Delay: {delay}s for screen recording)...")
    run_scenario_1(base_url)
    time.sleep(delay)
    run_scenario_2(base_url)
    time.sleep(delay)
    run_scenario_3(base_url)
    print("[✓] All scenarios executed successfully. Dashboard updated in real-time.")


def interactive_menu(base_url: str) -> None:
    """Presents user with interactive scenario runner menu."""
    while True:
        print("\n========================================================================")
        print("          AegisPay Mission Control - Interactive Live Demo              ")
        print("========================================================================")
        print("  [1] Run Scenario 1: Clean Pass (Score 0.96 -> $15.00 Payout)")
        print("  [2] Run Scenario 2: Partial Defect (Score 0.75 -> $10.50 Payout / $4.50 Refund)")
        print("  [3] Run Scenario 3: Malicious Injection (AST Hard Veto -> $0.00 Charged)")
        print("  [4] Run All Scenarios with 2.5s Delay (For Screen Recording)")
        print("  [q] Gracefully shut down all processes")
        print("========================================================================")
        choice = input("Select an option [1/2/3/4/q]: ").strip().lower()

        if choice == "1":
            run_scenario_1(base_url)
        elif choice == "2":
            run_scenario_2(base_url)
        elif choice == "3":
            run_scenario_3(base_url)
        elif choice == "4":
            run_all_scenarios(base_url, delay=2.5)
        elif choice in ("q", "quit", "exit"):
            print("[*] Gracefully terminating demo launcher...")
            break
        else:
            print("[!] Invalid selection. Please choose 1, 2, 3, 4, or q.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AegisPay 1-Click Interactive Demo Launcher & Scenario Orchestrator."
    )
    parser.add_argument(
        "--scenario",
        choices=["1", "2", "3", "4", "all"],
        default=None,
        help="Run a specific scenario directly without interactive prompt.",
    )
    parser.add_argument(
        "--backend-port",
        type=int,
        default=8000,
        help="FastAPI backend port (default: 8000).",
    )
    parser.add_argument(
        "--frontend-port",
        type=int,
        default=3000,
        help="Next.js frontend port (default: 3000).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically launch web browser.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.5,
        help="Delay in seconds between scenarios in automated sequence (default: 2.5s).",
    )
    args = parser.parse_args()

    backend_url = f"http://localhost:{args.backend_port}"
    frontend_url = f"http://localhost:{args.frontend_port}"

    print("========================================================================")
    print("      AegisPay Autonomous Escrow Platform - 1-Click Demo Launcher       ")
    print("========================================================================")

    # 1. Start Services
    start_backend(port=args.backend_port)
    start_frontend(port=args.frontend_port)

    # 2. Wait for Health Probe
    backend_ok = wait_for_health(f"{backend_url}/health", max_wait=20.0, desc="FastAPI Backend")
    if not backend_ok:
        print("[!] Backend failed to start or respond to health probe.")
        return 1

    # 3. Launch Browser
    if not args.no_browser:
        target_page = f"{frontend_url}/dashboard"
        print(f"[*] Launching Mission Control Dashboard in default browser: {target_page}")
        try:
            webbrowser.open(target_page)
        except Exception:
            pass

    # 4. Handle Direct CLI Execution or Interactive Menu
    if args.scenario:
        if args.scenario == "1":
            run_scenario_1(backend_url)
        elif args.scenario == "2":
            run_scenario_2(backend_url)
        elif args.scenario == "3":
            run_scenario_3(backend_url)
        elif args.scenario in ("4", "all"):
            run_all_scenarios(backend_url, delay=args.delay)
    else:
        interactive_menu(backend_url)

    return 0


if __name__ == "__main__":
    def _sig_handler(sig, frame):
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _sig_handler)

    sys.exit(main())
