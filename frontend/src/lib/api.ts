// Single module for all backend calls. Adjust here if the real API differs.
//
// Assumed contract (base = NEXT_PUBLIC_API_BASE, default http://localhost:8000/api/v1):
//   GET  /health                       -> { status: string }
//   POST /contracts                    body SlaContractCreate -> SlaContractResponse
//   GET  /contracts                    -> SlaContractResponse[]   (a {contracts: [...]} wrapper is also tolerated)
//   GET  /contracts/{id}               -> SlaContractResponse & { history: FsmTransition[] }
//   POST /contracts/{id}/submit        -> SlaContractResponse
//   POST /demo/scenarios/{name}        -> SlaContractResponse  (name: CLEAN_PASS | PARTIAL_DEFECT | MALICIOUS_INJECTION)
//   GET  /ledger                       -> { entries: DoubleEntryLedgerEntry[], balances: {account: micros} }
import type {
  HealthResponse,
  LedgerResponse,
  ScenarioName,
  SlaContractCreate,
  SlaContractResponse,
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

export async function listContracts(): Promise<SlaContractResponse[]> {
  const data = await request<SlaContractResponse[] | { contracts: SlaContractResponse[] }>("/contracts");
  return Array.isArray(data) ? data : data.contracts ?? [];
}

export const getContract = (id: string) => request<SlaContractResponse>(`/contracts/${encodeURIComponent(id)}`);

export const createContract = (body: SlaContractCreate) =>
  request<SlaContractResponse>("/contracts", { method: "POST", body: JSON.stringify(body) });

export const submitContract = (id: string, body: Record<string, unknown> = {}) =>
  request<SlaContractResponse>(`/contracts/${encodeURIComponent(id)}/submit`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const runScenario = (name: ScenarioName) =>
  request<SlaContractResponse>(`/demo/scenarios/${name}`, { method: "POST" });

export const getLedger = () => request<LedgerResponse>("/ledger");
