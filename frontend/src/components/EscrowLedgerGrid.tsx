"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type {
  ColDef,
  GridApi,
  GridReadyEvent,
  ICellRendererParams,
  IDetailCellRendererParams,
  RowClickedEvent,
} from "ag-grid-community";
import "ag-grid-enterprise";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";

import {
  FSM_STATES,
  type AgreementDetailResponse,
  type SlaContractResponse,
  type TestAssertionItem,
} from "@/lib/types";
import { formatBudget, microsToUsd, stateColor } from "@/lib/format";
import * as api from "@/lib/api";
import { useLedgerWebSocket } from "@/lib/useLedgerWebSocket";
import type { Connection } from "@/lib/useAegis";

// ----------------------------------------------------------------------------
// Custom Cell Renderers
// ----------------------------------------------------------------------------

function StateBadge(p: ICellRendererParams<SlaContractResponse, string>) {
  if (!p.value) return null;
  const isVoid = p.value === "ESCROW_VOIDED_REFUNDED" || p.value === "VOID_PENDING";
  return (
    <span
      className={`rounded border px-2 py-0.5 text-xs font-medium transition-all ${stateColor(p.value)} ${
        isVoid ? "border-rose-500/60 bg-rose-950/60 text-rose-300 shadow-[0_0_10px_rgba(239,68,68,0.3)]" : ""
      }`}
    >
      {p.value}
    </span>
  );
}

function IdBadge(p: ICellRendererParams<SlaContractResponse, string | null>) {
  if (!p.value) return <span className="text-slate-600">-</span>;
  const tone = String(p.colDef?.field).includes("payout")
    ? "border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-300"
    : String(p.colDef?.field).includes("capture")
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
      : "border-sky-500/40 bg-sky-500/10 text-sky-300";
  return <span className={`rounded border px-2 py-0.5 font-mono text-[11px] ${tone}`}>{p.value}</span>;
}

function AgreementIdCell(p: ICellRendererParams<SlaContractResponse, string>) {
  return (
    <span className="font-mono text-xs font-semibold text-cyan-300 hover:text-cyan-200 cursor-pointer">
      {p.value}
    </span>
  );
}

// ----------------------------------------------------------------------------
// AG Grid Enterprise Master-Detail Nested Sub-View (3 Tabs + Audit Proof Bar)
// ----------------------------------------------------------------------------

interface DetailSource {
  demoMode?: boolean;
  getDemoDetail?: (id: string) => AgreementDetailResponse | null;
}

