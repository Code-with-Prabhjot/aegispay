# AegisPay APIMatic SDK Generation & Validation (CodeGen v3)
*Targeting the **$1,000 APIMatic Track***

AegisPay delivers automated SDK generation and contract validation using the **APIMatic CodeGen v3 engine**. By maintaining strict compliance with APIMatic validation rules, AegisPay provides production-grade, type-safe developer SDKs for autonomous agent developers across Python and TypeScript.

---

## 1. APIMatic CodeGen v3 Architecture

```
                    ┌───────────────────────────────────────────────┐
                    │  backend/apimatic_validation/                 │
                    │  aegispay_api.openapi.yaml (OpenAPI 3.1)      │
                    └───────────────────────┬───────────────────────┘
                                            │
                                            ▼
                    ┌───────────────────────────────────────────────┐
                    │  apimatic/apimatic-config.json                │
                    │  • APIMatic CodeGen v3 Ruleset                │
                    │  • API Key Authentication (X-API-Key)         │
                    │  • Python (Pydantic v2 + HTTPX async/sync)    │
                    │  • TypeScript (Strict null checks + Fetch)    │
                    └───────────────────────┬───────────────────────┘
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    ▼                                               ▼
     ┌─────────────────────────────┐                 ┌─────────────────────────────┐
     │  dist/sdks/python/          │                 │  dist/sdks/typescript/      │
     │  aegispay-python            │                 │  @aegispay/sdk              │
     │  • Fully typed Pydantic v2  │                 │  • Dual ESM/CommonJS        │
     │  • Async & Sync httpx engine│                 │  • Native TS typings        │
     └─────────────────────────────┘                 └─────────────────────────────┘
```

---

## 2. APIMatic Validation Rules & Strict Compliance

The AegisPay API OpenAPI spec (`backend/apimatic_validation/aegispay_api.openapi.yaml`) conforms to APIMatic CodeGen v3 rules:
1. **Unambiguous Model Schemas**: Every request body, query parameter, and response payload is explicitly defined with non-conflicting types, descriptions, and bounds.
2. **Explicit Operation Identifiers**: Operations define unique `operationId` mappings for clean method names in generated SDKs.
3. **Deterministic Error Responses**: Handled error models (400, 401, 403, 404, 409, 422, 502, 503) are modeled with structured detail payloads.
4. **Security Scheme Standardization**: Global and route-level authentication maps directly to `X-API-Key` headers (`APIKeyHeader`), supporting open access in mock mode and authenticated access in sandbox/production.
5. **Contract Drift Verification**: `tests/test_apimatic_contract.py` runs 21 automated contract tests on every commit, verifying zero field drift against PayPal's and AegisPay's schemas.

---

## 3. Client SDK Packages

### A. Python Client SDK (`aegispay-python`)
- **Package Name**: `aegispay-python` (module `aegispay`)
- **Engine**: HTTPX (sync + async support)
- **Data Models**: Pydantic v2 with runtime validation
- **Usage**:
  ```python
  from aegispay import AegisPayClient

  client = AegisPayClient(base_url="http://localhost:8000")

  # 1. Check health & ledger balance invariant
  health = client.health()
  print("Status:", health["status"], "Ledger balanced:", health["ledger_balanced"])

  # 2. Negotiate A2A SLA via Nash bargaining
  agreement = client.negotiate_a2a({
      "buyer_pref": {"max_budget_micros": 15_000_000, "max_timeout_seconds": 8.0},
      "worker_pref": {"min_budget_micros": 5_000_000, "min_timeout_seconds": 2.0},
      "auto_sign": True,
      "buyer_agent_id": "did:key:z6MkuBuyerAegisPay",
  })
  print("Agreement ID:", agreement["agreement_id"])

  # 3. Submit deliverable for AST scan & sandbox verification
  result = client.submit_deliverable(
      agreement_id=agreement["agreement_id"],
      source_code="def solve(): return 42\n",
      test_suite="def test_solution(): assert solve() == 42\n",
  )
  print("Outcome:", result["fsm_state"])
  ```

### B. TypeScript Client SDK (`@aegispay/sdk`)
- **Package Name**: `@aegispay/sdk`
- **Engine**: Modern Fetch API with strict null checks
- **Target**: Universal Node.js, Next.js, and browser runtimes
- **Usage**:
  ```typescript
  import { AegisPayClient } from "@aegispay/sdk";

  const client = new AegisPayClient({ baseUrl: "http://localhost:8000" });

  // 1. Bilateral A2A SLA negotiation
  const agreement = await client.negotiateA2A({
    buyer_pref: { max_budget_micros: 15000000, max_timeout_seconds: 8.0 },
    worker_pref: { min_budget_micros: 5000000, min_timeout_seconds: 2.0 },
    auto_sign: true,
  });

  // 2. Submit deliverable for AST verification & PayPal capture
  const settlement = await client.submitDeliverable(
    agreement.agreement_id,
    "def solve(): return 42\n",
    "def test_solution(): assert solve() == 42\n"
  );
  console.log("Settlement FSM State:", settlement.fsm_state);
  ```

---

## 4. Running Validation & Exporting SDKs

### Local Export
Run the automated generation script:
```bash
python apimatic/export_sdks.py
# Or on Windows:
.\apimatic\export_sdks.bat
# Or on Linux/macOS:
./apimatic/export_sdks.sh
```

### APIMatic CLI Live Cloud Generation
If you have an APIMatic API key (`APIMATIC_API_KEY`):
```bash
npx --yes @apimatic/cli@2.0.0 auth login --auth-key="$APIMATIC_API_KEY"

# Validate AegisPay API
npx --yes @apimatic/cli@2.0.0 api validate --file backend/apimatic_validation/aegispay_api.openapi.yaml

# Generate typed SDKs directly from APIMatic CodeGen v3
npx --yes @apimatic/cli@2.0.0 sdk generate -l python --input backend/apimatic_validation/aegispay_api.openapi.yaml
npx --yes @apimatic/cli@2.0.0 sdk generate -l typescript --input backend/apimatic_validation/aegispay_api.openapi.yaml
```

CI automatically executes these validation checks in `.github/workflows/apimatic-validate.yml`.
