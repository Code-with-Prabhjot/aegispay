"use client";

import { useState } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import Background from "@/components/landing/Background";
import Logo from "@/components/landing/Logo";
import FsmTimeline from "@/components/FsmTimeline";
import LedgerPanel from "@/components/LedgerPanel";
import VaultManagementModal from "@/components/VaultManagementModal";
import { API_BASE } from "@/lib/api";
import { microsToUsd } from "@/lib/format";
import type { ScenarioName } from "@/lib/types";
import { useAegis } from "@/lib/useAegis";
import "@/components/landing/landing.css";

const EscrowLedgerGrid = dynamic(() => import("@/components/EscrowLedgerGrid"), {
  ssr: false,
  loading: () => (
    <div
      role="status"
      aria-label="Loading Escrow Ledger Grid"
      className="flex flex-col gap-3 p-4 w-full h-[400px] rounded-lg animate-pulse"
      style={{
        background: "rgba(11, 17, 32, 0.6)",
        border: "1px solid rgba(0, 240, 255, 0.2)",
        backdropFilter: "blur(16px)",
      }}
    >
      <div className="flex items-center justify-between pb-3 border-b border-cyan-500/20">
        <div className="h-4 w-36 bg-cyan-500/30 rounded" />
        <div className="h-4 w-24 bg-cyan-500/20 rounded" />
      </div>
      <div className="flex-1 flex flex-col gap-2.5 justify-around py-2">
        <div className="h-8 w-full bg-slate-800/60 rounded border border-cyan-500/10" />
        <div className="h-8 w-full bg-slate-800/40 rounded border border-cyan-500/10" />
        <div className="h-8 w-full bg-slate-800/60 rounded border border-cyan-500/10" />
        <div className="h-8 w-full bg-slate-800/40 rounded border border-cyan-500/10" />
        <div className="h-8 w-full bg-slate-800/50 rounded border border-cyan-500/10" />
      </div>
      <div className="flex justify-between items-center pt-2 border-t border-cyan-500/20 text-xs text-cyan-400/70 font-mono">
        <span>Initializing AG Grid Engine...</span>
        <span className="w-2 h-2 rounded-full bg-cyan-400 animate-ping" />
      </div>
    </div>
  ),
});

const CodeMutationStudio = dynamic(() => import("@/components/CodeMutationStudio"), {
  ssr: false,
});

const SCENARIOS = [
  {
    key: "CLEAN_PASS",
    title: "Clean pass",
    desc: "Correct, safe code. Full capture.",
    proves: "Verifies 10/10 tests & pays worker",
  },
  {
    key: "PARTIAL_DEFECT",
    title: "Partial defect",
    desc: "Some tests fail. Proportional capture.",
    proves: "Tests pro-rata refund math",
  },
  {
    key: "MALICIOUS_INJECTION",
    title: "Malicious injection",
    desc: "AST veto. Authorization voided.",
    proves: "Blocks malicious code & voids hold",
  },
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
                  <span className="text-[11px] font-mono text-cyan-300/80 flex items-center gap-1.5 mt-0.5">
                    <span className="text-cyan-400">🛡️</span> {s.proves}
                  </span>
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
