# AegisPay Postman collection

`AegisPay.postman_collection.json` (Collection v2.1) covers every `/api/v1` route:
health, contracts (create, list, get, submit, check-expiry, payout/retry), the three demo
scenarios, ledger and the PayPal webhook. Each request has simple `pm.test` status assertions.

## Use

1. Start the backend in mock mode: `PAYPAL_MODE=mock uvicorn backend.app.main:app --port 8000`
2. Import the collection (Postman: Import, or `postman collection run postman/AegisPay.postman_collection.json`).
3. Variables: `baseUrl` (default `http://localhost:8000/api/v1`) and `agreementId`
   (set automatically by "Create contract").
4. Run top to bottom: create, get, submit, then scenarios and ledger.

Notes: demo scenarios need `PAYPAL_MODE=mock`. Scenario names are case-insensitive.
"Retry payout" returns 409 unless the contract is in `PAYOUT_FAILED_REVERSED`
(try `payout_outcome=failed` on a scenario first). Webhook signature checks are accepted in mock mode.
