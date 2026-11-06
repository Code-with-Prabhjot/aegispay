# AegisPay Postman Collection v2.0
*Targeting the **$5,000 Best Demo Delivery Track** (Judged by Postman Advocate Pooja Mistry)*

This directory contains the official Postman Collection v2.0 and Environment for AegisPay — the autonomous escrow and verified settlement engine for AI agents with PayPal Vaulting v3, bilateral Nash negotiation, AST hard veto sandboxing, 7-leaf Merkle audit trees, and integer micro-dollar double-entry bookkeeping.

---

## Files

- **`aegispay_collection.json`**: Postman Collection v2.1.0 with comprehensive pre-request scripts, environment variable propagation, and rich `pm.test` assertions across 5 core modules.
- **`aegispay_environment.json`**: Pre-configured environment variables:
  - `base_url`: `http://localhost:8000` (FastAPI backend)
  - `agreement_id`: dynamically saved from A2A negotiation and contract creation
  - `buyer_did`: `did:key:z6MkuBuyerAegisPay`
  - `worker_did`: `did:key:z6MkuWorkerAegisPay`
  - `auth_id`: dynamically saved PayPal authorization hold ID
  - `capture_id`: dynamically saved PayPal capture ID
  - `merkle_root`: dynamically saved 64-char SHA-256 root of the 7-leaf Merkle audit tree

---

## 5 Structured Demo Folders & Assertions

### 1. Health & Manifest
- **`GET /health`**: Verifies system status is `"ok"`, SQLite database is connected, and the double-entry ledger is mathematically balanced.
- **`GET /api/v1/mcp/manifest`**: Validates the Model Context Protocol (MCP) server manifest exposing `aegispay_propose_agreement`, `aegispay_submit_deliverable`, and `aegispay_check_escrow_status` to Claude and LLM agents.

### 2. Agent Identity & A2A Negotiation
- **`POST /api/v1/a2a/verify-identity`**: Cryptographic Ed25519 signature verification over a canonical challenge object, asserting decentralized DID resolution and cryptographic proof of identity.
- **`POST /api/v1/a2a/negotiate`**: Bilateral Nash Bargaining mediation between buyer and worker preference frontiers. Asserts Nash utility convergence (`nash_product > 0`, `buyer_utility > 0`, `worker_utility > 0`), dual-signs SLA contract, and saves `agreement_id` and `auth_id` to the Postman environment.

### 3. PayPal Escrow & Vaulting
- **`POST /api/v1/vault/setup-token`**: Creates a PayPal Vault v3 setup token binding buyer DID to pre-authorized automated payment credentials.
- **`GET /api/v1/vault/velocity/{{buyer_did}}`**: Audits 24-hour spend against mathematical velocity limits, confirming circuit breaker status is `ACTIVE` and safe.

### 4. Execution, AST Hard Veto & Merkle Proofs
- **`POST /api/v1/contracts/{{agreement_id}}/submit`**: Submits worker deliverable to an isolated sandbox. Executes AST hard veto security scanning, runs test assertions, blends composite arbitration score, captures PayPal authorization hold, and saves `capture_id`.
- **`GET /api/v1/contracts/{{agreement_id}}/detail`**: Inspects full contract state. Asserts the 7-leaf Merkle audit bundle (`leaves.length == 7`), rolling hash chain head, and double-entry journal legs. Saves `merkle_root`.
- **`GET /api/v1/contracts/{{agreement_id}}/merkle-proof/0`**: Cryptographic proof of inclusion for leaf 0 (agreement terms). Verifies root reconstruction via sibling hashes (`data.verified === true`).

### 5. Double-Entry Ledger Invariants
- **`GET /api/v1/ledger/balance`**: Mathematical balance conservation assertion: proves total debits minus total credits equals exactly zero (`debits - credits == 0`, `net_imbalance == 0`).
- **`GET /api/v1/ledger/accounts`**: Displays chart of accounts (`buyer_vault`, `paypal_escrow_hold`, `worker_payable`, `platform_fee_revenue`, `paypal_rail_expense`) with micro-dollar and USD balances.

---

## Running the Collection

### Option A: Postman Desktop / Web
1. Open Postman.
2. Click **Import** $\rightarrow$ select `postman/aegispay_collection.json` and `postman/aegispay_environment.json`.
3. Select the **AegisPay Environment** in the top-right environment selector.
4. Ensure AegisPay backend is running:
   ```bash
   uvicorn backend.app.main:app --port 8000
   ```
5. Click **Run collection** to execute all 5 folders sequentially from top to bottom.

### Option B: Newman CLI (Headless Automation)
```bash
npm install -g newman
newman run postman/aegispay_collection.json -e postman/aegispay_environment.json
```
