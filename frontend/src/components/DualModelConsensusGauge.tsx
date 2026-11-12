"use client";

import React, { useState } from "react";

export interface RubricBreakdown {
  correctness: number;
  edgeCases: number;
  docstrings: number;
  typeSafety: number;
}

export interface DualModelConsensusGaugeProps {
  geminiScore?: number;
  claudeScore?: number;
  geminiLatencyMs?: number;
  claudeLatencyMs?: number;
  geminiRubric?: RubricBreakdown;
  claudeRubric?: RubricBreakdown;
  className?: string;
  allowInteractiveSimulation?: boolean;
}

const DEFAULT_GEMINI_RUBRIC: RubricBreakdown = {
  correctness: 0.98,
  edgeCases: 0.94,
  docstrings: 0.92,
  typeSafety: 0.96,
};

const DEFAULT_CLAUDE_RUBRIC: RubricBreakdown = {
  correctness: 0.95,
  edgeCases: 0.91,
  docstrings: 0.94,
  typeSafety: 0.95,
};

export default function DualModelConsensusGauge({
  geminiScore: propGeminiScore,
  claudeScore: propClaudeScore,
  geminiLatencyMs,
  claudeLatencyMs,
  geminiRubric = DEFAULT_GEMINI_RUBRIC,
  claudeRubric = DEFAULT_CLAUDE_RUBRIC,
  className = "",
  allowInteractiveSimulation = true,
}: DualModelConsensusGaugeProps) {
  // If simulation is enabled and no explicit props provided, allow user to test convergence vs divergence
  const [simMode, setSimMode] = useState<"consensus" | "divergent">("consensus");

  // Without both real scores the gauge shows built-in example values, and must say so.
  const isSimulated = propGeminiScore === undefined || propClaudeScore === undefined;

  const sA =
    propGeminiScore !== undefined
      ? propGeminiScore
      : simMode === "consensus"
        ? 0.96
        : 0.92;

  const sB =
    propClaudeScore !== undefined
      ? propClaudeScore
      : simMode === "consensus"
        ? 0.93
        : 0.68;

  const delta = Math.abs(sA - sB);
  const isConsensus = delta <= 0.15;
  const consensusScore = isConsensus ? (sA + sB) / 2 : Math.min(sA, sB) * 0.90;

  // Meter percentage (0.0 to 0.5 range mapped to 0% - 100%)
  const meterPercentage = Math.min(100, Math.round((delta / 0.30) * 100));

  return (
    <div
      className={`rounded-2xl border border-cyan-500/30 bg-[#0B1120]/90 p-5 shadow-[0_0_35px_rgba(0,240,255,0.15)] text-slate-100 backdrop-blur-xl ${className}`}
    >
      {/* Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 pb-4 mb-4 border-b border-cyan-500/20">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl bg-cyan-950/80 border border-cyan-400/40 text-cyan-400 shadow-[0_0_15px_rgba(0,240,255,0.25)]">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
              />
            </svg>
          </div>
          <div>
            <h3 className="text-sm font-bold tracking-tight text-white flex items-center gap-2">
              Dual-Model Arbiter HUD
              <span className="text-[10px] font-mono font-medium px-2 py-0.5 rounded-full bg-cyan-950 border border-cyan-500/40 text-cyan-300">
                Adversarial LLM Consensus
              </span>
              {isSimulated && (
                <span
                  className="text-[10px] font-mono font-bold px-2 py-0.5 rounded-full bg-amber-950 border border-amber-400/50 text-amber-300"
                  title="No model scores were supplied, so these are example values, not a real evaluation."
                >
                  SIMULATED
                </span>
              )}
            </h3>
            <p className="text-[11px] text-slate-400 font-mono">
              Concurrent multi-model arbitration across Google Gemini + Anthropic Claude
            </p>
          </div>
        </div>

        {/* Dynamic Convergence Badge */}
        <div className="flex items-center gap-2">
          {allowInteractiveSimulation && propGeminiScore === undefined && (
            <div className="flex items-center rounded-lg bg-slate-950 p-0.5 border border-slate-800 text-[11px] font-mono">
              <button
                onClick={() => setSimMode("consensus")}
                className={`px-2.5 py-1 rounded-md transition-all ${
                  simMode === "consensus"
                    ? "bg-cyan-500/20 text-[#00F0FF] border border-cyan-500/40 font-bold"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                Consensus
              </button>
              <button
                onClick={() => setSimMode("divergent")}
                className={`px-2.5 py-1 rounded-md transition-all ${
                  simMode === "divergent"
                    ? "bg-rose-500/20 text-rose-300 border border-rose-500/40 font-bold"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                Divergence
              </button>
            </div>
          )}

          <div
            className={`px-3 py-1 rounded-full text-xs font-mono font-bold flex items-center gap-1.5 border transition-all ${
              isConsensus
                ? "bg-emerald-950/80 border-emerald-400/60 text-emerald-300 shadow-[0_0_15px_rgba(52,211,153,0.3)]"
                : "bg-rose-950/80 border-rose-400/60 text-rose-300 shadow-[0_0_15px_rgba(244,63,94,0.3)] animate-pulse"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isConsensus ? "bg-emerald-400 shadow-[0_0_8px_#34d399]" : "bg-rose-400 shadow-[0_0_8px_#f43f5e]"
              }`}
            />
            <span>{isConsensus ? "CONSENSUS_REACHED (Average)" : "DIVERGENCE_FALLBACK (Min Score)"}</span>
          </div>
        </div>
      </div>

      {/* Side-by-Side Model Comparison Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-5">
        {/* Model A: Google Gemini 1.5 Flash */}
        <div className="p-4 rounded-xl border border-cyan-500/25 bg-slate-900/70 backdrop-blur-sm relative overflow-hidden">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="text-base font-bold text-white flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-blue-400 shadow-[0_0_8px_#60a5fa]" />
                Google Gemini 1.5 Flash
              </span>
            </div>
            {geminiLatencyMs !== undefined && (
              <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-blue-950/70 border border-blue-400/40 text-blue-300">
                ⚡ {geminiLatencyMs}ms
              </span>
            )}
          </div>

          <div className="flex items-baseline justify-between mb-3 pb-2 border-b border-slate-800">
            <span className="text-xs text-slate-400 font-medium">Model Grade ($S_A$)</span>
            <span className="text-2xl font-bold font-mono text-cyan-300">
              {sA.toFixed(3)}
            </span>
          </div>

          {/* Rubric Breakdown */}
          <div className="space-y-2 text-xs">
            <div className="text-[10px] uppercase tracking-wider text-slate-400 font-medium">
              Rubric Evaluation Breakdown
            </div>
            <RubricRow label="Correctness & Specs" score={geminiRubric.correctness} color="cyan" />
            <RubricRow label="Edge-Case Handling" score={geminiRubric.edgeCases} color="cyan" />
            <RubricRow label="Docstrings & Clarity" score={geminiRubric.docstrings} color="cyan" />
            <RubricRow label="Type Safety & Schema" score={geminiRubric.typeSafety} color="cyan" />
          </div>
        </div>

        {/* Model B: Claude 3.5 Haiku */}
        <div className="p-4 rounded-xl border border-cyan-500/25 bg-slate-900/70 backdrop-blur-sm relative overflow-hidden">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="text-base font-bold text-white flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-amber-400 shadow-[0_0_8px_#fbbf24]" />
                Claude 3.5 Haiku
              </span>
            </div>
            {claudeLatencyMs !== undefined && (
              <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-amber-950/70 border border-amber-400/40 text-amber-300">
                ⚡ {claudeLatencyMs}ms
              </span>
            )}
          </div>

          <div className="flex items-baseline justify-between mb-3 pb-2 border-b border-slate-800">
            <span className="text-xs text-slate-400 font-medium">Model Grade ($S_B$)</span>
            <span className="text-2xl font-bold font-mono text-amber-300">
              {sB.toFixed(3)}
            </span>
          </div>

          {/* Rubric Breakdown */}
          <div className="space-y-2 text-xs">
            <div className="text-[10px] uppercase tracking-wider text-slate-400 font-medium">
              Rubric Evaluation Breakdown
            </div>
            <RubricRow label="Correctness & Specs" score={claudeRubric.correctness} color="amber" />
            <RubricRow label="Edge-Case Handling" score={claudeRubric.edgeCases} color="amber" />
            <RubricRow label="Docstrings & Clarity" score={claudeRubric.docstrings} color="amber" />
            <RubricRow label="Type Safety & Schema" score={claudeRubric.typeSafety} color="amber" />
          </div>
        </div>
      </div>

      {/* Dynamic Convergence Gauge Meter */}
      <div className="p-4 rounded-xl border border-cyan-500/20 bg-slate-900/60">
        <div className="flex items-center justify-between text-xs mb-2">
          <div className="flex items-center gap-2">
            <span className="text-slate-400 font-medium uppercase tracking-wider">
              Mathematical Convergence Difference ($\Delta$)
            </span>
            <span className="font-mono text-cyan-300 font-bold">
              |S_A - S_B| = {delta.toFixed(3)}
            </span>
          </div>

          <span className="font-mono text-[11px] text-slate-400">
            Threshold: $\Delta \le 0.150$
          </span>
        </div>

        {/* Gauge Track with 0.15 Threshold Marker */}
        <div className="relative w-full h-3 rounded-full bg-slate-950 overflow-hidden border border-slate-800">
          {/* Threshold indicator line at 50% (representing 0.150) */}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-cyan-400/80 z-10"
            style={{ left: "50%" }}
            title="Consensus boundary: Δ = 0.15"
          />

          {/* Difference Bar */}
          <div
            className={`h-full rounded-full transition-all duration-500 ease-out ${
              isConsensus
                ? "bg-[#00F0FF] shadow-[0_0_12px_rgba(0,240,255,0.8)]"
                : "bg-rose-500 shadow-[0_0_12px_rgba(244,63,94,0.8)]"
            }`}
            style={{ width: `${Math.max(4, meterPercentage)}%` }}
          />
        </div>

        {/* Reconciliation Summary Footer */}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs font-mono pt-2 border-t border-slate-800/80">
          <div className="flex items-center gap-2">
            <span className="text-slate-400">Arbiter Output Formula:</span>
            <span className="text-slate-200">
              {isConsensus ? (
                <>
                  S_consensus = (S_A + S_B) / 2 ={" "}
                  <strong className="text-emerald-300 font-bold">{consensusScore.toFixed(3)}</strong>
                </>
              ) : (
                <>
                  S_consensus = min(S_A, S_B) × 0.90 ={" "}
                  <strong className="text-rose-300 font-bold">{consensusScore.toFixed(3)}</strong>
                </>
              )}
            </span>
          </div>

          <span className="text-[11px] text-slate-400">
            {isConsensus
              ? "✓ Convergence reached: equal weighting applied."
              : "⚠ Divergence penalty: conservative minimum score enforced."}
          </span>
        </div>
      </div>
    </div>
  );
}

function RubricRow({
  label,
  score,
  color,
}: {
  label: string;
  score: number;
  color: "cyan" | "amber";
}) {
  const pct = Math.round(score * 100);
  return (
    <div>
      <div className="flex items-center justify-between text-[11px] font-mono mb-1">
        <span className="text-slate-400">{label}</span>
        <span className={color === "cyan" ? "text-cyan-300" : "text-amber-300"}>
          {score.toFixed(2)}
        </span>
      </div>
      <div className="w-full h-1.5 rounded-full bg-slate-950 overflow-hidden">
        <div
          className={`h-full rounded-full ${
            color === "cyan" ? "bg-cyan-400" : "bg-amber-400"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
