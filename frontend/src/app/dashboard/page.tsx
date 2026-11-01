"use client";

import Link from "next/link";
import Background from "@/components/landing/Background";
import Logo from "@/components/landing/Logo";
import EscrowLedgerGrid from "@/components/EscrowLedgerGrid";
import FsmTimeline from "@/components/FsmTimeline";
import LedgerPanel from "@/components/LedgerPanel";
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
              <Logo /> AegisPay <span className="tag">Dashboard</span>
            </Link>
            <div className="right">
              <span className={`status ${statusClass}`}>{statusLabel}</span>
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
              <h1>Mission <em>control</em></h1>
              <p>Pick a scenario. We create an agreement, lock the escrow, verify the code and settle, all against the mocked PayPal client.</p>
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
                  <h3 style={{ fontSize: "0.85rem", textTransform: "uppercase", letterSpacing: "0.14em", color: "var(--cyan)", margin: 0 }}>
                    Escrow Ledger
                  </h3>
                  <span className="text-xs text-slate-400 font-mono">
                    {a.contracts.length} {a.contracts.length === 1 ? "agreement" : "agreements"}
                  </span>
                </div>
                <EscrowLedgerGrid
                  key={a.demoMode ? "demo" : "live"}
                  contracts={a.contracts}
                  selectedId={a.selectedId}
                  onSelect={a.setSelectedId}
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
    </>
  );
}
