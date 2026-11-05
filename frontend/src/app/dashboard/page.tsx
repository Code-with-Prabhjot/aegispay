"use client";

import { useState } from "react";
import Link from "next/link";
import Background from "@/components/landing/Background";
import Logo from "@/components/landing/Logo";
import EscrowLedgerGrid from "@/components/EscrowLedgerGrid";
import FsmTimeline from "@/components/FsmTimeline";
import LedgerPanel from "@/components/LedgerPanel";
import CodeMutationStudio from "@/components/CodeMutationStudio";
import VaultManagementModal from "@/components/VaultManagementModal";
import { API_BASE } from "@/lib/api";
import { microsToUsd } from "@/lib/format";
import type { ScenarioName } from "@/lib/types";
import { useAegis } from "@/lib/useAegis";
import "@/components/landing/landing.css";

const SCENARIOS = [
  { key: "CLEAN_PASS", title: "Clean pass", desc: "Correct, safe code. Full capture." },
  { key: "PARTIAL_DEFECT", title: "Partial defect", desc: "Some tests fail. Proportional capture." },
  { key: "MALICIOUS_INJECTION", title: "Malicious injection", desc: "AST veto. Authorization voided." },
];

export default function Dashboard() {
  const a = useAegis();
  const [isStudioOpen, setIsStudioOpen] = useState(false);
  const [isVaultModalOpen, setIsVaultModalOpen] = useState(false);

  const statusClass =
    a.connection === "online" ? "up"
    : a.connection === "offline" ? (a.demoMode ? "up" : "down")
    : "";

  const statusLabel =
    a.demoMode ? "Demo data mode (offline)"
    : a.connection === "online" ? "API online (mock PayPal)"
    : a.connection === "offline" ? "API offline"
    : "checking...";

  const report = a.selected?.arbitration_report;

  return (
    <>
      <Background />
      <div className="page">
        <div className="dash">
          <header className="dnav">
            <Link className="brand" href="/">
              <Logo /> AegisPay <span className="tag">Mission Control</span>
            </Link>
            <div className="right flex items-center gap-3">
              <span className={`status ${statusClass}`}>{statusLabel}</span>

              {/* Vault Management Allowance Modal Trigger */}
              <button
                onClick={() => setIsVaultModalOpen(true)}
                className="btn sm border border-cyan-500/40 bg-slate-900/80 hover:bg-cyan-500/20 text-cyan-300 font-semibold transition-all flex items-center gap-1.5"
              >
                <span className="w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_8px_#34d399]" />
                🛡️ Buyer Vault
              </button>

              {/* Code Mutation Studio Slide-Over Trigger */}
              <button
                onClick={() => setIsStudioOpen(true)}
                className="btn sm border border-cyan-400/80 bg-cyan-950/80 text-cyan-300 hover:bg-cyan-500 hover:text-slate-950 shadow-[0_0_15px_rgba(0,229,255,0.35)] font-semibold transition-all flex items-center gap-1.5"
              >
                <span className="w-2 h-2 rounded-full bg-cyan-400 shadow-[0_0_8px_#00e5ff] animate-ping" />
                ⚡ Live Mutation Studio
              </button>

              {a.demoMode && (
                <button onClick={a.disableDemo} className="btn sm ghost">
                  Try live backend
                </button>
              )}
              <Link className="btn sm ghost" href="/">
                ← Home
              </Link>
            </div>
          </header>

          <main>
            <section className="intro">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                  <h1>Mission <em>control</em> Ultra</h1>
                  <p>
                    AG Grid Enterprise Master-Detail Financial Workstation with real-time 60fps WebSocket streaming and cryptographic Merkle proof verification.
                  </p>
                </div>
                <button
                  onClick={() => setIsStudioOpen(true)}
                  className="px-4 py-2 rounded-lg border border-cyan-500/40 bg-slate-900/80 hover:bg-cyan-500/20 text-cyan-300 font-mono text-xs shadow-[0_0_15px_rgba(0,229,255,0.15)] flex items-center gap-2 transition-all"
                >
                  <svg className="w-4 h-4 text-cyan-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
                  </svg>
                  <span>Open Code Mutation Studio →</span>
                </button>
              </div>
            </section>

            {a.connection === "offline" && !a.demoMode && (
              <div className="alert" role="alert">
                Can&apos;t reach the backend at <code>{API_BASE}</code>. In another terminal run <code>uvicorn backend.app.main:app --port 8000</code>, or{" "}
                <button onClick={a.enableDemo} className="underline font-bold text-cyan-300 hover:text-cyan-200">
                  use demo data
                </button>.
              </div>
            )}
            {a.error && a.connection !== "offline" && <div className="alert">{a.error}</div>}

            <section className="scen">
              {SCENARIOS.map((s) => (
                <button
                  key={s.key}
                  className={`sc ${a.busy === s.key ? "busy" : ""}`}
                  disabled={a.busy !== null}
                  onClick={() => a.run(s.key as ScenarioName)}
                >
                  <b>{s.title}</b>
                  <span>{s.desc}</span>
                  <em>{a.busy === s.key ? "Running..." : "Run scenario →"}</em>
                </button>
              ))}
            </section>

            <div className="grid gap-6 lg:grid-cols-3 mt-6">
              <div
                className="panel lg:col-span-2"
                style={{
                  background: "rgba(10, 15, 30, 0.7)",
                  borderRadius: "var(--r)",
                  border: "1px solid rgba(0, 229, 255, 0.2)",
                  boxShadow: "0 0 0 1px rgba(0, 229, 255, 0.1), 0 20px 50px -20px rgba(0, 100, 255, 0.3)",
                  backdropFilter: "blur(18px)",
                }}
              >
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <h3 style={{ fontSize: "0.85rem", textTransform: "uppercase", letterSpacing: "0.14em", color: "var(--cyan)", margin: 0 }}>
                      AG Grid Master-Detail Workstation
                    </h3>
                    <span className="text-[10px] rounded bg-cyan-950/80 border border-cyan-500/40 px-2 py-0.5 text-cyan-300 font-mono">
                      Enterprise Hierarchical
                    </span>
                  </div>
                  <span className="text-xs text-slate-400 font-mono">
                    {a.contracts.length} {a.contracts.length === 1 ? "agreement" : "agreements"}
                  </span>
                </div>
                <EscrowLedgerGrid
                  key={a.demoMode ? "demo" : "live"}
                  contracts={a.contracts}
                  selectedId={a.selectedId}
                  onSelect={a.setSelectedId}
                  onRefresh={a.refresh}
                />
              </div>

              <div
                className="panel"
                style={{
                  background: "rgba(10, 15, 30, 0.7)",
                  borderRadius: "var(--r)",
                  border: "1px solid rgba(0, 229, 255, 0.2)",
                  boxShadow: "0 0 0 1px rgba(0, 229, 255, 0.1), 0 20px 50px -20px rgba(0, 100, 255, 0.3)",
                  backdropFilter: "blur(18px)",
                }}
              >
                <div className="flex items-center justify-between mb-4">
                  <h3 style={{ fontSize: "0.85rem", textTransform: "uppercase", letterSpacing: "0.14em", color: "var(--cyan)", margin: 0 }}>
                    FSM Timeline
                  </h3>
                  {a.selected && (
                    <span className="chip" style={{ fontSize: "0.7rem", padding: "0.2rem 0.6rem" }}>
                      {a.selected.fsm_state}
                    </span>
                  )}
                </div>
                <FsmTimeline contract={a.selected} />
                {report && (
                  <div className="mt-4 border-t border-slate-700/60 pt-3 text-xs text-slate-300">
                    <div>
                      Composite S = {report.composite_score.toFixed(3)} ({report.passed_tests}/{report.total_tests} tests)
                    </div>
                    <div>
                      Decision: {report.settlement.decision} — capture ${microsToUsd(report.settlement.capture_micros)}
                    </div>
                  </div>
                )}
              </div>
            </div>

            <div
              className="panel wide mt-6"
              style={{
                background: "rgba(10, 15, 30, 0.7)",
                borderRadius: "var(--r)",
                border: "1px solid rgba(0, 229, 255, 0.2)",
                boxShadow: "0 0 0 1px rgba(0, 229, 255, 0.1), 0 20px 50px -20px rgba(0, 100, 255, 0.3)",
                backdropFilter: "blur(18px)",
              }}
            >
              <div className="flex items-center justify-between mb-4">
                <h3 style={{ fontSize: "0.85rem", textTransform: "uppercase", letterSpacing: "0.14em", color: "var(--cyan)", margin: 0 }}>
                  Double-Entry Ledger
                </h3>
                <span className="text-xs text-slate-400 font-mono">
                  {Object.keys(a.ledger.balances).length} accounts
                </span>
              </div>
              <LedgerPanel ledger={a.ledger} />
            </div>
          </main>
        </div>
      </div>

      {/* Slide-Over Mutation Studio */}
      <CodeMutationStudio
        isOpen={isStudioOpen}
        onClose={() => setIsStudioOpen(false)}
        selectedContract={a.selected}
        onContractSettled={(c) => {
          a.setSelectedId(c.agreement_id);
          a.refresh();
        }}
        demoMode={a.demoMode}
      />

      {/* Buyer Vault Management Modal */}
      <VaultManagementModal
        isOpen={isVaultModalOpen}
        onClose={() => setIsVaultModalOpen(false)}
      />
    </>
  );
}
