export function microsToUsd(micros: number): string {
  return (micros / 1_000_000).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** "$15.00 USD / 15,000,000 micros" */
export function formatBudget(micros: number | null | undefined): string {
  if (micros == null) return "";
  return `$${microsToUsd(micros)} USD / ${micros.toLocaleString("en-US")} micros`;
}

export function stateColor(state: string): string {
  switch (state) {
    case "SETTLED_COMPLETE":
    case "PAYPAL_CAPTURED":
      return "bg-emerald-500/20 text-emerald-300 border-emerald-500/40";
    case "PARTIAL_CAPTURED":
    case "PARTIAL_CAPTURE_PENDING":
      return "bg-amber-500/20 text-amber-300 border-amber-500/40";
    case "ESCROW_VOIDED_REFUNDED":
    case "VOID_PENDING":
    case "AUTH_EXPIRED":
    case "CAPTURE_DECLINED":
    case "PAYOUT_FAILED_REVERSED":
      return "bg-rose-500/20 text-rose-300 border-rose-500/40";
    case "FULL_CAPTURE_PENDING":
    case "PAYOUT_DISPATCHED":
      return "bg-sky-500/20 text-sky-300 border-sky-500/40";
    case "SANDBOX_EXECUTING":
    case "ARBITRATION_AUDITING":
      return "bg-violet-500/20 text-violet-300 border-violet-500/40";
    default:
      return "bg-slate-500/20 text-slate-300 border-slate-500/40";
  }
}
