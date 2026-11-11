// Deterministic demo-data mode: used when the backend is unreachable.
// No randomness or wall-clock reads: IDs and timestamps derive from a counter.
import type {
  AgreementDetailResponse,
  AstSecurityAudit,
  DoubleEntryLedgerEntry,
  FsmState,
  FsmTransition,
  LedgerJournalLeg,
  LedgerResponse,
  ScenarioName,
  SlaContractResponse,
  TestAssertionItem,
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
    score: 0.0,
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

  getDetail(id: string): AgreementDetailResponse | null {
    const contract = this.contracts.find((c) => `demo-agr-${pad(c.seq)}` === id);
    if (!contract) return null;
    const base = this.toResponse(contract);
    const plan = PLANS[contract.scenario];
    const created = base.created_at;

    // 1. Test Assertions
    let testAssertions: TestAssertionItem[] = [];
    if (contract.scenario === "CLEAN_PASS") {
      testAssertions = [
        { name: "test_01_initial_allow_within_capacity", status: "PASSED", duration_ms: 0.9, error_message: null },
        { name: "test_02_capacity_exhaustion", status: "PASSED", duration_ms: 1.1, error_message: null },
        { name: "test_03_client_isolation", status: "PASSED", duration_ms: 1.4, error_message: null },
        { name: "test_04_refill_replenishes_tokens", status: "PASSED", duration_ms: 122.3, error_message: null },
        { name: "test_05_variable_cost_deduction", status: "PASSED", duration_ms: 0.8, error_message: null },
        { name: "test_06_excessive_cost_rejected_without_deduction", status: "PASSED", duration_ms: 0.7, error_message: null },
        { name: "test_07_reset_single_client", status: "PASSED", duration_ms: 0.9, error_message: null },
        { name: "test_08_reset_all_clients", status: "PASSED", duration_ms: 1.0, error_message: null },
        { name: "test_09_get_remaining_accuracy", status: "PASSED", duration_ms: 0.8, error_message: null },
        { name: "test_10_burst_capacity_ceiling", status: "PASSED", duration_ms: 81.5, error_message: null },
      ];
    } else if (contract.scenario === "PARTIAL_DEFECT") {
      testAssertions = [
        { name: "test_01_initial_allow_within_capacity", status: "PASSED", duration_ms: 0.9, error_message: null },
        { name: "test_02_capacity_exhaustion", status: "PASSED", duration_ms: 1.2, error_message: null },
        { name: "test_03_client_isolation", status: "PASSED", duration_ms: 1.3, error_message: null },
        { name: "test_04_refill_replenishes_tokens", status: "PASSED", duration_ms: 121.8, error_message: null },
        { name: "test_05_variable_cost_deduction", status: "PASSED", duration_ms: 0.9, error_message: null },
        {
          name: "test_06_excessive_cost_rejected_without_deduction",
          status: "FAILED",
          duration_ms: 0.8,
          error_message: "AssertionError: assert 0 == 5 (state corrupted: tokens erroneously zeroed out on excessive cost rejection)",
        },
        { name: "test_07_reset_single_client", status: "PASSED", duration_ms: 0.9, error_message: null },
        {
          name: "test_08_reset_all_clients",
          status: "FAILED",
          duration_ms: 1.0,
          error_message: "ValueError: client_id cannot be None (reset() crashes when resetting all tracked clients without argument)",
        },
        { name: "test_09_get_remaining_accuracy", status: "PASSED", duration_ms: 0.8, error_message: null },
        {
          name: "test_10_burst_capacity_ceiling",
          status: "FAILED",
          duration_ms: 82.1,
          error_message: "AssertionError: assert 13.0 == 5 (unclamped burst: tokens exceeded capacity ceiling)",
        },
      ];
    } else {
      testAssertions = [
        {
          name: "test_ast_security_perimeter_isolation",
          status: "FAILED",
          duration_ms: 0.3,
          error_message: "SecurityHardVetoError: Deliverable triggered hard AST perimeter veto: Prohibited network call detected [socket.connect(('evil.com', 80))]. Sandbox aborted.",
        },
        {
          name: "test_01_execution_aborted",
          status: "FAILED",
          duration_ms: 0.0,
          error_message: "Skipped: sandbox execution halted by AST security auditor",
        },
      ];
    }

    // 2. AST Security Audit
    const astAudit: AstSecurityAudit =
      contract.scenario === "MALICIOUS_INJECTION"
        ? {
            is_clean: false,
            score: 0.0,
            scanned_imports: ["socket", "subprocess", "time"],
            blocked_calls: ["socket.socket", "socket.connect", "subprocess.Popen"],
            nodes_count: 38,
            flagged_reasons: [
              "Prohibited module 'socket' detected in AST import table",
              "Prohibited network connection vector 'socket.connect' flagged",
              "Subprocess/shell execution vector flagged in perimeter policy",
            ],
          }
        : {
            is_clean: true,
            score: 1.0,
            scanned_imports: ["time", "typing"],
            blocked_calls: [],
            nodes_count: 54,
            flagged_reasons: [],
          };

    // 3. Double-Entry Journal Legs (Debits == Credits)
    const journal: LedgerJournalLeg[] = [];
    if (contract.scenario === "CLEAN_PASS") {
      journal.push(
        {
          debit_account: "paypal_escrow_hold",
          credit_account: "buyer_vault",
          amount_micros: 15_000_000,
          amount_usd: "$15.00",
          memo: "PayPal authorization hold locked for task escrow",
          created_at: created,
        },
        {
          debit_account: "worker_payable",
          credit_account: "paypal_escrow_hold",
          amount_micros: 14_450_000,
          amount_usd: "$14.45",
          memo: "Net worker deliverable settlement payout",
          created_at: base.updated_at,
        },
        {
          debit_account: "platform_fee_revenue",
          credit_account: "paypal_escrow_hold",
          amount_micros: 300_000,
          amount_usd: "$0.30",
          memo: "AegisPay protocol clearing fee (2.0%)",
          created_at: base.updated_at,
        },
        {
          debit_account: "paypal_rail_expense",
          credit_account: "paypal_escrow_hold",
          amount_micros: 250_000,
          amount_usd: "$0.25",
          memo: "PayPal Payouts API rail batch fee",
          created_at: base.updated_at,
        },
      );
    } else if (contract.scenario === "PARTIAL_DEFECT") {
      journal.push(
        {
          debit_account: "paypal_escrow_hold",
          credit_account: "buyer_vault",
          amount_micros: 15_000_000,
          amount_usd: "$15.00",
          memo: "PayPal authorization hold locked for task escrow",
          created_at: created,
        },
        {
          debit_account: "buyer_vault",
          credit_account: "paypal_escrow_hold",
          amount_micros: 4_500_000,
          amount_usd: "$4.50",
          memo: "Uncaptured partial defect balance released to buyer",
          created_at: base.updated_at,
        },
        {
          debit_account: "worker_payable",
          credit_account: "paypal_escrow_hold",
          amount_micros: 10_040_000,
          amount_usd: "$10.04",
          memo: "Pro-rata worker payout (70% score prorated)",
          created_at: base.updated_at,
        },
        {
          debit_account: "platform_fee_revenue",
          credit_account: "paypal_escrow_hold",
          amount_micros: 210_000,
          amount_usd: "$0.21",
          memo: "AegisPay protocol clearing fee (2.0% on $10.50 captured)",
          created_at: base.updated_at,
        },
        {
          debit_account: "paypal_rail_expense",
          credit_account: "paypal_escrow_hold",
          amount_micros: 250_000,
          amount_usd: "$0.25",
          memo: "PayPal Payouts API rail batch fee",
          created_at: base.updated_at,
        },
      );
    } else {
      journal.push(
        {
          debit_account: "paypal_escrow_hold",
          credit_account: "buyer_vault",
          amount_micros: 15_000_000,
          amount_usd: "$15.00",
          memo: "PayPal authorization hold locked for task escrow",
          created_at: created,
        },
        {
          debit_account: "buyer_vault",
          credit_account: "paypal_escrow_hold",
          amount_micros: 15_000_000,
          amount_usd: "$15.00",
          memo: "Authorization voided: AST security hard veto - 100% refunded to buyer",
          created_at: base.updated_at,
        },
      );
    }

    const merkleRoot =
      contract.scenario === "MALICIOUS_INJECTION"
        ? "0x0000000000000000000000000000000000000000000000000000000000000000"
        : contract.scenario === "PARTIAL_DEFECT"
          ? "0x3a8c1f92e0789bc4671de0129f8471c26581903ba841c726359012fca459b71e"
          : "0x7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069";

    const hashChainHead =
      contract.scenario === "MALICIOUS_INJECTION"
        ? "0x9999deadbeef9999deadbeef9999deadbeef9999deadbeef9999deadbeef9999"
        : contract.scenario === "PARTIAL_DEFECT"
          ? "0x918cb4926f04918e7c10294b291a4572ef9108c3a726194b6159203847a98bc1"
          : "0x489b039478f4ac9b291a27e7f9189b8214df7c94b2a3c7e0b51f08e4d29381ea";

    return {
      ...base,
      test_assertions: testAssertions,
      ast_security_audit: astAudit,
      ledger_journal: journal,
      merkle_root: merkleRoot,
      hash_chain_head: hashChainHead,
      audit_bundle: {
        merkle_root: merkleRoot,
        leaf_hashes: [
          merkleRoot.slice(0, 18),
          hashChainHead.slice(0, 18),
          "0x111122223333444455",
          "0x555566667777888899",
          "0xaaaa99998888777766",
          "0xbbbbccccddddeeee00",
          "0xffffeeeeccccbbbb11",
        ],
        leaf_names: [
          "agreement_spec",
          "paypal_auth_receipt",
          "sandbox_test_run",
          "ast_security_report",
          "settlement_split",
          "paypal_settlement_receipt",
          "ledger_journal_entry",
        ],
        leaves: [],
      },
    };
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
      merkle_root:
        c.scenario === "MALICIOUS_INJECTION"
          ? "0x0000000000000000000000000000000000000000000000000000000000000000"
          : c.scenario === "PARTIAL_DEFECT"
            ? "0x3a8c1f92e0789bc4671de0129f8471c26581903ba841c726359012fca459b71e"
            : "0x7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069",
      hash_chain_head:
        c.scenario === "MALICIOUS_INJECTION"
          ? "0x9999deadbeef9999deadbeef9999deadbeef9999deadbeef9999deadbeef9999"
          : c.scenario === "PARTIAL_DEFECT"
            ? "0x918cb4926f04918e7c10294b291a4572ef9108c3a726194b6159203847a98bc1"
            : "0x489b039478f4ac9b291a27e7f9189b8214df7c94b2a3c7e0b51f08e4d29381ea",
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
    return { entries, balances, balanced: true };
  }
}

