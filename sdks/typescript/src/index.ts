/**
 * @aegispay/sdk
 * TypeScript Client Package for AegisPay Autonomous Escrow, AST Verification & Double-Entry Settlement.
 * Derived from and adhering to OpenAPI 3.1.0 specification (aegispay_api.openapi.yaml).
 */

export interface SlaContractCreate {
  buyer_agent_id: string;
  worker_agent_id: string;
  worker_paypal_email: string;
  budget_micros: number;
  task_description: string;
  timeout_seconds?: number;
  vault_id?: string | null;
  test_suite?: string | null;
}

export interface DeliverableSubmission {
  source_code: string;
  test_suite?: string | null;
  runtime?: string | null;
}

export type FsmState =
  | "PROPOSED"
  | "PAYPAL_AUTH_LOCKED"
  | "SUBMITTED"
  | "ARBITRATING"
  | "SETTLED"
  | "PAYPAL_CAPTURED"
  | "PAYPAL_VOIDED"
  | "EXPIRED"
  | "FAILED";

export interface SettlementDecision {
  decision: "FULL_CAPTURE" | "PRO_RATA_CAPTURE" | "VOID_AUTHORIZATION";
  capture_micros: number;
  buyer_released_micros: number;
  worker_payout_micros: number;
}

export interface AstViolation {
  node_type?: string;
  banned_identifier?: string;
  reason?: string;
  line?: number;
}

export interface ArbitrationReport {
  composite_score: number;
  s_ast: number;
  s_test: number;
  s_semantic: number;
  ast_safe: boolean;
  passed_tests: number;
  total_tests: number;
  ast_violations: AstViolation[];
  settlement: SettlementDecision;
}

export interface ContractResponse {
  agreement_id: string;
  fsm_state: FsmState;
  buyer_agent_id: string;
  worker_agent_id: string;
  worker_paypal_email: string;
  budget_micros: number;
  task_description: string;
  timeout_seconds: number;
  paypal_order_id?: string | null;
  paypal_authorization_id?: string | null;
  paypal_capture_id?: string | null;
  merkle_root?: string | null;
  arbitration_report?: ArbitrationReport | null;
  created_at?: string;
  authorized_at?: string | null;
  settled_at?: string | null;
}

export interface MerkleProofResponse {
  agreement_id: string;
  leaf_index: number;
  leaf_name: string;
  leaf_data: Record<string, unknown>;
  leaf_hash: string;
  proof_hashes: string[];
  merkle_root: string;
  verified: boolean;
}

export interface AegisPayClientOptions {
  baseUrl?: string;
  apiKey?: string;
  fetch?: typeof fetch;
}

export class AegisPayClient {
  private readonly baseUrl: string;
  private readonly apiKey?: string;
  private readonly customFetch: typeof fetch;

  constructor(options: AegisPayClientOptions = {}) {
    this.baseUrl = (options.baseUrl || "http://localhost:8000").replace(/\/+$/, "");
    this.apiKey = options.apiKey;
    this.customFetch = options.fetch || (typeof fetch !== "undefined" ? fetch.bind(globalThis) : (undefined as unknown as typeof fetch));
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    };
    if (this.apiKey) {
      headers["X-API-Key"] = this.apiKey;
    }

    const res = await this.customFetch(`${this.baseUrl}${path}`, {
      ...options,
      headers,
    });

    if (!res.ok) {
      const errBody = await res.text().catch(() => "");
      throw new Error(`AegisPay API Error ${res.status} on ${path}: ${errBody}`);
    }

    return res.json() as Promise<T>;
  }

  /**
   * Proposes an SLA contract and locks escrow via PayPal authorization hold.
   */
  async propose_sla(params: SlaContractCreate): Promise<ContractResponse> {
    return this.request<ContractResponse>("/api/v1/contracts", {
      method: "POST",
      body: JSON.stringify(params),
    });
  }

  async proposeSla(params: SlaContractCreate): Promise<ContractResponse> {
    return this.propose_sla(params);
  }

  /**
   * Retrieves or verifies the locked escrow state for a given agreement.
   */
  async lock_escrow(agreementId: string): Promise<ContractResponse> {
    return this.request<ContractResponse>(`/api/v1/contracts/${agreementId}`, {
      method: "GET",
    });
  }

  async lockEscrow(agreementId: string): Promise<ContractResponse> {
    return this.lock_escrow(agreementId);
  }

  /**
   * Submits worker deliverable code for air-gapped AST and dual-consensus evaluation.
   */
  async submit_code(agreementId: string, submission: DeliverableSubmission): Promise<ContractResponse> {
    return this.request<ContractResponse>(`/api/v1/contracts/${agreementId}/submit`, {
      method: "POST",
      body: JSON.stringify(submission),
    });
  }

  async submitCode(agreementId: string, submission: DeliverableSubmission): Promise<ContractResponse> {
    return this.submit_code(agreementId, submission);
  }

  /**
   * Fetches cryptographic 7-leaf Merkle tree audit proof for dispute non-repudiation.
   */
  async get_merkle_proof(agreementId: string, leafIndex = 0): Promise<MerkleProofResponse> {
    return this.request<MerkleProofResponse>(`/api/v1/contracts/${agreementId}/merkle-proof/${leafIndex}`, {
      method: "GET",
    });
  }

  async getMerkleProof(agreementId: string, leafIndex = 0): Promise<MerkleProofResponse> {
    return this.get_merkle_proof(agreementId, leafIndex);
  }
}

export default AegisPayClient;
