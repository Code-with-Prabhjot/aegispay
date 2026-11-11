"""
backend/app/api/routes_webhooks.py
PayPal webhook listener: POST /api/v1/webhooks/paypal.

Authenticates each delivery with PayPal's /v1/notifications/verify-webhook-signature (after a cheap
header pre-check in backend.app.services.paypal_webhooks), then hands it to
EscrowOrchestrator.handle_webhook for the FSM and ledger updates. Any non-2xx response makes
PayPal redeliver, so ignored events still return 200 and only transient failures return 5xx.
"""

from typing import Any, Dict

import httpx
from fastapi import APIRouter, HTTPException, Request

from backend.app.schemas import WebhookResponse
from backend.app.services.orchestrator import UpstreamError
from backend.app.services.paypal_webhooks import check_webhook_headers

router = APIRouter(prefix="/webhooks")


async def _verify_signature(request: Request, event: Dict[str, Any]) -> None:
    orch = request.app.state.orchestrator
    if orch.settings.PAYPAL_WEBHOOK_MOCK_BYPASS:
        return
    engine = orch.engine
    # get_paypal_engine falls back to the always-accepting mock when credentials are missing.
    if getattr(engine, "is_mock", False) and not orch.settings.is_mock:
        raise HTTPException(503, "PayPal credentials are not configured")
    if not engine.webhook_id:
        raise HTTPException(503, "PAYPAL_WEBHOOK_ID is not configured")
    h = request.headers

    # Reject malformed or obviously spoofed requests before spending a PayPal call on them.
    if not getattr(engine, "is_mock", False) and not check_webhook_headers(dict(h)):
        raise HTTPException(401, "Webhook signature verification failed")

    try:
        verified = await engine.verify_webhook_signature(
            transmission_id=h.get("paypal-transmission-id", ""),
            transmission_time=h.get("paypal-transmission-time", ""),
            cert_url=h.get("paypal-cert-url", ""),
            auth_algo=h.get("paypal-auth-algo", ""),
            transmission_sig=h.get("paypal-transmission-sig", ""),
            webhook_event=event,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(503, "PayPal signature verification unavailable") from exc
    if not verified:
        raise HTTPException(401, "Webhook signature verification failed")


@router.post("/paypal", tags=["Webhooks & System"], response_model=WebhookResponse)
async def paypal_webhook(request: Request) -> Dict[str, Any]:
    try:
        event = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")
    if (
        not isinstance(event, dict)
        or not isinstance(event.get("id"), str)
        or not event["id"]
        or not event.get("event_type")
        or not isinstance(event.get("resource") or {}, dict)
    ):
        raise HTTPException(400, "Webhook event requires 'id' and 'event_type'")
    await _verify_signature(request, event)
    try:
        return await request.app.state.orchestrator.handle_webhook(event)
    except UpstreamError as exc:
        raise HTTPException(502, str(exc))