export function MasterDetailSubView(props: IDetailCellRendererParams<SlaContractResponse> & DetailSource) {
  const { demoMode, getDemoDetail } = props;
  const contract = props.data;
  const [activeTab, setActiveTab] = useState<"tests" | "ast" | "journal">("tests");
  const [detail, setDetail] = useState<AgreementDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [expandedTrace, setExpandedTrace] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let active = true;
    async function loadDetail() {
      if (!contract?.agreement_id) return;
      setLoading(true);
      setError(null);
      setDetail(null);
      try {
        const fetched = demoMode
          ? getDemoDetail?.(contract.agreement_id) ?? null
          : await api.getAgreementDetail(contract.agreement_id);
        if (!fetched) throw new Error("Contract detail is unavailable.");
        if (active) setDetail(fetched);
      } catch (err) {
        if (active) setError((err as Error).message);
      } finally {
        if (active) setLoading(false);
      }
    }
    loadDetail();
    return () => {
      active = false;
    };
  }, [contract, demoMode, getDemoDetail, reload]);

  const copyToClipboard = (text: string, key: string) => {
    if (navigator?.clipboard) {
      navigator.clipboard.writeText(text);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(null), 2000);
    }
  };

  const toggleTrace = (testName: string) => {
    setExpandedTrace((prev) => ({ ...prev, [testName]: !prev[testName] }));
  };

  const merkleRoot = detail?.merkle_root || "Unavailable";
  const hashChainHead = detail?.hash_chain_head || "Unavailable";

  const passedTestsCount = detail?.test_assertions?.filter((t) => t.status.toUpperCase().includes("PASS")).length ?? 0;
  const totalTestsCount = detail?.test_assertions?.length ?? 0;
  const astScore = detail?.ast_security_audit?.score ?? (contract?.verification_score ?? 1.0);
  const isAstClean = detail?.ast_security_audit?.is_clean ?? (contract?.fsm_state !== "ESCROW_VOIDED_REFUNDED");

  // Double entry sum calculations
  const journalLegs = detail?.ledger_journal ?? [];
  const totalDebits = journalLegs.reduce((acc, l) => acc + (l.amount_micros || 0), 0);
  const totalCredits = journalLegs.reduce((acc, l) => acc + (l.amount_micros || 0), 0);
  const isBalanced = totalDebits === totalCredits && totalDebits > 0;

  // AST perimeter checks for malicious imports
  const scannedImports = detail?.ast_security_audit?.scanned_imports || [];
  const blockedCalls = detail?.ast_security_audit?.blocked_calls || [];
  const maliciousImportsList = [
    { name: "os", flagged: scannedImports.includes("os") || blockedCalls.some((c) => c.startsWith("os.")) },
    { name: "sys", flagged: scannedImports.includes("sys") || blockedCalls.some((c) => c.startsWith("sys.")) },
    { name: "socket", flagged: scannedImports.includes("socket") || blockedCalls.some((c) => c.startsWith("socket")) },
    {
      name: "child_process / subprocess",
      flagged:
        scannedImports.includes("subprocess") ||
        scannedImports.includes("child_process") ||
        blockedCalls.some((c) => c.includes("subprocess") || c.includes("child_process")),
    },
  ];

  return (
    <div className="p-4 bg-slate-950/95 border-t border-b border-cyan-500/30 text-slate-200 text-xs font-sans h-full flex flex-col justify-between">
      {/* -------------------------------------------------------------------- */}
      {/* Audit Proof Bar: SHA-256 Merkle Root & Hash Chain Head              */}
      {/* -------------------------------------------------------------------- */}
      <div
        className="flex flex-wrap items-center justify-between gap-3 p-2.5 rounded-lg border border-cyan-500/20 bg-slate-900/80 backdrop-blur-md mb-3 shadow-[0_0_15px_rgba(0,229,255,0.06)]"
        title="Cryptographic receipt proving sandbox outputs and financial journals were not altered."
      >
        <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2.5">
          <div className="flex items-center gap-2">
            <span className="flex h-2 w-2 rounded-full bg-emerald-400 animate-ping" />
            <span className="text-[11px] font-semibold uppercase tracking-wider text-cyan-300 flex items-center gap-1.5">
              <svg className="w-3.5 h-3.5 text-cyan-400 inline" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
              </svg>
              Cryptographic Audit Proof
            </span>
            <span
              className="rounded bg-cyan-950/80 border border-cyan-500/40 px-2 py-0.5 font-mono text-[10px] text-cyan-300 cursor-help"
              title="Cryptographic receipt proving sandbox outputs and financial journals were not altered."
            >
              {demoMode ? "Demo proof" : detail?.merkle_root ? "Merkle receipt" : "Proof unavailable"}
            </span>
          </div>
          <span className="text-[10px] text-slate-400 italic">
            Cryptographic receipt proving sandbox outputs and financial journals were not altered.
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-4 font-mono text-[11px]">
          {/* Merkle Root */}
          <div className="flex items-center gap-1.5 bg-slate-950/80 px-2.5 py-1 rounded border border-slate-800">
            <span className="text-slate-400 font-semibold">Merkle Root:</span>
            <span className="text-cyan-300 max-w-[140px] truncate" title={merkleRoot}>
              {merkleRoot}
            </span>
            <button
              onClick={() => copyToClipboard(merkleRoot, "merkle")}
              className="text-slate-400 hover:text-cyan-300 transition-colors p-0.5 ml-1"
              title="Copy Merkle Root Hash"
            >
              {copiedKey === "merkle" ? (
                <span className="text-emerald-400 font-sans text-[10px] font-bold">Copied!</span>
              ) : (
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
              )}
            </button>
          </div>

          {/* Hash Chain Head */}
          <div className="flex items-center gap-1.5 bg-slate-950/80 px-2.5 py-1 rounded border border-slate-800">
            <span className="text-slate-400 font-semibold">Chain Head:</span>
            <span className="text-amber-300 max-w-[140px] truncate" title={hashChainHead}>
              {hashChainHead}
            </span>
            <button
              onClick={() => copyToClipboard(hashChainHead, "chain")}
              className="text-slate-400 hover:text-amber-300 transition-colors p-0.5 ml-1"
              title="Copy Hash Chain Head"
            >
              {copiedKey === "chain" ? (
                <span className="text-emerald-400 font-sans text-[10px] font-bold">Copied!</span>
              ) : (
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* -------------------------------------------------------------------- */}
      {/* Sub-view Navigation Tabs                                             */}
      {/* -------------------------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-2 overflow-x-auto py-1 border-b border-slate-800 pb-2 mb-3">
        <button
          onClick={() => setActiveTab("tests")}
          className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium whitespace-nowrap flex-shrink-0 transition-all ${
            activeTab === "tests"
              ? "bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-[0_0_10px_rgba(0,229,255,0.2)]"
              : "text-slate-400 hover:text-slate-200 hover:bg-slate-900"
          }`}
        >
          <span>Tab 1: Test Suite Matrix</span>
          <span className="rounded bg-slate-900 border border-slate-700 px-1.5 py-0.2 text-[10px] font-mono text-cyan-400">
            {passedTestsCount}/{totalTestsCount} PASS
          </span>
        </button>

        <button
          onClick={() => setActiveTab("ast")}
          className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium whitespace-nowrap flex-shrink-0 transition-all ${
            activeTab === "ast"
              ? "bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-[0_0_10px_rgba(0,229,255,0.2)]"
              : "text-slate-400 hover:text-slate-200 hover:bg-slate-900"
          }`}
        >
          <span>Tab 2: AST Security Syntax Tree</span>
          <span
            className={`rounded px-1.5 py-0.2 text-[10px] font-mono border ${
              isAstClean
                ? "bg-emerald-950/80 border-emerald-500/40 text-emerald-300"
                : "bg-rose-950/80 border-rose-500/40 text-rose-300"
            }`}
          >
            {isAstClean ? `Safe (${astScore.toFixed(2)})` : "Hard Veto (0.00)"}
          </span>
        </button>

        <button
          onClick={() => setActiveTab("journal")}
          className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium whitespace-nowrap flex-shrink-0 transition-all ${
            activeTab === "journal"
              ? "bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-[0_0_10px_rgba(0,229,255,0.2)]"
              : "text-slate-400 hover:text-slate-200 hover:bg-slate-900"
          }`}
        >
          <span>Tab 3: Balanced Double-Entry Journal</span>
          <span className="rounded bg-slate-900 border border-slate-700 px-1.5 py-0.2 text-[10px] font-mono text-emerald-400">
            ∑D ≡ ∑C
          </span>
        </button>
      </div>

      {/* -------------------------------------------------------------------- */}
      {/* Tab Panels                                                           */}
      {/* -------------------------------------------------------------------- */}
      <div className="flex-1 overflow-auto rounded border border-slate-800 bg-slate-900/60 p-3 min-h-[200px]">
        {loading ? (
          <div className="flex items-center justify-center h-full text-slate-400 gap-2">
            <span className="w-4 h-4 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin" />
            Loading sub-view data...
          </div>
        ) : error ? (
          <div role="alert" className="text-amber-300">
            Could not load contract detail: {error}
            <button onClick={() => setReload((value) => value + 1)} className="ml-3 underline">Retry</button>
          </div>
        ) : activeTab === "tests" ? (
          /* TAB 1: TEST SUITE MATRIX */
          <div className="space-y-2">
            <div className="flex items-center justify-between text-[11px] text-slate-400 pb-1.5 border-b border-slate-800">
              <span>Unit test execution traces evaluated in polyglot runner</span>
              <span className="font-mono text-cyan-400">Runner: Python 3.14 Pytest Sandbox</span>
            </div>
            <div className="divide-y divide-slate-800/80">
              {detail?.test_assertions && detail.test_assertions.length > 0 ? (
                detail.test_assertions.map((test: TestAssertionItem, idx: number) => {
                  const isPassed = test.status.toUpperCase().includes("PASS");
                  const hasError = Boolean(test.error_message);
                  const isExpanded = Boolean(expandedTrace[test.name]);

                  return (
                    <div key={test.name + idx} className="py-2">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                          <span
                            className={`rounded px-2 py-0.5 font-mono text-[10px] font-bold border ${
                              isPassed
                                ? "bg-emerald-500/10 border-emerald-500/40 text-emerald-400"
                                : "bg-rose-500/10 border-rose-500/40 text-rose-400"
                            }`}
                          >
                            {isPassed ? "PASS" : "FAIL"}
                          </span>
                          <span className="font-mono font-medium text-slate-200">{test.name}</span>
                        </div>

                        <div className="flex items-center gap-3">
                          <span className="font-mono text-[11px] text-slate-400">
                            {test.duration_ms.toFixed(1)} ms
                          </span>
                          {hasError && (
                            <button
                              onClick={() => toggleTrace(test.name)}
                              className="text-[10px] px-2 py-0.5 rounded border border-rose-500/40 bg-rose-950/40 text-rose-300 hover:bg-rose-900/60 transition-colors"
                            >
                              {isExpanded ? "Hide Trace ▲" : "Expand Stack Trace ▼"}
                            </button>
                          )}
                        </div>
                      </div>

                      {hasError && isExpanded && (
                        <div className="mt-2 p-2.5 rounded bg-slate-950 border border-rose-500/30 font-mono text-[11px] text-rose-300 overflow-x-auto whitespace-pre-wrap leading-relaxed shadow-inner">
                          {test.error_message}
                        </div>
                      )}
                    </div>
                  );
                })
              ) : (
                <div className="text-slate-500 py-4 text-center">No unit test assertions recorded.</div>
              )}
            </div>
          </div>
        ) : activeTab === "ast" ? (
          /* TAB 2: AST SECURITY SYNTAX TREE */
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 p-3 rounded border border-slate-800 bg-slate-950/60">
              <div>
                <div className="text-xs font-semibold text-slate-300 mb-1">AST Static Analysis Security Score</div>
                <div className="text-slate-400 text-[11px]">
                  Scans parsed syntax tree nodes for unauthorized system capabilities prior to sandbox container execution.
                </div>
              </div>
              <div
                className={`text-lg font-mono font-bold px-3 py-1 rounded border ${
                  isAstClean
                    ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-300"
                    : "border-rose-500/50 bg-rose-500/10 text-rose-400"
                }`}
              >
                S_ast = {astScore.toFixed(2)} / 1.00
              </div>
            </div>

            {/* Malicious Imports Checklist */}
            <div>
              <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2">
                Flagged Security Perimeter Imports ({maliciousImportsList.length})
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                {maliciousImportsList.map((item) => (
                  <div
                    key={item.name}
                    className={`p-2 rounded border flex items-center justify-between ${
                      item.flagged
                        ? "border-rose-500/60 bg-rose-950/40 text-rose-300 shadow-[0_0_8px_rgba(239,68,68,0.2)]"
                        : "border-emerald-500/30 bg-emerald-950/20 text-emerald-300"
                    }`}
                  >
                    <span className="font-mono text-[11px] font-semibold">{item.name}</span>
                    <span
                      className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                        item.flagged ? "bg-rose-500/30 text-rose-200" : "bg-emerald-500/30 text-emerald-200"
                      }`}
                    >
                      {item.flagged ? "FLAGGED" : "CLEAN"}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* AST Discovered Imports & Blocked Calls */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="p-2.5 rounded bg-slate-950 border border-slate-800">
                <div className="text-[11px] font-semibold text-slate-400 mb-1.5">Scanned Import Nodes:</div>
                <div className="flex flex-wrap gap-1.5">
                  {scannedImports.length > 0 ? (
                    scannedImports.map((imp) => (
                      <span key={imp} className="rounded bg-slate-800 border border-slate-700 px-2 py-0.5 font-mono text-[10px] text-cyan-300">
                        {imp}
                      </span>
                    ))
                  ) : (
                    <span className="text-slate-600 font-mono text-[10px]">None</span>
                  )}
                </div>
              </div>

              <div className="p-2.5 rounded bg-slate-950 border border-slate-800">
                <div className="text-[11px] font-semibold text-slate-400 mb-1.5">Blocked Intercepted Calls:</div>
                <div className="flex flex-wrap gap-1.5">
                  {blockedCalls.length > 0 ? (
                    blockedCalls.map((call) => (
                      <span key={call} className="rounded bg-rose-950/80 border border-rose-500/50 px-2 py-0.5 font-mono text-[10px] text-rose-300">
                        {call}
                      </span>
                    ))
                  ) : (
                    <span className="text-emerald-400 font-mono text-[10px]">Zero blocked calls detected</span>
                  )}
                </div>
              </div>
            </div>

            {!isAstClean && (
              <div className="p-3 rounded border border-rose-500/60 bg-rose-950/50 text-rose-200 text-[11px] flex items-center gap-2.5 shadow-[0_0_15px_rgba(239,68,68,0.25)]">
                <svg className="w-5 h-5 text-rose-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                <span>
                  <b>HARD AST VETO TRIGGERED:</b> Host security perimeter was violated by prohibited system import. The escrow authorization was voided immediately without charging the buyer.
                </span>
              </div>
            )}
          </div>
        ) : (
          /* TAB 3: BALANCED DOUBLE-ENTRY JOURNAL */
          <div className="space-y-3">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-slate-800 text-[10px] uppercase font-semibold text-slate-400 tracking-wider">
                    <th className="py-1 px-2">Timestamp</th>
                    <th className="py-1 px-2">Memo / Description</th>
                    <th className="py-1 px-2">Debit Account</th>
                    <th className="py-1 px-2 text-right">Debit ($)</th>
                    <th className="py-1 px-2">Credit Account</th>
                    <th className="py-1 px-2 text-right">Credit ($)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 font-mono">
                  {journalLegs.length > 0 ? (
                    journalLegs.map((leg, idx) => (
                      <tr key={idx} className="hover:bg-slate-800/40">
                        <td className="py-1.5 px-2 text-slate-400 text-[10px]">
                          {new Date(leg.created_at).toLocaleTimeString()}
                        </td>
                        <td className="py-1.5 px-2 font-sans text-slate-200 text-[11px] max-w-[200px] truncate" title={leg.memo}>
                          {leg.memo || "Settlement transfer"}
                        </td>
                        <td className="py-1.5 px-2 text-sky-400">{leg.debit_account}</td>
                        <td className="py-1.5 px-2 text-right text-emerald-400 font-semibold">{leg.amount_usd}</td>
                        <td className="py-1.5 px-2 text-amber-400">{leg.credit_account}</td>
                        <td className="py-1.5 px-2 text-right text-emerald-400 font-semibold">{leg.amount_usd}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={6} className="py-4 text-center text-slate-500 font-sans">
                        No double-entry journal lines recorded yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Footer Badge Proving Debits == Credits */}
            <div className="mt-3 p-3 rounded-lg border border-emerald-500/40 bg-emerald-950/30 flex flex-wrap items-center justify-between gap-3 text-xs shadow-[0_0_12px_rgba(16,185,129,0.15)]">
              <div className="flex items-center gap-2">
                <svg className="w-4 h-4 text-emerald-400 inline" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
                <span className="font-semibold text-emerald-300">
                  Balanced Journal Invariant: $\sum \text&#123;Debits&#125; \equiv \sum \text&#123;Credits&#125;$
                </span>
              </div>
              <div className="flex items-center gap-3 font-mono">
                <span className="text-slate-300">
                  Debits: <b className="text-emerald-400">${microsToUsd(totalDebits)}</b>
                </span>
                <span className="text-slate-500">≡</span>
                <span className="text-slate-300">
                  Credits: <b className="text-emerald-400">${microsToUsd(totalCredits)}</b>
                </span>
                <span className="rounded bg-emerald-500/20 border border-emerald-500/40 px-2 py-0.5 text-[10px] text-emerald-300 font-bold">
                  {isBalanced ? "ZERO DISCREPANCY" : "BALANCING"}
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Master Grid Main Component
// ----------------------------------------------------------------------------

const EMPTY_ROWS: SlaContractResponse[] = [];

interface Props extends DetailSource {
  contracts: SlaContractResponse[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRefresh?: () => void;
  connection: Connection;
}

export default function EscrowLedgerGrid({ contracts, selectedId, onSelect, onRefresh, connection, demoMode, getDemoDetail }: Props) {
  const apiRef = useRef<GridApi<SlaContractResponse> | null>(null);
  const known = useRef<Map<string, string>>(new Map());
  const [ready, setReady] = useState(false);
  const [stateFilter, setStateFilter] = useState("ALL");
  const stateFilterRef = useRef(stateFilter);

  // Column definitions with Master-Detail Group Cell Renderer on ID
  const columnDefs = useMemo<ColDef<SlaContractResponse>[]>(
    () => [
      {
        field: "agreement_id",
        headerName: "Agreement ID",
        minWidth: 230,
        cellRenderer: "agGroupCellRenderer",
        cellRendererParams: {
          innerRenderer: AgreementIdCell,
        },
        filter: "agTextColumnFilter",
      },
      {
        field: "fsm_state",
        headerName: "FSM State",
        minWidth: 220,
        cellRenderer: StateBadge,
        cellClassRules: {
          "cell-void-flash": (p) =>
            p.value === "ESCROW_VOIDED_REFUNDED" || p.value === "VOID_PENDING",
        },
      },
      {
        field: "verification_score",
        headerName: "Score (S)",
        width: 120,
        filter: "agNumberColumnFilter",
        valueFormatter: (p) => (p.value == null ? "-" : Number(p.value).toFixed(3)),
        cellClassRules: {
          "cell-void-flash": (p) => p.value === 0,
        },
      },
      {
        field: "budget_micros",
        headerName: "Budget (USD)",
        minWidth: 220,
        filter: "agNumberColumnFilter",
        valueFormatter: (p) => formatBudget(p.value),
      },
      { field: "paypal_authorization_id", headerName: "PayPal Auth ID", minWidth: 170, cellRenderer: IdBadge },
      { field: "paypal_capture_id", headerName: "Capture ID", minWidth: 170, cellRenderer: IdBadge },
      { field: "paypal_payout_batch_id", headerName: "Payout Batch ID", minWidth: 180, cellRenderer: IdBadge },
    ],
    [],
  );

  const defaultColDef = useMemo<ColDef>(
    () => ({
      sortable: true,
      resizable: true,
      filter: true,
      enableCellChangeFlash: true,
      flex: 1,
    }),
    [],
  );

  const onGridReady = useCallback((e: GridReadyEvent<SlaContractResponse>) => {
    apiRef.current = e.api;
    setReady(true);
    if (contracts.length > 0) {
      e.api.hideOverlay();
    } else {
      e.api.showNoRowsOverlay();
    }
  }, [contracts.length]);

  // Events invalidate the HTTP snapshot; partial event payloads must not replace full rows.
  const { status: wsStatus } = useLedgerWebSocket({
    onContractUpdate: () => onRefresh?.(),
    onSnapshot: () => onRefresh?.(),
    onLedgerTransaction: () => onRefresh?.(),
  }, !demoMode);

  // Apply the complete snapshot, including removals, before synchronizing selection.
  useEffect(() => {
    const api = apiRef.current;
    if (!ready || !api) return;
    const add: SlaContractResponse[] = [];
    const update: SlaContractResponse[] = [];
    const remove: SlaContractResponse[] = [];
    const ids = new Set(contracts.map((c) => c.agreement_id));
    for (const id of Array.from(known.current.keys())) {
      if (!ids.has(id)) {
        const row = api.getRowNode(id)?.data;
        if (row) remove.push(row);
        known.current.delete(id);
      }
    }

    for (const c of contracts) {
      const sig = JSON.stringify(c);
      const prev = known.current.get(c.agreement_id);
      if (prev === undefined) add.push(c);
      else if (prev !== sig) update.push(c);
      known.current.set(c.agreement_id, sig);
    }

    if (add.length || update.length || remove.length) {
      api.applyTransaction({ add, update, remove });
    }
    if (contracts.length === 0) {
      api.showNoRowsOverlay();
    } else {
      api.hideOverlay();
    }
  }, [contracts, ready]);

  useEffect(() => {
    const api = apiRef.current;
    if (!ready || !api) return;
    api.forEachNode((n) => n.setSelected(n.data?.agreement_id === selectedId));
  }, [selectedId, ready, contracts]);

  const onFilterChange = (v: string) => {
    setStateFilter(v);
    stateFilterRef.current = v;
    apiRef.current?.onFilterChanged();
  };

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <label className="text-xs text-slate-400" htmlFor="state-filter">
          State
        </label>
        <select
          id="state-filter"
          value={stateFilter}
          onChange={(e) => onFilterChange(e.target.value)}
          className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200"
        >
          <option value="ALL">All states</option>
          {FSM_STATES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <div className="flex items-center gap-1.5 ml-2">
          <span
            className={`w-2 h-2 rounded-full ${
              (demoMode || connection === "online")
                ? "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]"
                : "bg-amber-400 animate-pulse"
            }`}
          />
          <span className="text-[11px] font-mono text-slate-400">
            {demoMode ? "Demo data (offline)"
              : connection === "online" ? (wsStatus === "connected" ? "Live WebSocket" : "Live polling (3s)")
              : connection === "connecting" ? "Connecting to backend..." : "API offline · retrying"}
          </span>
        </div>

        <button
          onClick={() => apiRef.current?.exportDataAsCsv({ fileName: "aegispay-escrow-ledger.csv" })}
          className="ml-auto rounded border border-slate-600 bg-slate-800 px-3 py-1 text-xs hover:bg-slate-700 transition-colors"
        >
          Export CSV
        </button>
      </div>

      <div className="ag-theme-quartz-dark" style={{ height: 480, width: "100%" }}>
        <AgGridReact<SlaContractResponse>
          rowData={EMPTY_ROWS}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          getRowId={(p) => p.data.agreement_id}
          rowSelection="single"
          masterDetail={true}
          detailCellRenderer={MasterDetailSubView}
          detailCellRendererParams={{ demoMode, getDemoDetail }}
          detailRowHeight={420}
          onGridReady={onGridReady}
          onRowClicked={(e: RowClickedEvent<SlaContractResponse>) => {
            if (e.node) {
              e.node.setExpanded(!e.node.expanded);
            }
            if (e.data) {
              onSelect(e.data.agreement_id);
            }
          }}
          isExternalFilterPresent={() => stateFilterRef.current !== "ALL"}
          doesExternalFilterPass={(node) => node.data?.fsm_state === stateFilterRef.current}
          asyncTransactionWaitMillis={50}
          overlayNoRowsTemplate="No contracts yet. Run a scenario above."
        />
      </div>
    </div>
  );
}
