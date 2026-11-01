// Deterministic demo-data mode: used when the backend is unreachable.
// No randomness or wall-clock reads: IDs and timestamps derive from a counter.
import type {
  DoubleEntryLedgerEntry,
  FsmState,
  FsmTransition,
  LedgerResponse,
  ScenarioName,
  SlaContractResponse,
} from "./types";

interface ScenarioPlan {
  score: number;
  path: FsmState[];
  captureMicros: number;
}

const BUDGET = 15_000_000;
const RAIL_FEE = 250_000;
const FEE_BPS = 200; // 2% protocol fee

const PLANS: Record<ScenarioName, ScenarioPlan> = {
  CLEAN_PASS: {
    score: 0.96,
    captureMicros: 15_000_000,
    path: [
      "AGREEMENT_DRAFTED",
      "PAYPAL_AUTH_LOCKED",
      "SANDBOX_EXECUTING",
      "ARBITRATION_AUDITING",
      "FULL_CAPTURE_PENDING",
      "PAYPAL_CAPTURED",
      "PAYOUT_DISPATCHED",
      "SETTLED_COMPLETE",
    ],
  },
  PARTIAL_DEFECT: {
    score: 0.75,
    captureMicros: 10_500_000,
    path: [
      "AGREEMENT_DRAFTED",
      "PAYPAL_AUTH_LOCKED",
      "SANDBOX_EXECUTING",
      "ARBITRATION_AUDITING",
      "PARTIAL_CAPTURE_PENDING",
      "PARTIAL_CAPTURED",
      "PAYOUT_DISPATCHED",
      "SETTLED_COMPLETE",
    ],
  },
  MALICIOUS_INJECTION: {
    score: 0.22,
    captureMicros: 0,
    path: [
      "AGREEMENT_DRAFTED",
      "PAYPAL_AUTH_LOCKED",
      "SANDBOX_EXECUTING",
      "ARBITRATION_AUDITING",
      "VOID_PENDING",
      "ESCROW_VOIDED_REFUNDED",
    ],
  },
};

const BASE_TIME = Date.UTC(2026, 10, 1, 12, 0, 0);

interface DemoContract {
  scenario: ScenarioName;
  seq: number;
  step: number; // index into path
  history: FsmTransition[];
}

const ts = (n: number) => new Date(BASE_TIME + n * 5000).toISOString();
const pad = (n: number) => String(n).padStart(4, "0");

export class DemoStore {
  private contracts: DemoContract[] = [];
  private clock = 0;

  /** Seed one finished example per scenario so the grid is never empty in demo mode. */
  constructor(seed = true) {
    if (seed) {
      (["CLEAN_PASS", "PARTIAL_DEFECT", "MALICIOUS_INJECTION"] as ScenarioName[]).forEach((s) => {
        this.run(s);
        const c = this.contracts[this.contracts.length - 1];
        while (c.step < PLANS[c.scenario].path.length - 1) this.advance(c);
      });
    }
  }

  run(name: ScenarioName): SlaContractResponse {
    const seq = this.contracts.length + 1;
    const c: DemoContract = { scenario: name, seq, step: 0, history: [] };
    c.history.push({ from_state: null, to_state: "AGREEMENT_DRAFTED", timestamp: ts(this.clock++) });
    this.contracts.push(c);
    return this.toResponse(c);
  }

  private advance(c: DemoContract) {
    const path = PLANS[c.scenario].path;
    if (c.step >= path.length - 1) return;
    c.history.push({ from_state: path[c.step], to_state: path[c.step + 1], timestamp: ts(this.clock++) });
    c.step += 1;
  }

  /** Advance every non-terminal contract by one FSM step. Returns true if anything changed. */
  tick(): boolean {
    let changed = false;
    for (const c of this.contracts) {
      if (c.step < PLANS[c.scenario].path.length - 1) {
        this.advance(c);
        changed = true;
      }
    }
    return changed;
  }

  list(): SlaContractResponse[] {
    return this.contracts.map((c) => this.toResponse(c));
  }

