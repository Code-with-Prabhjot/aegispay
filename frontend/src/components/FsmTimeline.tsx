"use client";

import { FSM_STATES, TERMINAL_STATES, type SlaContractResponse } from "@/lib/types";
import { stateColor } from "@/lib/format";

const LABELS: Partial<Record<string, string>> = {
  AGREEMENT_DRAFTED: "1. Agreement drafted",
  PAYPAL_AUTH_LOCKED: "2. PayPal auth locked",
  AUTH_EXPIRED: "3A. Auth expired (>72h)",
  SANDBOX_EXECUTING: "3B. Sandbox executing",
  ARBITRATION_AUDITING: "4. Arbitration auditing",
  FULL_CAPTURE_PENDING: "5A. Full capture pending",
  PARTIAL_CAPTURE_PENDING: "5B. Partial capture pending",
  VOID_PENDING: "5C. Void pending",
  PAYPAL_CAPTURED: "6A. PayPal captured",
  PARTIAL_CAPTURED: "6B. Partial captured",
  ESCROW_VOIDED_REFUNDED: "6C. Escrow voided / refunded",
  CAPTURE_DECLINED: "6D. Capture declined",
  PAYOUT_DISPATCHED: "7. Payout dispatched",
  SETTLED_COMPLETE: "8A. Settled complete",
  PAYOUT_FAILED_REVERSED: "8B. Payout failed / reversed",
};

export default function FsmTimeline({ contract }: { contract: SlaContractResponse | null }) {
  if (!contract) {
    return <p className="text-sm text-slate-400">Select a contract in the ledger to see its FSM path.</p>;
  }
  const history = contract.history ?? [];
  const visited = new Map<string, string>();
  history.forEach((h) => visited.set(h.to_state, h.timestamp));
  if (!visited.has(contract.fsm_state)) visited.set(contract.fsm_state, contract.updated_at);

  return (
    <div>
      <p className="mb-3 truncate font-mono text-xs text-slate-400">{contract.agreement_id}</p>
      <ol className="relative ml-2 border-l border-slate-700">
        {FSM_STATES.map((state) => {
          const when = visited.get(state);
          const isCurrent = contract.fsm_state === state;
          const isTerminal = isCurrent && TERMINAL_STATES.includes(state);
          return (
            <li
              key={state}
              data-testid={`fsm-${state}`}
              className={`mb-2 ml-4 flex items-center justify-between gap-2 ${when ? "" : "opacity-30"}`}
            >
              <span
                className={`absolute -left-[7px] h-3 w-3 rounded-full border ${
                  when ? "border-emerald-400 bg-emerald-500" : "border-slate-600 bg-slate-800"
                } ${isCurrent && !isTerminal ? "animate-pulse ring-4 ring-sky-500/40" : ""} ${
                  isTerminal ? "ring-4 ring-emerald-400/40" : ""
                }`}
              />
              <span
                className={`rounded border px-2 py-0.5 text-xs ${when ? stateColor(state) : "border-slate-700 text-slate-500"} ${
                  isCurrent ? "font-semibold" : ""
                }`}
              >
                {LABELS[state] ?? state}
                {isTerminal ? "  (terminal)" : ""}
              </span>
              <span className="font-mono text-[10px] text-slate-500">
                {when ? new Date(when).toLocaleTimeString() : ""}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
