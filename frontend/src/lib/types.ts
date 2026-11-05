// Mirrors backend/app/schemas.py

export type FsmState =
  | "AGREEMENT_DRAFTED"
  | "PAYPAL_AUTH_LOCKED"
  | "AUTH_EXPIRED"
  | "SANDBOX_EXECUTING"
  | "ARBITRATION_AUDITING"
  | "FULL_CAPTURE_PENDING"
  | "PARTIAL_CAPTURE_PENDING"
  | "VOID_PENDING"
  | "PAYPAL_CAPTURED"
  | "PARTIAL_CAPTURED"
  | "CAPTURE_DECLINED"
  | "ESCROW_VOIDED_REFUNDED"
  | "PAYOUT_DISPATCHED"
  | "SETTLED_COMPLETE"
  | "PAYOUT_FAILED_REVERSED";

export const FSM_STATES: FsmState[] = [
  "AGREEMENT_DRAFTED",
  "PAYPAL_AUTH_LOCKED",
  "AUTH_EXPIRED",
  "SANDBOX_EXECUTING",
  "ARBITRATION_AUDITING",
  "FULL_CAPTURE_PENDING",
  "PARTIAL_CAPTURE_PENDING",
  "VOID_PENDING",
  "PAYPAL_CAPTURED",
  "PARTIAL_CAPTURED",
  "CAPTURE_DECLINED",
  "ESCROW_VOIDED_REFUNDED",
  "PAYOUT_DISPATCHED",
  "SETTLED_COMPLETE",
  "PAYOUT_FAILED_REVERSED",
];

export const TERMINAL_STATES: FsmState[] = [
  "AUTH_EXPIRED",
  "CAPTURE_DECLINED",
  "ESCROW_VOIDED_REFUNDED",
  "SETTLED_COMPLETE",
  "PAYOUT_FAILED_REVERSED",
];

/** Scenario keys (UI/fixtures). api.runScenario lowercases them; the backend route is case-insensitive. */
export type ScenarioName = "CLEAN_PASS" | "PARTIAL_DEFECT" | "MALICIOUS_INJECTION";

export type SettlementDecision = "FULL_CAPTURE" | "PARTIAL_CAPTURE" | "VOID_AUTHORIZATION";

export interface SettlementSplitResult {
  decision: SettlementDecision | string;
  capture_micros: number;
  buyer_released_micros: number;
  platform_fee_micros: number;
  paypal_rail_fee_micros: number;
  worker_net_payout_micros: number;
}

export interface ArbitrationReport {
  agreement_id: string;
  s_pytest: number;
  s_ast: number;
  s_semantic: number;
  composite_score: number;
  passed_tests: number;
  total_tests: number;
  ast_safe: boolean;
  ast_violations: string[];
  stdout: string;
  settlement: SettlementSplitResult;
}

export interface FsmTransition {
  from_state: FsmState | null;
  to_state: FsmState;
  timestamp: string;
}

export interface SlaContractResponse {
  agreement_id: string;
  buyer_agent_id: string;
  worker_agent_id: string;
  worker_paypal_email: string;
  budget_micros: number;
  task_description: string;
  timeout_seconds: number;
  fsm_state: FsmState;
  paypal_order_id?: string | null;
  paypal_authorization_id?: string | null;
  paypal_capture_id?: string | null;
  paypal_payout_batch_id?: string | null;
  verification_score?: number | null;
  created_at: string;
  updated_at: string;
  budget_usd?: string | null;
  /** Derived by api.ts from backend `transitions`; only on detail responses. */
  history?: FsmTransition[];
  arbitration_report?: ArbitrationReport | null;
  merkle_root?: string | null;
  hash_chain_head?: string | null;
}

export interface SlaContractCreate {
  agreement_id?: string;
  buyer_agent_id: string;
  worker_agent_id: string;
  worker_paypal_email: string;
  budget_micros: number;
  task_description: string;
  timeout_seconds?: number;
}

export interface SubmitWork {
  source_code: string;
  test_suite: string;
  runtime?: string;
}

export interface DoubleEntryLedgerEntry {
  entry_id: string;
  debit_account: string;
  credit_account: string;
  amount_micros: number;
  reference_id: string;
  currency: string;
  created_at: string;
  amount_usd?: string;
  description?: string | null;
}

export interface LedgerResponse {
  entries: DoubleEntryLedgerEntry[];
  /** account name -> balance in micros */
  balances: Record<string, number>;
  balanced?: boolean;
}

export interface HealthResponse {
  status: string;
  [k: string]: unknown;
}

// ----------------------------------------------------------------------------
// AG Grid Master-Detail & Real-Time Streaming Types
// ----------------------------------------------------------------------------

export interface TestAssertionItem {
  name: string;
  status: "PASSED" | "FAILED" | "PASS" | "FAIL" | string;
  duration_ms: number;
  error_message?: string | null;
}

export interface AstSecurityAudit {
  is_clean: boolean;
  scanned_imports: string[];
  blocked_calls: string[];
  score: number;
  nodes_count?: number;
  flagged_reasons?: string[];
}

export interface LedgerJournalLeg {
  debit_account: string;
  credit_account: string;
  amount_micros: number;
  amount_usd: string;
  memo?: string;
  created_at: string;
}

export interface AuditBundle {
  merkle_root: string;
  leaf_hashes: string[];
  leaf_names: string[];
  leaves: Record<string, unknown>[];
}

export interface AgreementDetailResponse extends SlaContractResponse {
  test_assertions: TestAssertionItem[];
  ast_security_audit: AstSecurityAudit;
  ledger_journal: LedgerJournalLeg[];
  audit_bundle?: AuditBundle | null;
  merkle_root?: string | null;
  hash_chain_head?: string | null;
}

export type WebSocketEventType =
  | "INITIAL_SNAPSHOT"
  | "FSM_TRANSITION"
  | "ARBITER_EVALUATED"
  | "LEDGER_TRANSACTION"
  | "HEARTBEAT"
  | "AUTH_LOCKED"
  | "TEST_PASSED"
  | "AST_CLEAN"
  | "SETTLEMENT_SPLIT"
  | "LEDGER_POSTED";

export interface WebSocketEnvelope<T = any> {
  event_type: WebSocketEventType | string;
  agreement_id: string;
  timestamp: string;
  data: T;
}