export const FIXTURE_LEDGER: LedgerResponse = {
  entries: [
    {
      entry_id: "demo-entry-0001",
      debit_account: "paypal_escrow_hold",
      credit_account: "buyer_vault",
      amount_micros: 50_000_000,
      reference_id: "demo-auth-init",
      currency: "USD",
      created_at: new Date().toISOString(),
      description: "Authorized Escrow Hold Allocation ($50.00 Authorized)",
    },
    {
      entry_id: "demo-entry-0002",
      debit_account: "worker_payable",
      credit_account: "paypal_escrow_hold",
      amount_micros: 32_500_000,
      reference_id: "demo-payout-settle",
      currency: "USD",
      created_at: new Date().toISOString(),
      description: "Settled Payout Net Disbursement ($32.50 Settled)",
    },
    {
      entry_id: "demo-entry-0003",
      debit_account: "buyer_vault",
      credit_account: "paypal_escrow_hold",
      amount_micros: 17_500_000,
      reference_id: "demo-hold-release",
      currency: "USD",
      created_at: new Date().toISOString(),
      description: "Released Uncaptured Hold Remainder to Buyer Vault",
    },
  ],
  balances: {
    buyer_vault: 50_000_000,
    worker_payable: 32_500_000,
    platform_fee_revenue: 0,
    paypal_rail_expense: 0,
    paypal_escrow_hold: 0,
  },
  balanced: true,
};

