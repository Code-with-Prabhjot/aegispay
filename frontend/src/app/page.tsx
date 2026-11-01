"use client";

import EscrowLedgerGrid from "@/components/EscrowLedgerGrid";
import FsmTimeline from "@/components/FsmTimeline";
import LedgerPanel from "@/components/LedgerPanel";
import { API_BASE } from "@/lib/api";
import { microsToUsd } from "@/lib/format";
import type { ScenarioName } from "@/lib/types";
import { useAegis } from "@/lib/useAegis";

const SCENARIOS: { name: ScenarioName; title: string; sub: string; tone: string }[] = [
  { name: "CLEAN_PASS", title: "Full Pass", sub: "S = 0.96 - capture 100%", tone: "border-emerald-500/50 hover:bg-emerald-500/10" },
  { name: "PARTIAL_DEFECT", title: "Partial Capture", sub: "S = 0.75 - pro-rata capture", tone: "border-amber-500/50 hover:bg-amber-500/10" },
  { name: "MALICIOUS_INJECTION", title: "Malicious Void", sub: "S = 0.22 - AST veto, full refund", tone: "border-rose-500/50 hover:bg-rose-500/10" },
];

export default function MissionControl() {
  const a = useAegis();
  const status =
    a.demoMode ? { label: "Demo data mode", dot: "bg-violet-400" }
    : a.connection === "online" ? { label: "Backend online", dot: "bg-emerald-400" }
    : a.connection === "offline" ? { label: "Backend offline", dot: "bg-rose-500" }
    : { label: "Connecting...", dot: "bg-slate-500" };
  const report = a.selected?.arbitration_report;

  return (
    <main className="mx-auto max-w-7xl space-y-6 p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">AegisPay Mission Control</h1>
          <p className="text-sm text-slate-400">Autonomous agent escrow on PayPal - polling every 2s</p>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="flex items-center gap-2 rounded-full border border-slate-700 px-3 py-1" data-testid="status">
            <span className={`h-2.5 w-2.5 rounded-full ${status.dot}`} />
            {status.label}
          </span>
          {a.demoMode && (
            <button onClick={a.disableDemo} className="rounded border border-slate-600 px-3 py-1 hover:bg-slate-800">
              Try live backend
            </button>
          )}
        </div>
      </header>

      {a.connection === "offline" && !a.demoMode && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-4 text-sm" role="alert">
          <p className="font-semibold text-rose-200">Backend offline</p>
          <p className="mt-1 text-slate-300">
            Could not reach <code className="font-mono">{API_BASE}</code>
            {a.error ? ` (${a.error})` : ""}. Start the FastAPI server or use deterministic demo data.
          </p>
          <button onClick={a.enableDemo} className="mt-3 rounded bg-violet-600 px-4 py-2 font-semibold hover:bg-violet-500">
            Use demo data
          </button>
        </div>
      )}

      <section className="grid gap-4 md:grid-cols-3">
        {SCENARIOS.map((s) => (
          <button
            key={s.name}
            disabled={a.busy !== null || (!a.demoMode && a.connection !== "online")}
            onClick={() => a.run(s.name)}
            className={`rounded-xl border bg-slate-900 p-6 text-left transition disabled:cursor-not-allowed disabled:opacity-40 ${s.tone}`}
          >
            <div className="text-xs uppercase tracking-wide text-slate-400">Run scenario</div>
            <div className="mt-1 text-xl font-bold">{s.title}</div>
            <div className="text-sm text-slate-400">{a.busy === s.name ? "Running..." : s.sub}</div>
          </button>
        ))}
      </section>

      <section className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-2 lg:col-span-2">
          <h2 className="text-lg font-semibold">Escrow ledger</h2>
          <EscrowLedgerGrid key={a.demoMode ? "demo" : "live"} contracts={a.contracts} selectedId={a.selectedId} onSelect={a.setSelectedId} />
        </div>
        <div className="space-y-2">
          <h2 className="text-lg font-semibold">FSM timeline</h2>
          <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
            <FsmTimeline contract={a.selected} />
            {report && (
              <div className="mt-4 border-t border-slate-800 pt-3 text-xs text-slate-300">
                <div>Composite S = {report.composite_score.toFixed(3)} ({report.passed_tests}/{report.total_tests} tests)</div>
                <div>Decision: {report.settlement.decision} - capture ${microsToUsd(report.settlement.capture_micros)}</div>
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Double-entry ledger</h2>
        <LedgerPanel ledger={a.ledger} />
      </section>
    </main>
  );
}
