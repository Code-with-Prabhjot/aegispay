// Single module for all backend calls (verified against the real FastAPI backend).
//
// Contract (base = NEXT_PUBLIC_API_BASE, default http://localhost:8000/api/v1):
//   GET  /health                       -> { status, paypal_mode, mock }
//   POST /contracts                    body SlaContractCreate -> contract detail (201)
//   GET  /contracts                    -> { contracts: SlaContractResponse[], count }
//   GET  /contracts/{id}               -> SlaContractResponse + { transitions: [{from_state,to_state,reason,created_at}],
//                                         arbitration_report, authorized_at }   (normalised here to `history`)
//   POST /contracts/{id}/submit        body { source_code, test_suite } -> contract detail
//   POST /demo/scenarios/{name}        -> contract detail + { ledger_entries, balances }
//                                         (name: clean_pass | partial_defect | malicious_injection, case-insensitive)
//   GET  /ledger                       -> { entries, balances: {account: micros}, balanced }
import type {
  HealthResponse,
  FsmTransition,
  LedgerResponse,
  ScenarioName,
  SlaContractCreate,
  SlaContractResponse,
  SubmitWork,
} from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000/api/v1").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    });
  } catch (e) {
    throw new ApiError(`Backend unreachable: ${(e as Error).message}`);
  }
  if (!res.ok) {
    throw new ApiError(`${res.status} ${res.statusText} for ${path}`, res.status);
  }
  return (await res.json()) as T;
}

export const getHealth = () => request<HealthResponse>("/health");

interface RawTransition {
  from_state: FsmTransition["from_state"];
  to_state: FsmTransition["to_state"];
  created_at: string;
}

/** Backend sends `transitions[].created_at`; the UI consumes `history[].timestamp`. */
function normalizeContract(raw: SlaContractResponse & { transitions?: RawTransition[] }): SlaContractResponse {
  const { transitions, ...rest } = raw;
  if (!transitions) return rest;
  return {
    ...rest,
    history: transitions.map((t) => ({ from_state: t.from_state, to_state: t.to_state, timestamp: t.created_at })),
  };
}

export async function listContracts(): Promise<SlaContractResponse[]> {
  const data = await request<SlaContractResponse[] | { contracts: SlaContractResponse[] }>("/contracts");
  return (Array.isArray(data) ? data : data.contracts ?? []).map(normalizeContract);
}

export const getContract = async (id: string) =>
  normalizeContract(await request<SlaContractResponse>(`/contracts/${encodeURIComponent(id)}`));

export const createContract = async (body: SlaContractCreate) =>
  normalizeContract(await request<SlaContractResponse>("/contracts", { method: "POST", body: JSON.stringify(body) }));

export const submitContract = async (id: string, body: SubmitWork) =>
  normalizeContract(
    await request<SlaContractResponse>(`/contracts/${encodeURIComponent(id)}/submit`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  );

export const runScenario = async (name: ScenarioName) =>
  normalizeContract(
    await request<SlaContractResponse>(`/demo/scenarios/${encodeURIComponent(name.toLowerCase())}`, { method: "POST" }),
  );

export const getLedger = () => request<LedgerResponse>("/ledger");
