// Single module for all backend calls (verified against the real FastAPI backend).
//
// Contract (base = NEXT_PUBLIC_API_BASE, default http://localhost:8000/api/v1):
//   GET  /health                       -> { status, paypal_mode, mock }
//   POST /contracts                    body SlaContractCreate -> contract detail (201)
//   GET  /contracts                    -> { contracts: SlaContractResponse[], count }
//   GET  /contracts/{id}               -> SlaContractResponse + { transitions: [{from_state,to_state,reason,created_at}],
//                                         arbitration_report, authorized_at }   (normalised here to `history`)
//   GET  /agreements/{id}/detail       -> AgreementDetailResponse (AG Grid Master-Detail schema)
//   POST /contracts/{id}/submit        body { source_code, test_suite, runtime } -> contract detail
//   POST /demo/scenarios/{name}        -> contract detail + { ledger_entries, balances }
//                                         (name: clean_pass | partial_defect | malicious_injection, case-insensitive)
//   GET  /ledger                       -> { entries, balances: {account: micros}, balanced }
import type {
  AgreementDetailResponse,
  HealthResponse,
  FsmTransition,
  LedgerResponse,
  ScenarioName,
  SlaContractCreate,
  SlaContractResponse,
  SubmitWork,
  VaultVelocityStatus,
} from "./types";

function resolveApiBase(): string {
  if (typeof window !== "undefined") {
    // In browser environments, make REST requests use relative /api/v1 routes
    // so Next.js rewrites proxy them automatically, eliminating Codespaces port visibility and CORS issues.
    const envBase = process.env.NEXT_PUBLIC_API_BASE;
    if (envBase && !envBase.includes("localhost") && !envBase.includes("127.0.0.1") && envBase.startsWith("http")) {
      return envBase.replace(/\/$/, "");
    }
    return "/api/v1";
  }
  // Server-side fallback (e.g. during SSR or build)
  const backend =
    process.env.BACKEND_URL ||
    (process.env.NEXT_PUBLIC_API_BASE?.startsWith("http")
      ? process.env.NEXT_PUBLIC_API_BASE.replace(/\/api\/v1\/?$/, "")
      : "http://127.0.0.1:8000");
  return `${backend.replace(/\/$/, "")}/api/v1`;
}

export const API_BASE = resolveApiBase();

