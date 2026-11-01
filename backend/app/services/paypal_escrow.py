"""
backend/app/services/paypal_escrow.py
Production-grade PayPal Escrow & Payout Service.
Implements:
- Deterministic UUIDv5 idempotency key derivation (Fix #2)
- Cent-floor micro-dollar to USD conversion
- Exact piecewise linear partial capture mathematical formula (Fix #3)
- Non-crashing HTTP 422 / DECLINED capture error handling (Fix #1)
- Async PayPal REST API operations (OAuth, Orders v2, Authorizations, Batch Payouts)
"""

from decimal import Decimal, ROUND_DOWN
import time
from typing import Any, Dict, Optional
import uuid

import httpx

from backend.app.schemas import SettlementSplitResult

AEGIS_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def derive_idempotency_key(agreement_id: str, action: str, step: int = 1) -> str:
    """
    Deterministically derives a PayPal-Request-Id from the agreement_id and action.
    Prevents double-capture or double-payout on network retries.
    Uses UUIDv5 with the DNS namespace.
    """
    seed = f"aegispay:{agreement_id}:{action}:v{step}"
    return str(uuid.uuid5(AEGIS_NAMESPACE, seed))


def micros_to_usd_str(micros: int) -> str:
    """
    Converts integer micro-dollars to a 2-decimal USD string, rounding down (cent floor).
    $1.00 USD = 1,000,000 micro-dollars.
    """
    if micros < 0:
        raise ValueError("Micro-dollar amount cannot be negative.")
    dollars = (Decimal(micros) / Decimal(1_000_000)).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    return f"{dollars:.2f}"


def usd_amount_to_micros(amount: Any) -> Optional[int]:
    """
    Parses a PayPal {"currency_code": "USD", "value": "10.50"} amount into integer micro-dollars.
    Returns None for a missing, malformed or non-USD amount.
    """
    if not isinstance(amount, dict) or amount.get("currency_code") != "USD":
        return None
    try:
        return int(Decimal(str(amount.get("value"))) * 1_000_000)
    except (ArithmeticError, ValueError):
        return None


