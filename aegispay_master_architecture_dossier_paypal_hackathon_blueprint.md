# AEGISPAY: AUTONOMOUS AGENTIC COMMERCE & TRUSTLESS ESCROW ENGINE
## Master Architectural Blueprint & PayPal Developer Platform Integration Dossier

**Target Competition:** PayPal AI Hackathon ($67,500 Total Prize Pool)  
**Target Award Category:** Best Agentic Commerce ($5,000) + Grand Champion ($12,000)  
**Target Timeline:** 12-Day Phased Engineering Sprint (Deadline: Mid-November 2026)  
**Proposed Repository Name:** `aegispay` (or `apexpay-agentic-commerce`)  

---

## 1. EXECUTIVE SUMMARY & WINNING POSITIONING

### 1.1 The Frontier Problem: The Agentic Commerce Dilemma
Autonomous AI agents are proliferating across software development, data pipeline construction, dynamic research synthesis, and API brokering. However, **autonomous agents cannot safely transact real money**. 

Giving an autonomous agent unrestricted credit card access or direct payment credentials introduces catastrophic operational risks:
1. **Prompt-Injection Fund Exfiltration:** Malicious third-party prompts can trick an execution agent into transferring funds to rogue wallets.
2. **Hallucination & Unfulfilled Deliverables:** Agents frequently report task completion despite failing subtle edge cases, producing broken code, or hallucinating synthetic datasets.
3. **Absence of Trustless Escrow:** In human commerce, escrow services (like Escrow.com or traditional marketplaces) require days of manual dispute review. In an agent economy operating in seconds, transactions need programmatic, sub-second conditional settlement.

### 1.2 The Solution: AegisPay
**AegisPay** is an autonomous agentic commerce protocol that turns PayPal into the authoritative financial clearinghouse for the machine-to-machine economy. 

Instead of trusting agents with raw cards, AegisPay introduces **Conditional Proof-Before-Payment**:
* **Autonomous Escrow Locking:** Buyer agents lock budget allocations in escrow using **PayPal Orders v2 (`intent: AUTHORIZE`)** and **PayPal Vaulting**.
* **Contract-First Verification:** Deliverables are autonomously audited by an independent verification judge running unit tests, AST syntactic diffs, and schema validation.
* **Instant Disbursal via PayPal Rails:** Upon mathematical verification ($\text{Score} \ge 0.90$), AegisPay executes an automated **PayPal Capture** and triggers instantaneous micro-disbursements to worker agents via the **PayPal Batch Payouts API**.
* **Zero-Human Dispute Mediation:** If an agent fails or underperforms, AegisPay's financial state machine executes an automated PayPal Authorization Void or partial capture refund without human intervention.

---

## 2. PAYPAL DEVELOPER PLATFORM INTEGRATION MATRIX

To maximize scoring under the PayPal AI Hackathon rubric, AegisPay integrates five distinct PayPal developer primitives:

| PayPal Developer API | API Endpoint | Technical Purpose in AegisPay Architecture |
| :--- | :--- | :--- |
| **Orders v2 (Escrow Authorize)** | `POST /v2/checkout/orders` (`intent: AUTHORIZE`) | Locks the buyer's maximum job budget in an escrow hold before the worker agent executes compute. |
| **Orders v2 (Capture Hold)** | `POST /v2/payments/authorizations/{id}/capture` | Programmatically captures verified funds once deliverables pass automated verification. |
| **PayPal Vaulting** | `POST /v2/checkout/orders` (`store_in_vault: ON_SUCCESS`) | Stores verified buyer payment tokens so autonomous buyer agents can re-up escrow automatically within daily spending caps. |
| **Batch Payouts API** | `POST /v1/payments/payouts` | Disburses instant, idempotent payments directly to worker agents' PayPal accounts using UUIDs as `sender_batch_id`. |
| **Webhook Notifications** | `POST /v1/notifications/verify-webhook-signature` | Ingests cryptographic PayPal webhook events (`PAYMENT.AUTHORIZATION.VOIDED`, `PAYMENT.PAYOUTS-ITEM.SUCCEEDED`) to reconcile state. |

