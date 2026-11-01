"""
backend/app/services/sandbox_check.py
Verifies PayPal Developer Sandbox credentials before going live with them:
mode, OAuth client credentials, and the webhook registration for PAYPAL_WEBHOOK_ID.

Run:  python -m backend.app.services.sandbox_check   (exit code 0 only if every check passes)
Never prints the client ID, secret or access token.
"""

import asyncio
import sys
from dataclasses import dataclass
from typing import List, Optional

import httpx

from backend.app.config import Settings, settings as default_settings
from backend.app.services.orchestrator import (
    AUTHORIZATION_CREATED,
    AUTHORIZATION_VOIDED,
    CAPTURE_COMPLETED,
)
from backend.app.services.paypal_escrow import PayPalEscrowEngine
from backend.app.services.paypal_mock import _is_placeholder

REQUIRED_WEBHOOK_EVENTS = (
    AUTHORIZATION_CREATED,
    CAPTURE_COMPLETED,
    AUTHORIZATION_VOIDED,
    "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
    "PAYMENT.PAYOUTS-ITEM.FAILED",
    "PAYMENT.PAYOUTS-ITEM.UNCLAIMED",
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


async def verify_sandbox_credentials(
    cfg: Settings, http_client: Optional[httpx.AsyncClient] = None
) -> List[CheckResult]:
    """Runs the checks in order, stopping at the first failure later checks depend on."""
    if not cfg.is_sandbox:
        return [CheckResult("mode", False, f"PAYPAL_MODE must be 'sandbox', got '{cfg.PAYPAL_MODE}'")]
    results = [CheckResult("mode", True, "PAYPAL_MODE=sandbox")]

    missing = [n for n in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET") if _is_placeholder(getattr(cfg, n))]
    if missing:
        results.append(CheckResult("credentials", False, f"Missing or placeholder: {', '.join(missing)}"))
        return results
    results.append(CheckResult("credentials", True, "Client ID and secret are set"))

    async with PayPalEscrowEngine(
        cfg.PAYPAL_CLIENT_ID, cfg.PAYPAL_CLIENT_SECRET, webhook_id=cfg.PAYPAL_WEBHOOK_ID,
        is_sandbox=True, http_client=http_client,
    ) as engine:
        try:
            await engine.get_access_token()
        except httpx.HTTPStatusError as exc:
            results.append(CheckResult("oauth", False, f"PayPal rejected the credentials (HTTP {exc.response.status_code})"))
            return results
        except httpx.HTTPError as exc:
            results.append(CheckResult("oauth", False, f"Could not reach PayPal ({type(exc).__name__})"))
            return results
        results.append(CheckResult("oauth", True, "Sandbox client credentials accepted"))

        if _is_placeholder(cfg.PAYPAL_WEBHOOK_ID):
            results.append(CheckResult("webhook", False, "PAYPAL_WEBHOOK_ID is missing or a placeholder"))
            return results
        try:
            registration = await engine.get_webhook_registration()
        except httpx.HTTPError as exc:
            results.append(CheckResult("webhook", False, f"Could not fetch webhook ({type(exc).__name__})"))
            return results

    if registration is None:
        results.append(CheckResult("webhook", False, "PAYPAL_WEBHOOK_ID is not registered for this app"))
        return results
    subscribed = {e.get("name") for e in registration.get("event_types", [])}
    unsubscribed = [] if "*" in subscribed else [e for e in REQUIRED_WEBHOOK_EVENTS if e not in subscribed]
    if unsubscribed:
        results.append(CheckResult("webhook", False, f"Not subscribed to: {', '.join(unsubscribed)}"))
    else:
        results.append(CheckResult("webhook", True, f"Registered at {registration.get('url')}"))
    return results


def main() -> int:
    results = asyncio.run(verify_sandbox_credentials(default_settings))
    for r in results:
        print(f"[{'PASS' if r.ok else 'FAIL'}] {r.name}: {r.detail}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