export function getWebSocketUrl(path = "/ws/ledger"): string {
  // Normalize path and eliminate any duplicate /api/v1 prefixes
  let cleanPath = path.trim();
  if (!cleanPath.startsWith("/")) cleanPath = `/${cleanPath}`;
  cleanPath = cleanPath.replace(/^(\/api\/v1)+/i, "/api/v1");
  if (cleanPath === "/ws/ledger") {
    cleanPath = "/api/v1/ws/ledger";
  }

  // 1. Explicit override via NEXT_PUBLIC_WS_URL
  if (typeof window !== "undefined" && process.env.NEXT_PUBLIC_WS_URL) {
    const customWs = process.env.NEXT_PUBLIC_WS_URL.replace(/\/$/, "");
    return customWs.includes("/ws/") ? customWs : `${customWs}${cleanPath}`;
  }

  // 2. Browser environment
  if (typeof window !== "undefined") {
    // Same rule as REST (resolveApiBase): an absolute, non-local NEXT_PUBLIC_API_BASE means the
    // browser talks to the backend directly.
    if (API_BASE.startsWith("http")) {
      try {
        const parsed = new URL(API_BASE);
        const wsProto = parsed.protocol === "https:" ? "wss:" : "ws:";
        return `${wsProto}//${parsed.host}${cleanPath}`;
      } catch {
        // fall through to the same-origin proxy
      }
    }
    const host = window.location.host;
    const hostname = window.location.hostname;
    const isLocalhost = hostname === "localhost" || hostname === "127.0.0.1";

    // On local machine (Next.js on 3000, FastAPI on 8000), default directly to port 8000
    // because Next.js HTTP rewrites do not proxy WebSockets.
    if (isLocalhost) {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      return `${protocol}//${hostname}:8000${cleanPath}`;
    }

    // GitHub Codespaces port forwarding (-3000 subdomain to -8000)
    if (hostname.includes("app.github.dev")) {
      const codespaceBackend = host.replace(/-3000(\.app\.github\.dev)/, "-8000$1");
      return `wss://${codespaceBackend}${cleanPath}`;
    }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${host}${cleanPath}`;
  }

  // 3. Server-side environment
  const rawBackend = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
  const base = rawBackend.replace(/^http:/i, "ws:").replace(/^https:/i, "wss:");
  return `${base}${cleanPath}`;
}

/**
 * Short-lived WebSocket ticket. Browsers cannot send X-API-Key on a WebSocket, so the dashboard
 * fetches a ticket over REST (the proxy middleware adds the key) and passes it as ?ticket=.
 * Returns null when the backend runs without an API key.
 */
export async function fetchWebSocketTicket(): Promise<string | null> {
  try {
    const body = await request<{ ticket?: string | null }>("/ws-ticket");
    return body.ticket ?? null;
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const onAbort = () => controller.abort();
  init?.signal?.addEventListener("abort", onAbort, { once: true });
  if (init?.signal?.aborted) controller.abort();
  const timeout = setTimeout(() => controller.abort(), init?.method === "POST" ? 90000 : 6000);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    });
    if (!res.ok) {
      throw new ApiError(`${res.status} ${res.statusText} for ${path}`, res.status);
    }
    return (await res.json()) as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (controller.signal.aborted || init?.signal?.aborted) {
      throw new ApiError("Backend request timed out. The backend may still be waking up.");
    }
    throw new ApiError(`Backend unreachable: ${(e as Error).message}`);
  } finally {
    clearTimeout(timeout);
    init?.signal?.removeEventListener("abort", onAbort);
  }
}

export const getHealth = (signal?: AbortSignal) => request<HealthResponse>("/health", { signal });

interface RawTransition {
  from_state: FsmTransition["from_state"];
  to_state: FsmTransition["to_state"];
  created_at?: string;
  timestamp?: string;
}

/** Backend sends `transitions[].created_at`; the UI consumes `history[].timestamp`. */
function normalizeContract<T extends SlaContractResponse>(raw: T & { transitions?: RawTransition[] }): T {
  const { transitions, ...rest } = raw;
  if (!transitions) return rest as T;
  return {
    ...rest,
    history: transitions.map((t) => ({
      from_state: t.from_state,
      to_state: t.to_state,
      timestamp: t.created_at || t.timestamp || new Date().toISOString(),
    })),
  } as T;
}

export async function listContracts(signal?: AbortSignal): Promise<SlaContractResponse[]> {
  const data = await request<SlaContractResponse[] | { contracts: SlaContractResponse[] }>("/contracts", { signal });
  return (Array.isArray(data) ? data : data.contracts ?? []).map(normalizeContract);
}

export const getContract = async (id: string, signal?: AbortSignal) =>
  normalizeContract(await request<SlaContractResponse>(`/contracts/${encodeURIComponent(id)}`, { signal }));

export async function getAgreementDetail(id: string): Promise<AgreementDetailResponse> {
  try {
    const raw = await request<AgreementDetailResponse>(`/contracts/${encodeURIComponent(id)}/detail`);
    return normalizeContract(raw);
  } catch (err) {
    // Alias fallback
    if (!(err instanceof ApiError) || err.status !== 404) throw err;
    const raw = await request<AgreementDetailResponse>(`/agreements/${encodeURIComponent(id)}/detail`);
    return normalizeContract(raw);
  }
}

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

export const seedDemoScenarios = () =>
  request<{ contracts: SlaContractResponse[]; count: number }>("/demo/scenarios/all", { method: "POST" });

export const getLedger = (signal?: AbortSignal) => request<LedgerResponse>("/ledger", { signal });

export const getVaultStatus = async (vaultId = "vault_buyer_primary"): Promise<VaultVelocityStatus> => {
  try {
    return await request<VaultVelocityStatus>(`/vault/status`);
  } catch {
    return await request<VaultVelocityStatus>(`/vaults/${encodeURIComponent(vaultId)}`);
  }
};

export const refreshVaultAllowance = async (
  vaultId = "vault_buyer_primary"
): Promise<{ status: string; refreshed: boolean; vault: VaultVelocityStatus }> => {
  try {
    return await request<{ status: string; refreshed: boolean; vault: VaultVelocityStatus }>("/vault/refresh", {
      method: "POST",
      body: JSON.stringify({ vault_id: vaultId }),
    });
  } catch {
    const res = await request<{ status: string; vault: VaultVelocityStatus }>(
      `/vaults/${encodeURIComponent(vaultId)}/reset`,
      { method: "POST" }
    );
    return { status: res.status, refreshed: true, vault: res.vault };
  }
};

export const toggleVaultFreeze = async (
  vaultId = "vault_buyer_primary",
  freeze?: boolean
): Promise<{ status: string; is_frozen: number; vault: VaultVelocityStatus }> => {
  return await request<{ status: string; is_frozen: number; vault: VaultVelocityStatus }>("/vault/freeze", {
    method: "POST",
    body: JSON.stringify({ vault_id: vaultId, freeze }),
  });
};