---

## 3. FINTECH AGENTIC COMMERCE WORKFLOW

```
+--------------------------------------------------------------------------------------------------+
|                                1. BUYER AGENT SUBMITS TASK AGREEMENT                             |
|          Specifies JSON SLA Schema, Max Budget ($15.00), Target Model, & Acceptance Tests        |
+-------------------------------------------------+------------------------------------------------+
                                                  |
                                                  v
+--------------------------------------------------------------------------------------------------+
|                            2. AEGISPAY ESCROW ENGINE (PAYPAL ORDERS v2)                          |
|             Calls PayPal API with intent="AUTHORIZE" -> Locks $15.00 Hold in Escrow              |
|                    Generates Cryptographic Contract Signature (HMAC-SHA256)                      |
+-------------------------------------------------+------------------------------------------------+
                                                  |
                                                  v
+--------------------------------------------------------------------------------------------------+
|                                3. WORKER AGENT RUNS IN SANDBOX                                   |
|                Generates code, builds API integration, or synthesizes datasets                   |
+-------------------------------------------------+------------------------------------------------+
                                                  |
                                                  v
+--------------------------------------------------------------------------------------------------+
|                             4. INDEPENDENT VERIFICATION ARBITER                                  |
|         - Automated Unit Test Harness Execution (Pytest / Jest in Docker sandbox)                |
|         - AST Parsing & Code Quality Verification                                                |
|         - LLM Semantic Evaluation (Calculates Composite Score: 0.00 to 1.00)                      |
+-------------------------------------------------+------------------------------------------------+
                                                  |
                        +-------------------------+-------------------------+
                        |                                                   |
            [Score >= 0.90: SUCCESS]                               [Score < 0.90: DEFECT]
                        v                                                   v
+-----------------------------------------------+   +----------------------------------------------+
| 5A. PAYPAL CAPTURE & DISBURSAL                |   | 5B. AUTOMATED MEDIATION & REFUND             |
| - Captures exact settled spend ($14.20)       |   | - Partial Score (0.60–0.89): Partial Capture |
| - Disburses fee-safe split via PayPal Payouts |   |   with automated refund of unearned delta    |
| - Remaining authorization delta ($0.80) voided|   | - Failure (<0.60): Full Authorization Void   |
+-----------------------------------------------+   +----------------------------------------------+
```

---

## 4. FINANCIAL FINITE STATE MACHINE (FSM) SPECIFICATION

All transactions strictly traverse this ACID-compliant state engine:

```
[AGREEMENT_DRAFTED] 
       │ (Buyer & Worker sign contract terms)
       ▼
[PAYPAL_AUTH_LOCKED] 
       │ (PayPal Orders v2 AUTHORIZE hold placed; escrow confirmed)
       ▼
[SANDBOX_EXECUTING] 
       │ (Worker executes in isolated sandbox)
       ▼
[ARBITRATION_AUDITING] 
       │ (Arbiter executes automated verification)
       ├─────────────────────────────────┬─────────────────────────────────┐
       ▼ (Score >= 0.90)                 ▼ (0.60 <= Score < 0.90)          ▼ (Score < 0.60)
[SETTLEMENT_AUTHORIZED]           [PARTIAL_MEDIATION]              [BREACH_VOIDED]
       │                                 │                                 │
       ▼                                 ▼                                 ▼
[PAYPAL_CAPTURED]                 [PARTIAL_CAPTURED]               [AUTH_VOIDED]
       │ (Batch Payouts dispatched)      │ (Remaining refunded)            │ (100% funds released)
       ▼                                 ▼                                 ▼
[SETTLED_COMPLETE]                [SETTLED_PARTIAL]                [ESCROW_REFUNDED]
```

---

## 5. REPOSITORY ARCHITECTURE & FILE TREE

