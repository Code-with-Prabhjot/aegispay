"use client";

import { microsToUsd } from "@/lib/format";
import type { LedgerResponse } from "@/lib/types";

export default function LedgerPanel({ ledger }: { ledger: LedgerResponse }) {
  const balances = Object.entries(ledger.balances);
  const entries = [...ledger.entries].reverse();
  return (
    <div className="space-y-4">
      <div>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Account balances</h3>
        {balances.length === 0 ? (
          <p className="text-sm text-slate-500">No balances yet.</p>
        ) : (
          <ul className="grid grid-cols-2 gap-2 md:grid-cols-5">
            {balances.map(([acct, micros]) => (
              <li key={acct} className="rounded border border-slate-700 bg-slate-900 p-2">
                <div className="truncate font-mono text-[11px] text-slate-400">{acct}</div>
                <div className={`text-sm font-semibold ${micros < 0 ? "text-rose-300" : "text-emerald-300"}`}>
                  {micros < 0 ? "-" : ""}${microsToUsd(Math.abs(micros))}
                </div>
                <div className="font-mono text-[10px] text-slate-500">{micros.toLocaleString("en-US")} micros</div>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="max-h-64 overflow-auto rounded border border-slate-800">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-slate-900 text-slate-400">
            <tr>
              <th className="p-2">Time</th>
              <th className="p-2">Debit</th>
              <th className="p-2">Credit</th>
              <th className="p-2 text-right">Amount</th>
              <th className="p-2">Ref</th>
              <th className="p-2">Memo</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.entry_id} className="border-t border-slate-800">
                <td className="p-2 font-mono text-slate-500">{new Date(e.created_at).toLocaleTimeString()}</td>
                <td className="p-2 font-mono text-sky-300">{e.debit_account}</td>
                <td className="p-2 font-mono text-amber-300">{e.credit_account}</td>
                <td className="p-2 text-right font-mono">${microsToUsd(e.amount_micros)}</td>
                <td className="p-2 font-mono text-slate-400">{e.reference_id}</td>
                <td className="p-2 text-slate-400">{e.description}</td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr>
                <td colSpan={6} className="p-3 text-slate-500">
                  No ledger entries yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
