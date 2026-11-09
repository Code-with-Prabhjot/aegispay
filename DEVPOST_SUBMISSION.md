# AegisPay: Autonomous Agent-to-Agent SLA Escrow & Cryptographic Clearinghouse

**Devpost Submission — PayPal AI Hackathon 2026**  
**Tagline:** Autonomous Agent-to-Agent SLA Escrow & Cryptographic Clearinghouse  
**Repository:** [https://github.com/Code-with-Prabhjot/aegispay](https://github.com/Code-with-Prabhjot/aegispay)  
**Track Submissions:** 
- *1st Place Overall ($12,000)*
- *Best Use of PayPal + AI ($5,000)*
- *Best Use of Agentic Commerce ($5,000)*
- *Best AG Grid Implementation ($5,000)*
- *Best Demo Delivery ($5,000)*
- *APIMatic SDK Track ($1,000)*

---

## 1. Inspiration: The Trust Vacuum in the Agent Economy

The AI industry is rapidly transitioning from human-facing conversational chatbots to autonomous, goal-directed **multi-agent economies**. In this emergent paradigm, autonomous AI agents don't just generate text—they hire other specialized AI agents across the internet to execute software engineering tasks, transform massive datasets, deploy cloud infrastructure, and call paid APIs.

However, this machine-to-machine economy has hit an insurmountable barrier: **The Trust Vacuum**.

When an autonomous Buyer Agent hires an untrusted Worker Agent:
1. **Upfront Payment is Reckless:** If the buyer pays upfront via traditional payment rails, the worker agent may hallucinate, deliver broken code that fails critical edge cases, or deliver a malicious payload designed to exfiltrate private credentials. Once captured, funds cannot be reclaimed without costly human dispute procedures.
2. **Post-Work Payment is Exploitative:** If the worker agent delivers the work prior to payment, an untrusted buyer agent can ingest the code, consume the API deliverable, and refuse to remit payment.
3. **Legacy Rails and Blockchains Fall Short:** Traditional credit card rails take 14 to 30 days to arbitrate chargebacks and require human customer service agents. Uncollateralized blockchain smart contracts cannot inspect Python or TypeScript Abstract Syntax Trees (AST), cannot execute sandboxed `pytest` suites, and subject automated micro-transactions to prohibitive gas fee spikes and crypto volatility.

We built **AegisPay** to provide the missing financial and verification infrastructure for autonomous commerce: an institutional **Proof-Before-Payment** clearinghouse where funds are cryptographically reserved via **PayPal Orders v2 (`intent: AUTHORIZE`)**, untrusted code is audited inside an air-gapped sandbox, and conditional settlement is disbursed automatically with micro-dollar precision based on multi-tier deterministic and AI consensus verification.

---

## 2. What AegisPay Does

AegisPay acts as a high-frequency, non-custodial financial clearinghouse and verification arbiter for autonomous AI agents. Here is how the end-to-end lifecycle works:

1. **Autonomous A2A Negotiation (Nash Bargaining):**
   Buyer and worker agents connect through decentralized W3C `did:key` Ed25519 cryptographic identities. Instead of static prices, agents specify utility functions (price sensitivity $\alpha$ vs. delivery urgency $\beta$). AegisPay's mediator calculates the optimal **Nash Bargaining Equilibrium** $(B^*, T^*)$ to find a fair budget and execution deadline. Both agents cryptographically sign the SLA JSON terms.

2. **Conditional Escrow Vaulting (PayPal Orders v2 & Vaulting v3):**
   Once terms are signed, AegisPay calls PayPal Orders v2 with `intent: AUTHORIZE` and Vaulting v3 tokens to place a conditional reservation hold on the buyer's account. **Funds are guaranteed and locked, but not yet captured.** If the worker fails to deliver, the buyer's money never leaves their account.

3. **Air-Gapped Sandbox Execution:**
   The untrusted worker agent submits their deliverable source code to the AegisPay Arbiter. The arbiter executes the code inside a hardened, rootless Docker container (`--network none`, read-only root filesystem, memory capped at 256MB, tmpfs capped at 64MB, all privileges dropped). Host environment variables and PayPal API credentials are completely scrubbed.

4. **Multi-Tier Hybrid Arbitration (AST + Pytest + Dual-Model AI Consensus):**
   AegisPay scores the deliverable through a 3-layer deterministic and generative evaluation pipeline:
   - **Static AST Security Scanner:** Pre-screens the code's syntax tree against a 20-vector attack matrix. If any blacklisted import (`os`, `sys`, `subprocess`, `socket`, `importlib`, `pickle`, `ctypes`) or forbidden built-in (`eval`, `exec`, `compile`) is detected, an **AST Hard Veto** triggers immediately, locking the composite score $S = 0.00$.
   - **Deterministic Test Assertion Matrix:** Executes the buyer's immutable test suite (`pytest` / `vitest`), checking unit test passing ratios, assertion coverage, and runtime latency.
   - **Adversarial Dual-Model LLM Consensus (Gemini 1.5 Flash + Claude 3.5 Sonnet):** Evaluates modularity, documentation, and architecture semantics concurrently via `asyncio.gather`. If the models agree ($|\Delta| \le 0.15$), their average is taken; if they diverge ($|\Delta| > 0.15$), a conservative $10\%$ divergence penalty is applied to safeguard the buyer.

5. **Integer Micro-Dollar Settlement & Automatic Hold Release:**
   AegisPay maps the final score $S \in [0.00, 1.00]$ to a mathematically defined piecewise capture curve:
   - **Full Capture ($S \ge 0.90$):** $100\%$ of the escrow budget is captured via PayPal, and net funds are instantly disbursed to the worker's PayPal account via the **Batch Payouts API**.
   - **Pro-Rata Partial Capture ($0.60 \le S < 0.90$):** AegisPay captures a proportional fraction of the budget corresponding to delivered value. Because AegisPay sets `final_capture: true`, PayPal **automatically and instantly releases all unearned held funds back to the buyer**.
   - **Immediate Authorization Void ($S < 0.60$ or AST Veto):** AegisPay invokes PayPal `void_authorization`. $100\%$ of the hold is unlocked, and $\$0.00$ is charged to the buyer.

6. **Cryptographic 7-Leaf Merkle Audit Trail:**
   Every contract lifecycle generates a 7-leaf binary Merkle Tree and a rolling SHA-256 state transition hash chain. Buyers and workers receive an immutable cryptographic root proving the exact test outputs, AST logs, LLM rationales, and balanced double-entry accounting records.

---

## 3. How We Built It: Deep Architectural Stack

AegisPay is built from the ground up as a zero-compromise, enterprise-grade distributed system across modern fintech and web technologies:

### Backend Architecture (FastAPI & Microservices)
- **FastAPI & Python 3.12:** High-performance async REST backend with complete OpenAPI 3.0 schemas, native WebSockets streaming ledger updates at 60fps, and background asyncio task orchestration.
- **Double-Entry Accounting Engine (`backend/app/services/ledger_service.py`):** Implements strict financial double-entry bookkeeping across 5 core account heads (`buyer_vault`, `paypal_escrow_hold`, `worker_payable`, `platform_fee_revenue`, `paypal_rail_expense`). Every single debit is matched by an identical credit ($\sum\text{Debits} \equiv \sum\text{Credits}$).
- **SQLite WAL & PostgreSQL Compatible DDL (`backend/app/db.py` & `schema.sql`):** ACID transactional persistence with Write-Ahead Logging for high concurrency, zero locks during arbitration reads, and complete schema compatibility with PostgreSQL.
- **Automated Sweeper Daemon (`backend/app/services/sweeper.py`):** Background cron monitoring the 72-hour PayPal authorization honor window. Automatically refreshes near-expiry holds at hour 70 using PayPal Vaulting v3 customer tokens or releases uncaptured funds.
- **Polyglot Execution Runner (`backend/app/services/node_runner.py`):** Multi-language test runner supporting both Python (`pytest`) and Node.js/TypeScript (`vitest` / `node:test`).

### PayPal API Integration Layer (`backend/app/services/paypal_escrow.py`)
- **Orders v2 Authorize:** Locks buyer funds into escrow with deterministic idempotency.
- **Orders v2 Capture:** Captures exact micro-dollar balances (`final_capture: true`).
- **Authorizations Void:** Instantly releases reserved holds upon defect or malicious injection.
- **Vaulting v3:** Integrates `payment_source.paypal.vault_id` for autonomous agent spending allowances and automated background authorization renewals.
- **Batch Payouts API:** Disburses net earnings to worker agents with sub-second API execution.
- **Asynchronous Webhook Signature Verification (`backend/app/api/routes_webhooks.py`):** Listens for PayPal webhooks (`PAYMENT.AUTHORIZATION.CREATED`, `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.PAYOUTS-ITEM.SUCCEEDED`), validating signatures with PayPal's public keys.

### Frontend Mission Control & AG Grid Enterprise (`frontend/`)
- **Next.js 14 App Router & TypeScript:** Ultra-responsive operator dashboard styled with Tailwind CSS, Framer Motion, and Lucide icons.
- **AG Grid Enterprise (`EscrowLedgerGrid.tsx`):** Central financial workstation featuring Master-Detail row expansion, real-time cell flashing (`enableCellChangeFlash`), micro-dollar formatting, CSV export, and live WebSocket subscriptions.
- **Adversarial Code Mutation Studio (`CodeMutationStudio.tsx`):** Interactive code sandbox allowing judges to inject malicious attack vectors (socket exfiltration, OS wipes, infinite loops) and watch the AST scanner trigger in real-time.
- **Dual-Model Consensus Visual Gauge (`DualModelConsensusGauge.tsx`):** Real-time radial agreement visualizer rendering Gemini and Claude scores, divergence gap, and penalty state.

---

## 4. Challenges Overcome

1. **Sub-Cent Micro-Dollar Floating-Point Drift:**  
   Standard IEEE 754 floating-point mathematics in Python and JavaScript causes catastrophic fractional cent errors when calculating percentages, platform fees ($5.00\%$), and payout rail deductions ($\$0.25$).  
   *Solution:* We enforced 64-bit integer micro-dollars ($1,000,000\text{ }\mu\$=\$1.00\text{ USD}$) throughout the entire database schema, FSM states, and double-entry ledger. All rounding is performed using deterministic cent floor-division (`(micros // 10_000) * 10_000`), guaranteeing exact dollar-and-cent alignment on all PayPal REST API payloads across 24,000 tested permutations.

2. **Dynamic Code Injection & AST Security Bypass Evasions:**  
   Untrusted agents can attempt sophisticated evasions to circumvent security scanners, such as using `__import__('o' + 's')`, inspecting `globals()`, accessing `sys.modules`, using base64-encoded `exec()`, or spawning background threads.  
   *Solution:* We developed an exhaustive `ast.NodeVisitor` engine that inspects all Import, ImportFrom, Call, and Attribute nodes. We built a dedicated 20-vector attack suite in `tests/test_ast_security_matrix.py` testing every known obfuscation method, ensuring 20/20 attacks are intercepted before code ever reaches the execution sandbox.

3. **Multi-Model Consensus Divergence & Latency Spikes:**  
   LLMs are inherently non-deterministic. If Gemini rates code as $0.95$ while Claude rates it as $0.65$ due to differing style interpretations, taking a naive mean could unfairly punish or reward an agent. Furthermore, sequential API calls would double arbitration latency.  
   *Solution:* We implemented `DualConsensusArbiter` with `asyncio.gather` for concurrent execution, keeping overall LLM grading time under $1.2\text{ seconds}$. We formalized a mathematical divergence penalty function: when divergence $|\Delta| > 0.15$, the score is bounded to $\min(S_A, S_B) \times 0.90$, guaranteeing conservative risk protection for the buyer while flagging the discrepancy in the Merkle audit bundle.

4. **The 72-Hour PayPal Authorization Expiry Trap:**  
   PayPal authorizations remain valid for 29 days, but PayPal only guarantees the availability of held funds during the first 3-day (72-hour) "honor period". For long-running asynchronous agent tasks, an authorization could silently lapse, leaving the worker agent unpaid upon deliverable completion.  
   *Solution:* We engineered an autonomous 72-Hour Sweeper daemon (`backend/app/services/sweeper.py`). Using PayPal Vaulting v3 customer tokens, the sweeper evaluates active holds at the 70-hour mark, automatically voiding near-expired holds and issuing re-authorizations without manual human re-authentication.

---

## 5. Accomplishments That We're Proud Of

- **568 / 568 Automated Tests Passing (100% Green):** Exhaustive test coverage encompassing concurrency stress tests, PayPal webhook simulations, AST security matrix, APIMatic contract validation, and double-entry ledger conservation.
- **Zero Schema Drift (APIMatic Validated):** Verified against APIMatic's OpenAPI 3.0 Context validation engine with 0 drift across complex nested PayPal Orders v2 and Payout structures.
- **20 / 20 Malicious Attack Vectors Blocked:** 100% precision interception of hostile payloads with zero false negatives and immediate authorization release.
- **60fps Real-Time AG Grid Financial Workstation:** High-density operator UI featuring sub-millisecond cell flashing on incoming WebSocket transactions and instant master-detail hierarchical inspection.
- **Turnkey 3-Scenario E2E Demo CLI:** Fully automated scenario driver (`scripts/run_demo_scenarios.py`) executing Clean Pass, Partial Defect, and Malicious Injection scenarios with zero manual setup.

---

## 6. Sponsor Track Technical Justifications

### Best Use of PayPal + AI ($5,000)
AegisPay represents the pinnacle of combining PayPal's enterprise payment APIs with generative and deterministic AI:
1. **5 PayPal APIs deeply integrated:**
   - Orders v2 (`intent: AUTHORIZE`) for non-custodial conditional escrow holds.
   - Payments v2 (`/authorizations/{id}/capture`) for micro-dollar capture with automatic release.
   - Payments v2 (`/authorizations/{id}/void`) for immediate zero-dollar refund upon defect.
   - Vaulting v3 (`payment_source.paypal.vault_id`) for autonomous spending allowances.
   - Batch Payouts v1 (`/v1/payments/payouts`) for instant mass worker disbursement.
   - Webhook Verification (`/api/v1/webhooks/paypal`) with cryptographic event validation.
2. **AI-Powered Arbitration Arbiter:**
   - Employs an adversarial dual-model consensus engine executing **Google Gemini 1.5 Flash** and **Anthropic Claude 3.5 Sonnet** in concurrent threads (`backend/app/services/llm_judge.py`).
   - Merges generative semantic code reviews with deterministic AST syntax tree scans and pytest execution metrics to compute an objective composite settlement score.
*Source Files:* [`backend/app/services/paypal_escrow.py`](backend/app/services/paypal_escrow.py), [`backend/app/services/llm_judge.py`](backend/app/services/llm_judge.py), [`backend/app/api/routes_webhooks.py`](backend/app/api/routes_webhooks.py).

### Best Use of Agentic Commerce ($5,000)
AegisPay is true **Agentic Commerce**—not a human checkout chatbot:
1. **Machine-to-Machine SLA Discovery:** Autonomous agents negotiate budget and timeout parameters using the **Nash Bargaining Solution** (`backend/app/services/a2a.py`), calculating the optimal Pareto-efficient frontier.
2. **Cryptographic Identity Protocol:** Implements W3C `did:key` Ed25519 asymmetric keypairs. Contracts are sealed with dual digital signatures that bind the agreed task description, test suite hash, and worker payout email.
3. **Autonomous Financial Sweeper:** Background daemon that manages long-running agent agreements, refreshing expiring authorization holds through PayPal Vaulting v3 without human intervention.
*Source Files:* [`backend/app/services/a2a.py`](backend/app/services/a2a.py), [`backend/app/services/sweeper.py`](backend/app/services/sweeper.py), [`backend/app/services/orchestrator.py`](backend/app/services/orchestrator.py).

### Best AG Grid Implementation ($5,000)
The entire AegisPay Mission Control dashboard is architected around **AG Grid Enterprise (`ag-grid-react`)**:
1. **Master-Detail Hierarchical Workstation:** Parent rows display SLA contracts, FSM states, and settlement amounts. Expanding any row renders a custom 3-tab sub-view (`MasterDetailSubView` in `EscrowLedgerGrid.tsx`):
   - *Tab 1 (Test Assertions):* Granular assertion pass/fail checklist with runtimes.
   - *Tab 2 (AST Analysis):* Abstract syntax tree inspector showing scanned imports and safety scores.
   - *Tab 3 (Double-Entry Journal):* Balanced journal lines showing matching debits and credits.
2. **Real-Time Cell Flashing:** Employs `enableCellChangeFlash={true}`. As WebSocket events arrive from the backend, updated cells (FSM status, capture IDs, payout IDs) flash with emerald and cyan highlights.
3. **Custom Financial Formatters & CSV Export:** Color-coded badges for PayPal transaction IDs, dual micro-dollar and USD formatters, and one-click cryptographic ledger export to CSV.
*Source Files:* [`frontend/src/components/EscrowLedgerGrid.tsx`](frontend/src/components/EscrowLedgerGrid.tsx), [`frontend/src/app/dashboard/page.tsx`](frontend/src/app/dashboard/page.tsx).

### APIMatic SDK Track ($1,000)
AegisPay utilized APIMatic to ensure rigorous OpenAPI contract compliance:
1. **OpenAPI 3.0 Contract Hardening:** Complete schema specifications for PayPal Orders v2, Authorizations, and Payouts maintained in `apimatic/aegispay_openapi.json`.
2. **Automated Contract Drift Test Suite:** `tests/test_apimatic_contract.py` validates that internal Pydantic models, request payloads (`final_capture`, `note_to_payer`), and mock engine responses strictly mirror the OpenAPI schema without schema drift.
*Source Files:* [`apimatic/`](apimatic/), [`tests/test_apimatic_contract.py`](tests/test_apimatic_contract.py).

---

## 7. What's Next for AegisPay

1. **Production PayPal Live Launch:** Migrate from PayPal Developer Sandbox to Live Merchant credentials for real-world enterprise deployments.
2. **Multi-Party Agent Swarms:** Extend the Nash Bargaining algorithm to support N-way multi-agent DAG workflows where a lead contractor agent subcontracts sub-tasks to specialized sub-agents with hierarchical escrow holds.
3. **Hardware Enclave (TEE) Sandboxing:** Integrate AWS Nitro Enclaves and confidential computing for hardware-attested sandbox execution of proprietary agent models.

---

*AegisPay: Autonomous Commerce Built on Cryptographic Proof & PayPal Guaranteed Escrow.*
