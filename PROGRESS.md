# AegisPay — Progress Tracker

Shared status board for everyone contributing. **Update this file in the same PR as your work.**
Rules: never push to `main` directly — work on a branch and open a PR. Claim a task by putting your name in *Owner* before starting.

Status legend: ✅ done · 🚧 in progress · ⬜ not started · 🔒 needs a human (credentials, accounts, decisions)

## Roadmap (from the architecture dossier, Nov 1–12, 2026)

| # | Area | Task | Status | Owner | Branch / PR | Notes |
|---|------|------|--------|-------|-------------|-------|
| 1 | Core | Pydantic schemas, config, FSM state enum | ✅ | — | main (19c7640) | |
| 2 | Core | PayPal escrow client (authorize/capture/void/payout/webhook verify), UUIDv5 idempotency, settlement math | ✅ | — | main (19c7640) | Never run against live sandbox yet |
| 3 | Core | Double-entry ledger engine | ✅ | — | main (19c7640) | In-memory only |
| 4 | Arbiter | AST security scanner, subprocess pytest runner, 3 scenario presets | ✅ | — | main (a08299e) | |
| 5 | Core | FSM transition engine (legal transitions, 72h auth expiry, capture-declined, payout-failed paths) | ✅ | Claude (Sonnet agent) | wip/backend-api | |
| 6 | Core | Persistence: `schema.sql` (Postgres-compatible) + repository layer, SQLite for dev/tests | ✅ | Claude (Sonnet agent) | wip/backend-api | |
| 7 | API | FastAPI app: contracts, arbitrate, settle, ledger, `/api/v1/webhooks/paypal`, health | ✅ | Claude (Sonnet agent) | wip/backend-api | |
| 8 | API | Offline mock PayPal mode (deterministic fake IDs) so demo runs without credentials | ✅ | Claude (Sonnet agent) | wip/backend-api | |
| 9 | Arbiter | Docker `--network none` runner + `sandbox/Dockerfile`, falls back to subprocess when Docker unavailable | 🚧 | Claude (agent) | feat/backend-api-and-sandbox | Not verified against a live Docker daemon |
| 10 | Arbiter | LLM semantic judge (pluggable, falls back to heuristic rubric when no API key) | 🚧 | Claude (agent) | feat/backend-api-and-sandbox | Needs an API key to use for real |
| 11 | Frontend | Next.js 14 dashboard scaffold, AG Grid ledger, FSM timeline, 3-scenario demo buttons | 🚧 | Claude (agent) | feat/backend-api-and-sandbox | |
| 12 | PayPal | Live sandbox wiring: create sandbox accounts, fill `.env`, register webhook, end-to-end run | 🔒 | | | Needs PayPal developer login |
| 13 | PayPal | Postman collection export for the API | ⬜ | | | |
| 14 | Frontend | `@paypal/react-paypal-js` Smart Payment Buttons | 🔒 | | | Needs sandbox client ID |
| 15 | Sponsor | APIMatic schema validation (`backend/apimatic_validation/`) | ⬜ | | | |
| 16 | Release | Vercel deploy, repo public, Devpost write-up, demo video | 🔒 | | | Needs account access |

## Changelog
- 2026-11-01 — Tracker created. Branch `feat/backend-api-and-sandbox` started for tasks 5–11.
- 2026-11-01 — Tasks 5-8 (branch `wip/backend-api`): FSM engine (`services/fsm.py`, 72h TTL, capture-declined, payout failed/retry), SQLite repository + Postgres-compatible `backend/db/schema.sql` (`app/db.py`; ledger persists/reloads), offline `MockPayPalEscrowEngine` + `get_paypal_engine()` (`PAYPAL_MODE=mock`), orchestrator and FastAPI app under `/api/v1`. Run: `cp .env.example .env && uvicorn backend.app.main:app --reload`, then `POST /api/v1/demo/scenarios/clean_pass`. 91 tests pass.