```text
aegispay/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                     # FastAPI application lifespan & routers
│   │   ├── config.py                   # Pydantic v2 settings (PayPal credentials, Sandbox keys)
│   │   ├── database.py                 # Async SQLAlchemy engine & PostgreSQL connection
│   │   ├── schemas/
│   │   │   ├── agreement.py            # Task contract & SLA schemas
│   │   │   ├── escrow.py               # PayPal order authorization & capture schemas
│   │   │   └── payout.py               # Batch payout and ledger disbursement schemas
│   │   ├── services/
│   │   │   ├── paypal_orders.py        # Orders v2 Authorize, Capture, Void implementations
│   │   │   ├── paypal_payouts.py       # Batch Payouts API client with UUID idempotency
│   │   │   ├── paypal_vault.py         # Customer vaulting & pre-approved payment tokens
│   │   │   ├── arbitration.py          # AST verification, test suite execution, LLM judge
│   │   │   └── ledger.py               # Double-entry micro-dollar PostgreSQL accounting
│   │   └── api/
│   │       ├── routes_agreements.py    # Contract creation & lifecycle endpoints
│   │       ├── routes_escrow.py        # PayPal checkout initiation & webhook receivers
│   │       └── routes_telemetry.py     # Real-time WebSocket settlement feed
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx                # Hero landing page & agent economy statistics
│   │   │   ├── escrow/page.tsx         # Live escrow transaction monitor & agreement maker
│   │   │   ├── arbiter/page.tsx        # Verification suite & code diff inspector
│   │   │   └── layout.tsx              # Root layout with navigation & PayPal SDK script
│   │   ├── components/
│   │   │   ├── PayPalButtonWrapper.tsx # Official @paypal/react-paypal-js button integration
│   │   │   ├── TransactionFlow.tsx     # Animated state machine visualizer
│   │   │   ├── LiveLedgerFeed.tsx      # WebSocket streaming settlement ticker
│   │   │   └── CodeAuditDrawer.tsx     # Arbiter score breakdown & test report
│   │   └── lib/
│   │       └── api.ts                  # Typed client for backend communication
├── tests/
│   ├── test_paypal_client.py           # Mocked & Sandbox PayPal API tests
│   ├── test_escrow_fsm.py              # State machine invariant & rollback tests
│   └── test_arbitration.py             # Test suite execution & scoring validation
├── docker-compose.yml                  # PostgreSQL 16 + Redis + Backend containerization
├── requirements.txt                    # Pinned Python dependencies
└── README.md                           # Comprehensive documentation & setup guide
```

---

## 6. COMPLETE PAYPAL CLIENT IMPLEMENTATION

### `backend/app/services/paypal_orders.py`
```python
import os
import uuid
from typing import Dict, Any, Optional
import httpx

class PayPalEscrowService:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        is_sandbox: bool = True
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = (
            "https://api-m.sandbox.paypal.com"
            if is_sandbox
            else "https://api-m.paypal.com"
        )
        self.http = httpx.AsyncClient(timeout=20.0)

    async def get_access_token(self) -> str:
        """Fetches OAuth 2.0 bearer token using client credentials."""
        res = await self.http.post(
            f"{self.base_url}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
        )
        res.raise_for_status()
        return res.json()["access_token"]

    async def create_escrow_authorization(
        self,
        agreement_id: str,
        amount_usd: float,
        description: str,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates a PayPal Order with intent="AUTHORIZE".
        Funds are locked on the buyer's card/account without immediate capture.
        """
        token = await self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": idempotency_key or str(uuid.uuid4()),
        }
        payload = {
            "intent": "AUTHORIZE",
            "purchase_units": [
                {
                    "reference_id": agreement_id,
                    "description": description[:120],
                    "custom_id": agreement_id,
                    "amount": {
                        "currency_code": "USD",
                        "value": f"{amount_usd:.2f}",
                    },
                }
            ],
            "application_context": {
                "brand_name": "AegisPay Autonomous Escrow",
                "user_action": "CONTINUE",
                "shipping_preference": "NO_SHIPPING",
            },
        }
        res = await self.http.post(
            f"{self.base_url}/v2/checkout/orders",
            json=payload,
            headers=headers
        )
        res.raise_for_status()
        return res.json()

    async def capture_authorization(
        self,
        authorization_id: str,
        amount_usd: float,
        is_final_capture: bool = True
    ) -> Dict[str, Any]:
        """
        Captures previously authorized escrow funds.
        Supports partial captures if an agreement is partially fulfilled.
        """
        token = await self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": str(uuid.uuid4()),
        }
        payload = {
            "amount": {
                "currency_code": "USD",
                "value": f"{amount_usd:.2f}",
            },
            "is_final_capture": is_final_capture,
        }
        res = await self.http.post(
            f"{self.base_url}/v2/payments/authorizations/{authorization_id}/capture",
            json=payload,
            headers=headers
        )
        res.raise_for_status()
        return res.json()

    async def void_authorization(self, authorization_id: str) -> Dict[str, Any]:
        """
        Voids an authorization hold to immediately return 100% of escrowed
        funds back to the buyer if the worker agent breaches SLA terms.
        """
        token = await self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        res = await self.http.post(
            f"{self.base_url}/v2/payments/authorizations/{authorization_id}/void",
            headers=headers
        )
        if res.status_code == 204:
            return {"status": "VOIDED", "authorization_id": authorization_id}
        res.raise_for_status()
        return res.json()
```

