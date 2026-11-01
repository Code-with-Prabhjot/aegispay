# AegisPay: Autonomous Agentic Commerce Clearinghouse & Trustless Escrow Engine

[![PayPal Developer](https://img.shields.io/badge/Payments%20Engine-PayPal%20Orders%20v2%20%26%20Vault-003087?style=flat&logo=paypal)](https://developer.paypal.com)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Track](https://img.shields.io/badge/Hackathon-PayPal%20AI%20Hackathon%202026-00457C)](https://devpost.com)

> Autonomous, intent-governed escrow and real-time micro-dollar batch payouts for multi-agent workflows. Powered by PayPal Orders v2, Vaulting, and Webhooks.

---

## Architecture Overview
AegisPay enables autonomous AI agents to initiate, hold, arbitrate, and disburse escrow funds conditionally based on cryptographic proof-of-work and dynamic verification gates.

## Running the backend

```bash
uv venv .venv && uv pip install -r requirements.txt   # or python -m venv + pip
cp .env.example .env                                    # PAYPAL_MODE=mock runs fully offline
.venv/bin/uvicorn backend.app.main:app --reload         # http://localhost:8000/docs
.venv/bin/python -m pytest -q
```

State is stored in SQLite (`DATABASE_URL`, default `sqlite:///./aegispay.db`; the Postgres-compatible DDL is `backend/db/schema.sql`).
Try the demo without PayPal credentials: `curl -X POST localhost:8000/api/v1/demo/scenarios/clean_pass` (also `partial_defect`, `malicious_injection`), then `GET /api/v1/ledger`.
Routes (`/api/v1`): `GET /health`, `POST|GET /contracts`, `GET /contracts/{id}`, `POST /contracts/{id}/submit`, `POST /contracts/{id}/check-expiry`, `POST /contracts/{id}/payout/retry`, `POST /demo/scenarios/{name}`, `GET /ledger`, `POST /webhooks/paypal`.

