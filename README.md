# AegisPay: Autonomous Agentic Commerce Clearinghouse & Trustless Escrow Engine

[![PayPal Developer](https://img.shields.io/badge/Payments%20Engine-PayPal%20Orders%20v2%20%26%20Vault-003087?style=flat&logo=paypal)](https://developer.paypal.com)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Track](https://img.shields.io/badge/Hackathon-PayPal%20AI%20Hackathon%202026-00457C)](https://devpost.com)

> Autonomous, intent-governed escrow and real-time micro-dollar batch payouts for multi-agent workflows. Powered by PayPal Orders v2, Vaulting, and Webhooks.

---

## Architecture Overview
AegisPay enables autonomous AI agents to initiate, hold, arbitrate, and disburse escrow funds conditionally based on cryptographic proof-of-work and dynamic verification gates.

## Running the frontend

```bash
cd frontend
npm install
cp .env.example .env.local   # optional; NEXT_PUBLIC_API_BASE defaults to http://localhost:8000/api/v1
npm run dev                  # http://localhost:3000
```

The dashboard polls the backend every 2s. If the API is unreachable it shows a "Backend offline" banner with a
"Use demo data" button that switches to deterministic fixtures (`frontend/src/lib/fixtures.ts`). All API calls live in
`frontend/src/lib/api.ts`. Production check: `npm run build && npm run lint`.