---

## 7. TEAM WORK STREAM DIVISION & 12-DAY COMMIT SCHEDULE

With 12+ days remaining, the team will establish a natural, high-velocity commit log:

```
Day 1–2 (Oct 31–Nov 01): Repository Scaffolding, Schemas, & PayPal Sandbox Setup
Day 3–4 (Nov 02–Nov 03): PayPal Orders v2 Escrow & Vaulting Engine
Day 5–6 (Nov 04–Nov 05): Autonomous Arbitration & Sandbox Test Harness
Day 7–8 (Nov 06–Nov 07): Frontend Mission Control & PayPal Smart Buttons
Day 9–10 (Nov 08–Nov 09): Batch Payouts API & Webhook Reversal Engine
Day 11 (Nov 10): End-to-End Stress Testing & Sandbox Verification
Day 12 (Nov 11): 3-Minute Demo Video Recording & Devpost Final Submission
```

### Team Assignments

* **Lead Systems Architect:**
  * Core state machine orchestration and transaction concurrency locks.
  * Integration of PayPal Orders v2 (`AUTHORIZE` / `CAPTURE` / `VOID`) and Batch Payouts API.
  * PostgreSQL double-entry micro-dollar ledger and idempotency enforcement.

* **AI & Arbitration Lead:**
  * Dockerized test execution runner for auditing agent code deliverables.
  * LLM-as-a-judge arbitration prompts for grading semantic equivalence and instruction adherence.
  * AST syntax parsing and security vulnerability scanners.

* **Fintech & Backend Engineer:**
  * PayPal Webhook cryptographic verification (`/v1/notifications/verify-webhook-signature`).
  * Automated dispute resolution and partial capture arithmetic.
  * Pytest test suite covering mock and sandbox transaction edge cases.

* **Mission Control UI/UX Commander:**
  * Next.js 14 dashboard featuring `@paypal/react-paypal-js` button integration.
  * Interactive transaction timeline showing real-time escrow transitions.
  * Code diff viewer with itemized test pass/fail breakdown and demo video recording.

---

## 8. STEP-BY-STEP REPOSITORY INITIALIZATION

Run these commands to initialize the new repository:

```bash
# 1. Create a fresh directory
mkdir aegispay
cd aegispay

# 2. Initialize Git
git init
git branch -M main

# 3. Create initial project scaffolding
mkdir -p backend/app/schemas backend/app/services backend/app/api frontend/src/components tests

# 4. Create base environment and gitignore files
cat << 'EOF' > .gitignore
__pycache__/
*.pyc
.venv/
.env
node_modules/
.next/
dist/
build/
*.log
EOF

# 5. Commit initial foundation
git add .
git commit -m "chore: initial project scaffolding for aegispay agentic commerce engine"

# 6. Add GitHub remote and push (create repo 'aegispay' on GitHub first)
# git remote add origin https://github.com/Code-with-Prabhjot/aegispay.git
# git push -u origin main
```