def compute_settlement_split_micros(
    agreed_budget_micros: int,
    composite_score: float,
    ast_security_passed: bool = True,
    platform_fee_bps: int = 500,  # 5.00% protocol fee
    paypal_payout_fee_micros: int = 250_000,  # $0.25 Sandbox Payout rail fee
) -> Dict[str, Any]:
    """
    Computes the exact deterministic financial split in integer micro-dollars.
    - Score >= 0.90: 100% capture of agreed budget (FULL_CAPTURE).
    - 0.60 <= Score < 0.90: Linear pro-rata partial capture between 50% and 90% (PARTIAL_CAPTURE).
    - Score < 0.60 or AST security failure: 0% capture (VOID_AUTHORIZATION).
    """
    if not ast_security_passed or composite_score < 0.60:
        return {
            "decision": "VOID_AUTHORIZATION",
            "capture_micros": 0,
            "buyer_released_micros": agreed_budget_micros,
            "platform_fee_micros": 0,
            "paypal_rail_fee_micros": 0,
            "worker_net_payout_micros": 0,
        }

    if composite_score >= 0.90:
        capture_micros = (agreed_budget_micros // 10_000) * 10_000
        decision = "FULL_CAPTURE"
    else:
        # Piecewise linear factor in [0.50, 0.90)
        # Factor = 0.50 + 0.40 * ((S - 0.60) / 0.30)
        normalized = (composite_score - 0.60) / 0.30
        factor = 0.50 + (0.40 * normalized)
        raw_micros = int(agreed_budget_micros * factor)
        # Round down to nearest whole cent (10,000 micros)
        capture_micros = (raw_micros // 10_000) * 10_000
        decision = "PARTIAL_CAPTURE"

    buyer_released_micros = agreed_budget_micros - capture_micros
    raw_platform_fee = (capture_micros * platform_fee_bps) // 10_000
    platform_fee_micros = (raw_platform_fee // 10_000) * 10_000

    worker_net_payout_micros = max(
        0, capture_micros - platform_fee_micros - paypal_payout_fee_micros
    )

    return {
        "decision": decision,
        "capture_micros": capture_micros,
        "buyer_released_micros": buyer_released_micros,
        "platform_fee_micros": platform_fee_micros,
        "paypal_rail_fee_micros": paypal_payout_fee_micros if worker_net_payout_micros > 0 else 0,
        "worker_net_payout_micros": worker_net_payout_micros,
    }


def compute_settlement_split(
    agreed_budget_micros: int,
    composite_score: float,
    ast_security_passed: bool = True,
    platform_fee_bps: int = 500,
    paypal_payout_fee_micros: int = 250_000,
) -> SettlementSplitResult:
    """Type-safe wrapper returning SettlementSplitResult schema."""
    raw = compute_settlement_split_micros(
        agreed_budget_micros=agreed_budget_micros,
        composite_score=composite_score,
        ast_security_passed=ast_security_passed,
        platform_fee_bps=platform_fee_bps,
        paypal_payout_fee_micros=paypal_payout_fee_micros,
    )
    return SettlementSplitResult(**raw)


class PayPalEscrowEngine:
    """
    Production-grade asynchronous PayPal Escrow & Payout Client.
    Manages OAuth2 token caching, Orders v2 Authorize holds, Captures, Voids,
    and Batch Payouts with strict idempotency and error recovery.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        webhook_id: str = "",
        is_sandbox: bool = True,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.webhook_id = webhook_id
        self.is_sandbox = is_sandbox
        self.base_url = (
            "https://api-m.sandbox.paypal.com"
            if is_sandbox
            else "https://api-m.paypal.com"
        )
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._custom_http = http_client is not None
        self.http = http_client or httpx.AsyncClient(timeout=20.0)

    async def get_access_token(self) -> str:
        """
        Retrieves an OAuth2 Bearer token from PayPal, utilizing in-memory cache
        with an early-refresh buffer of 60 seconds.
        """
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        res = await self.http.post(
            f"{self.base_url}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
        )
        res.raise_for_status()
        data = res.json()
        self._access_token = data["access_token"]
        self._token_expires_at = now + int(data.get("expires_in", 3600))
        return self._access_token

    async def create_escrow_authorization_order(
        self,
        agreement_id: str,
        amount_micros: int,
        description: str,
        enable_vault: bool = True,
    ) -> Dict[str, Any]:
        """
        Creates a PayPal v2 Order with intent='AUTHORIZE'.
        Uses a deterministic PayPal-Request-Id derived from agreement_id.
        """
        token = await self.get_access_token()
        req_id = derive_idempotency_key(agreement_id, "create_auth_order")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": req_id,
        }
        payload: Dict[str, Any] = {
            "intent": "AUTHORIZE",
            "purchase_units": [
                {
                    "reference_id": agreement_id,
                    "custom_id": agreement_id,
                    "description": description[:120],
                    "amount": {
                        "currency_code": "USD",
                        "value": micros_to_usd_str(amount_micros),
                    },
                }
            ],
            "application_context": {
                "brand_name": "AegisPay Autonomous Agent Escrow (Sandbox)",
                "shipping_preference": "NO_SHIPPING",
                "user_action": "CONTINUE",
            },
        }
        if enable_vault:
            payload["payment_source"] = {
                "paypal": {
                    "attributes": {
                        "vault": {
                            "store_in_vault": "ON_SUCCESS",
                            "usage_type": "MERCHANT",
                        }
                    }
                }
            }

        res = await self.http.post(
            f"{self.base_url}/v2/checkout/orders", json=payload, headers=headers
        )
        res.raise_for_status()
        return res.json()

    async def capture_authorization(
        self,
        agreement_id: str,
        authorization_id: str,
        capture_micros: int,
        is_final_capture: bool = True,
    ) -> Dict[str, Any]:
        """
        Captures full or partial funds from an active authorization hold.
        - Deterministic PayPal-Request-Id prevents double-capture on retry.
        - Explicitly handles HTTP 422 / DECLINED states without crashing.
        """
        token = await self.get_access_token()
        req_id = derive_idempotency_key(agreement_id, "capture_auth")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": req_id,
        }
        payload = {
            "amount": {
                "currency_code": "USD",
                "value": micros_to_usd_str(capture_micros),
            },
            "final_capture": is_final_capture,
            "note_to_payer": f"AegisPay Verified SLA Settlement (Agreement {agreement_id})"[:255],
        }
        res = await self.http.post(
            f"{self.base_url}/v2/payments/authorizations/{authorization_id}/capture",
            json=payload,
            headers=headers,
        )

        if res.status_code in (400, 422):
            try:
                err_body = res.json()
            except Exception:
                err_body = {"raw_text": res.text}
            return {
                "fsm_transition": "CAPTURE_DECLINED",
                "ok": False,
                "paypal_status": "DECLINED",
                "error_details": err_body,
            }

        res.raise_for_status()
        data = res.json()
        pp_status = data.get("status", "UNKNOWN")
        if pp_status in ("DECLINED", "FAILED"):
            return {
                "fsm_transition": "CAPTURE_DECLINED",
                "ok": False,
                "paypal_status": pp_status,
                "capture_data": data,
            }

        return {
            "fsm_transition": "PAYPAL_CAPTURED",
            "ok": True,
            "paypal_status": pp_status,
            "capture_id": data.get("id"),
            "capture_data": data,
        }

    async def void_authorization(
        self, agreement_id: str, authorization_id: str
    ) -> Dict[str, Any]:
        """
        Voids an authorization hold when score < 0.60 or AST security fails,
        immediately releasing 100% of held funds back to the buyer.
        """
        token = await self.get_access_token()
        req_id = derive_idempotency_key(agreement_id, "void_auth")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": req_id,
        }
        res = await self.http.post(
            f"{self.base_url}/v2/payments/authorizations/{authorization_id}/void",
            headers=headers,
        )
        if res.status_code == 204:
            return {
                "fsm_transition": "ESCROW_VOIDED_REFUNDED",
                "ok": True,
                "authorization_id": authorization_id,
                "paypal_status": "VOIDED",
            }
        res.raise_for_status()
        return res.json()

    async def dispatch_worker_payout(
        self,
        agreement_id: str,
        worker_paypal_email: str,
        net_payout_micros: int,
        verification_score: float,
        attempt: int = 1,
    ) -> Dict[str, Any]:
        """
        Disburses net verified earnings to the worker agent via PayPal Batch Payouts API.
        Uses deterministic sender_batch_id and sender_item_id to prevent double disbursement.
        `attempt` (1-based) is folded into the idempotency keys so that a retry after
        PAYOUT_FAILED_REVERSED uses a fresh sender_batch_id instead of replaying the failed one;
        repeating the same attempt still yields identical keys.
        """
        if net_payout_micros < 10_000:
            return {
                "status": "SKIPPED_BELOW_ONE_CENT",
                "ok": False,
                "fsm_transition": "SETTLED_COMPLETE",
            }

        token = await self.get_access_token()
        batch_id = derive_idempotency_key(agreement_id, "payout_batch", step=attempt)
        item_id = derive_idempotency_key(agreement_id, "payout_item", step=attempt)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": batch_id,
        }
        payload = {
            "sender_batch_header": {
                "sender_batch_id": batch_id,
                "email_subject": f"AegisPay Sandbox Settlement ({agreement_id})",
                "email_message": (
                    f"Your autonomous worker agent passed SLA verification "
                    f"(Score: {verification_score:.2f}). Sandbox payout enclosed."
                ),
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "amount": {
                        "value": micros_to_usd_str(net_payout_micros),
                        "currency": "USD",
                    },
                    "receiver": worker_paypal_email,
                    "note": f"AegisPay Agreement {agreement_id} | Score: {verification_score:.2f}",
                    "sender_item_id": item_id,
                }
            ],
        }
        res = await self.http.post(
            f"{self.base_url}/v1/payments/payouts", json=payload, headers=headers
        )
        res.raise_for_status()
        return {
            "ok": True,
            "fsm_transition": "PAYOUT_DISPATCHED",
            "payout_response": res.json(),
        }

    async def verify_webhook_signature(
        self,
        transmission_id: str,
        transmission_time: str,
        cert_url: str,
        auth_algo: str,
        transmission_sig: str,
        webhook_event: Dict[str, Any],
    ) -> bool:
        """
        Verifies an incoming PayPal webhook event signature via PayPal's API.
        Requests missing a signature header are rejected without calling PayPal.
        """
        if not all((transmission_id, transmission_time, cert_url, auth_algo, transmission_sig)):
            return False
        token = await self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "transmission_id": transmission_id,
            "transmission_time": transmission_time,
            "cert_url": cert_url,
            "auth_algo": auth_algo,
            "transmission_sig": transmission_sig,
            "webhook_id": self.webhook_id,
            "webhook_event": webhook_event,
        }
        res = await self.http.post(
            f"{self.base_url}/v1/notifications/verify-webhook-signature",
            json=payload,
            headers=headers,
        )
        if res.status_code == 200:
            data = res.json()
            return data.get("verification_status") == "SUCCESS"
        return False

    async def get_webhook_registration(self) -> Optional[Dict[str, Any]]:
        """Fetches the configured webhook's registration, or None if PayPal doesn't know the ID."""
        token = await self.get_access_token()
        res = await self.http.get(
            f"{self.base_url}/v1/notifications/webhooks/{self.webhook_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if res.status_code == 404:
            return None
        res.raise_for_status()
        return res.json()

    async def close(self) -> None:
        """Closes the underlying HTTP client session if internally created."""
        if not self._custom_http:
            await self.http.aclose()

    async def __aenter__(self) -> "PayPalEscrowEngine":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