  get(id: string): SlaContractResponse | undefined {
    return this.list().find((c) => c.agreement_id === id);
  }

  private toResponse(c: DemoContract): SlaContractResponse {
    const plan = PLANS[c.scenario];
    const idx = (state: FsmState) => plan.path.indexOf(state);
    const reached = (state: FsmState) => idx(state) !== -1 && idx(state) <= c.step;
    const n = pad(c.seq);
    const created = c.history[0].timestamp;
    const updated = c.history[c.history.length - 1].timestamp;
    const scored = reached("ARBITRATION_AUDITING");
    return {
      agreement_id: `demo-agr-${n}`,
      buyer_agent_id: "buyer-agent-alpha",
      worker_agent_id: "worker-agent-beta",
      worker_paypal_email: "worker@example.com",
      budget_micros: BUDGET,
      budget_usd: "15.00",
      task_description: "Implement a token-bucket rate limiter (FastAPI/Python) passing the buyer's 10-test suite.",
      timeout_seconds: 3600,
      fsm_state: c.history[c.history.length - 1].to_state,
      paypal_order_id: `DEMO-ORDER-${n}`,
      paypal_authorization_id: reached("PAYPAL_AUTH_LOCKED") ? `DEMO-AUTH-${n}` : null,
      paypal_capture_id:
        reached("PAYPAL_CAPTURED") || reached("PARTIAL_CAPTURED") ? `DEMO-CAP-${n}` : null,
      paypal_payout_batch_id: reached("PAYOUT_DISPATCHED") ? `DEMO-PAYOUT-${n}` : null,
      verification_score: scored ? plan.score : null,
      created_at: created,
      updated_at: updated,
      history: [...c.history],
    };
  }

  ledger(): LedgerResponse {
    const entries: DoubleEntryLedgerEntry[] = [];
    let k = 0;
    const push = (debit: string, credit: string, amount: number, ref: string, at: string, description: string) => {
      if (amount <= 0) return;
      k += 1;
      entries.push({
        entry_id: `demo-entry-${pad(k)}`,
        debit_account: debit,
        credit_account: credit,
        amount_micros: amount,
        reference_id: ref,
        currency: "USD",
        created_at: at,
        description,
      });
    };
    for (const c of this.contracts) {
      const plan = PLANS[c.scenario];
      const ref = `demo-agr-${pad(c.seq)}`;
      const when = (s: FsmState) => c.history.find((h) => h.to_state === s)?.timestamp;
      const lockedAt = when("PAYPAL_AUTH_LOCKED");
      if (lockedAt) push("paypal_escrow_hold", "buyer_vault", BUDGET, ref, lockedAt, "Authorization hold locked");
      const voidAt = when("ESCROW_VOIDED_REFUNDED");
      if (voidAt) push("buyer_vault", "paypal_escrow_hold", BUDGET, ref, voidAt, "Authorization voided, buyer refunded");
      const capAt = when("PAYPAL_CAPTURED") ?? when("PARTIAL_CAPTURED");
      if (capAt) {
        const released = BUDGET - plan.captureMicros;
        push("buyer_vault", "paypal_escrow_hold", released, ref, capAt, "Uncaptured remainder released to buyer");
      }
      const payAt = when("PAYOUT_DISPATCHED");
      if (payAt) {
        const fee = Math.floor((plan.captureMicros * FEE_BPS) / 10_000);
        const net = plan.captureMicros - fee - RAIL_FEE;
        push("worker_payable", "paypal_escrow_hold", net, ref, payAt, "Net worker payout");
        push("platform_fee_revenue", "paypal_escrow_hold", fee, ref, payAt, "Protocol clearing fee");
        push("paypal_rail_expense", "paypal_escrow_hold", RAIL_FEE, ref, payAt, "PayPal payout rail fee");
      }
    }
    const balances: Record<string, number> = {};
    for (const e of entries) {
      balances[e.debit_account] = (balances[e.debit_account] ?? 0) + e.amount_micros;
      balances[e.credit_account] = (balances[e.credit_account] ?? 0) - e.amount_micros;
    }
    return { entries, balances };
  }
}
