"""
backend/app/services/paypal_mock.py
Offline, deterministic stand-in for PayPalEscrowEngine so the whole demo runs with no
PayPal credentials. Same async method signatures; fake IDs are derived from the same
UUIDv5 idempotency keys the real engine sends, so identical inputs yield identical IDs.

Test hooks: add an agreement_id to `decline_captures_for` to simulate a PayPal 422 decline.
"""

import hashlib
from typing import Any, Dict, Optional, Set

from backend.app.config import Settings
from backend.app.services.paypal_escrow import (
    PayPalEscrowEngine,
    derive_idempotency_key,
    micros_to_usd_str,
)

_PLACEHOLDER_MARKERS = ("your_", "_here", "changeme", "placeholder", "example")


def _fake_id(prefix: str, key: str, length: int = 17) -> str:
    digest = hashlib.sha256(key.encode()).hexdigest().upper()
    return f"{prefix}-{digest[:length]}"


class MockPayPalEscrowEngine:
    """Drop-in replacement for PayPalEscrowEngine that never touches the network."""

    is_mock = True

    def __init__(self, webhook_id: str = "MOCK-WEBHOOK", **_: Any) -> None:
        self.webhook_id = webhook_id
        self.decline_captures_for: Set[str] = set()
        self.accept_webhooks = True

    async def get_access_token(self) -> str:
        return "MOCK-ACCESS-TOKEN"

    async def create_escrow_authorization_order(
        self,
        agreement_id: str,
        amount_micros: int,
        description: str,
        enable_vault: bool = True,
    ) -> Dict[str, Any]:
        """Returns an order shaped like PayPal's response *after* buyer approval + authorize."""
        key = derive_idempotency_key(agreement_id, "create_auth_order")
        value = micros_to_usd_str(amount_micros)
        amount = {"currency_code": "USD", "value": value}
        return {
            "id": _fake_id("MOCKORDER", key),
            "intent": "AUTHORIZE",
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "reference_id": agreement_id,
                    "custom_id": agreement_id,
                    "description": description[:120],
                    "amount": amount,
                    "payments": {
                        "authorizations": [
                            {
                                "id": _fake_id("MOCKAUTH", key),
                                "status": "CREATED",
                                "amount": amount,
                            }
                        ]
                    },
                }
            ],
            "links": [],
        }

    async def capture_authorization(
        self,
        agreement_id: str,
        authorization_id: str,
        capture_micros: int,
        is_final_capture: bool = True,
    ) -> Dict[str, Any]:
        if agreement_id in self.decline_captures_for:
            return {
                "fsm_transition": "CAPTURE_DECLINED",
                "ok": False,
                "paypal_status": "DECLINED",
                "error_details": {
                    "name": "UNPROCESSABLE_ENTITY",
                    "details": [{"issue": "INSTRUMENT_DECLINED"}],
                    "debug_id": _fake_id("MOCKDBG", agreement_id, 12),
                },
            }
        key = derive_idempotency_key(agreement_id, "capture_auth")
        capture_id = _fake_id("MOCKCAP", key)
        data = {
            "id": capture_id,
            "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": micros_to_usd_str(capture_micros)},
            "final_capture": is_final_capture,
        }
        return {
            "fsm_transition": "PAYPAL_CAPTURED",
            "ok": True,
            "paypal_status": "COMPLETED",
            "capture_id": capture_id,
            "capture_data": data,
        }

    async def void_authorization(
        self, agreement_id: str, authorization_id: str
    ) -> Dict[str, Any]:
        return {
            "fsm_transition": "ESCROW_VOIDED_REFUNDED",
            "ok": True,
            "authorization_id": authorization_id,
            "paypal_status": "VOIDED",
        }

    async def dispatch_worker_payout(
        self,
        agreement_id: str,
        worker_paypal_email: str,
        net_payout_micros: int,
        verification_score: float,
    ) -> Dict[str, Any]:
        if net_payout_micros < 10_000:
            return {
                "status": "SKIPPED_BELOW_ONE_CENT",
                "ok": False,
                "fsm_transition": "SETTLED_COMPLETE",
            }
        batch_key = derive_idempotency_key(agreement_id, "payout_batch")
        item_key = derive_idempotency_key(agreement_id, "payout_item")
        return {
            "ok": True,
            "fsm_transition": "PAYOUT_DISPATCHED",
            "payout_response": {
                "batch_header": {
                    "payout_batch_id": _fake_id("MOCKPAYOUT", batch_key, 13),
                    "batch_status": "PENDING",
                    "sender_batch_header": {"sender_batch_id": batch_key},
                },
                "items": [
                    {
                        "payout_item_id": _fake_id("MOCKITEM", item_key, 13),
                        "receiver": worker_paypal_email,
                        "amount": {
                            "value": micros_to_usd_str(net_payout_micros),
                            "currency": "USD",
                        },
                        "sender_item_id": item_key,
                    }
                ],
                "links": [],
            },
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
        return self.accept_webhooks

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> "MockPayPalEscrowEngine":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


def _is_placeholder(value: Optional[str]) -> bool:
    v = (value or "").strip().lower()
    return not v or any(m in v for m in _PLACEHOLDER_MARKERS)


def get_paypal_engine(settings: Settings):
    """Mock engine when PAYPAL_MODE=mock or credentials are missing/placeholder; else real."""
    if (
        settings.is_mock
        or _is_placeholder(settings.PAYPAL_CLIENT_ID)
        or _is_placeholder(settings.PAYPAL_CLIENT_SECRET)
    ):
        return MockPayPalEscrowEngine(webhook_id=settings.PAYPAL_WEBHOOK_ID or "MOCK-WEBHOOK")
    return PayPalEscrowEngine(
        client_id=settings.PAYPAL_CLIENT_ID,
        client_secret=settings.PAYPAL_CLIENT_SECRET,
        webhook_id=settings.PAYPAL_WEBHOOK_ID,
        is_sandbox=settings.is_sandbox,
    )
