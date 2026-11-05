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
| 9 | Arbiter | Docker `--network none` runner + `sandbox/Dockerfile`, falls back to subprocess when Docker unavailable | ✅ | Claude (Sonnet agent) | wip/arbiter-sandbox | Docker path unit-tested with mocks; not run against a live daemon |
| 10 | Arbiter | LLM semantic judge (pluggable, falls back to heuristic rubric when no API key) | ✅ | Claude (Sonnet agent) | wip/arbiter-sandbox | needs API key for live use |
| 11 | Frontend | Next.js 14 dashboard scaffold, AG Grid ledger, FSM timeline, 3-scenario demo buttons | ✅ | Claude (Sonnet agent) | wip/frontend | Verified against the real backend contract (src/lib/api.ts normalises `transitions` to `history`); build + lint pass |
| 12 | PayPal | Live sandbox wiring: create sandbox accounts, fill `.env`, register webhook, end-to-end run | 🔒 | | | Needs PayPal developer login |
| 13 | PayPal | Postman collection export for the API | ✅ | Claude (Sonnet agent) | feat/backend-api-and-sandbox | `postman/AegisPay.postman_collection.json` + README |
| 17 | Integration | Integration: arbiter factories wired, API contract reconciled, payout retry idempotency fix | ✅ | Claude (Sonnet agent) | feat/backend-api-and-sandbox | Orchestrator uses get_execution_runner/get_semantic_judge; payout keys vary per attempt; 123 tests |
| 18 | PayPal | Webhook signature listener (`api/routes_webhooks.py`): authorization created/voided, capture completed, payout item events; `PAYPAL_WEBHOOK_MOCK_BYPASS` | ✅ | Raksha Sendhil | feat/paypal-webhooks | Tested against stubs; no live PayPal delivery yet (task 12) |
| 19 | PayPal | Sandbox credential check (`python -m backend.app.services.sandbox_check`) | 🔒 | Raksha Sendhil | feat/paypal-webhooks | Code done and tested against stubs; needs real sandbox credentials to run |
| 14 | Frontend | `@paypal/react-paypal-js` Smart Payment Buttons | 🔒 | | | Needs sandbox client ID |
| 15 | Sponsor | APIMatic schema validation (`backend/apimatic_validation/`) | 🔒 | Claude (Sonnet agent) | feat/backend-api-and-sandbox | Specs + drift tests merged; all 3 drifts fixed (0 xfail). APIMatic CLI run still needs an API key (CLI demands auth even for `api validate`) |
| 16 | Release | Vercel deploy, repo public, Devpost write-up, demo video | 🔒 | | | Needs account access |

## Changelog
- 2026-11-01 — Tracker created. Branch `feat/backend-api-and-sandbox` started for tasks 5–11.
- 2026-11-01 — Tasks 9–10 done on `wip/arbiter-sandbox`: DockerExecutionRunner + get_execution_runner (SANDBOX_MODE), sandbox/Dockerfile, pluggable SemanticJudge (Heuristic/Claude).
- 2026-11-01 — Frontend (task 11) built on `wip/frontend`: Mission Control, AG Grid ledger, FSM timeline, double-entry panel, offline demo-data mode.
- 2026-11-01 — Tasks 5-8 (branch `wip/backend-api`): FSM engine (`services/fsm.py`, 72h TTL, capture-declined, payout failed/retry), SQLite repository + Postgres-compatible `backend/db/schema.sql` (`app/db.py`; ledger persists/reloads), offline `MockPayPalEscrowEngine` + `get_paypal_engine()` (`PAYPAL_MODE=mock`), orchestrator and FastAPI app under `/api/v1`. Run: `cp .env.example .env && uvicorn backend.app.main:app --reload`, then `POST /api/v1/demo/scenarios/clean_pass`. 91 tests pass.
- 2026-11-01 — Integration: arbiter factories wired into the orchestrator, frontend api layer reconciled with the real API (`transitions` -> `history`, lowercase scenario names, submit body typed), real-engine payout retry now uses per-attempt sender_batch_id/item_id, Postman collection added (task 13). Mock-mode smoke of all 3 scenarios passes; frontend build + lint pass. 123 tests pass.
- 2026-11-01 — Task 15 (branch `wip/apimatic`): PayPal subset + AegisPay OpenAPI specs, `export_openapi.py`, `tests/test_apimatic_contract.py` (3 strict xfails = real drift, see test reasons), `requirements-dev.txt`, APIMatic CI workflow. Real drift: `is_final_capture` should be `final_capture`.
- 2026-11-01 — Merged `wip/apimatic` and fixed the 3 contract drifts: capture body `is_final_capture` -> `final_capture` (+ `note_to_payer` truncated to 255), mock payout 201 response drops `items`, webhook `transaction_status` SUCCEEDED -> SUCCESS (spec enum corrected). Removed all xfails; 144 pass with dev deps, 123 pass + contract tests skipped without.
- 2026-11-01 — Tasks 18-19 (branch `feat/paypal-webhooks`): moved the webhook route from `main.py` to `api/routes_webhooks.py`; added the authorization created/voided and capture completed handlers (each recovers a lost synchronous response, and each is a no-op when that response already landed); added `PAYPAL_WEBHOOK_MOCK_BYPASS` (rejected in live mode); the listener fails closed if a non-mock mode has fallen back to the mock engine; added the sandbox credential check.
- 2026-11-05 — Module 3 Sec 5.3 & Day 11 Invariants (PS): Automated 72-Hour Re-Authorization Sweeper (`backend/app/services/sweeper.py`) with PayPal Vaulting v3 token refresh, double-entry re-balancing, cryptographic hash chain anchoring, and `/api/v1/sweeper/run` & `/api/v1/sweeper/status` FastAPI endpoints. High-concurrency financial invariant stress test suite (`tests/test_concurrency_stress.py`) verifying balance conservation ($\sum\text{Debits} \equiv \sum\text{Credits}$) across 50 concurrent settlements, strict idempotency storm under 20 duplicate webhooks, sweeper near-expiry refresh/void, and circuit breaker trip to `VAULT_LOCKED_CIRCUIT_BREAKER`. 311 tests passing with zero regressions.

## Known gaps
- `DAILY_SPEND_LIMIT_MICROS` is not enforced anywhere.
- Only the sqlite driver is implemented (`postgresql://` URLs raise), although `schema.sql` is Postgres-compatible.
- The real PayPal flow needs a buyer-approval step (Orders v2 authorize); no live sandbox test has been run.
- Capture-declined leaves the authorization hold unreleased (design decision pending).
- Docker runner and Claude judge are only tested with mocks.
- Webhook signature verification has only run against stubs. The parsed event is re-serialized for `verify-webhook-signature`, so confirm with a real sandbox delivery that PayPal accepts it.
- The frontend has not been exercised in a browser (build/lint only).
- `npm audit` reports warnings for frontend dependencies.
