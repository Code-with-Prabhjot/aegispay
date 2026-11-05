"""
backend/app/services/paypal_webhooks.py
Request-level checks for PayPal webhook deliveries.

What this module does NOT do: verify PayPal's signature. Authenticity is decided by PayPal itself via
POST /v1/notifications/verify-webhook-signature (PayPalEscrowEngine.verify_webhook_signature); we do not
fetch the certificate chain or check the RSA signature locally.

What it does:
1. check_webhook_headers: a cheap pre-check that all five PayPal signature headers are present and
   that PAYPAL-CERT-URL is an HTTPS URL on paypal.com, so malformed or obviously spoofed requests
   are rejected before any call to PayPal.
2. verify_mock_webhook_signature: the HMAC-SHA256 scheme the offline mock mode and local tests use to
   sign their own deliveries. It only ever succeeds for a signature computed with the shared secret.

Event handling (authorization created/voided, capture completed, payout items) lives in
EscrowOrchestrator.handle_webhook.
"""

import base64
import hashlib
import hmac
import logging
from typing import Any, Dict, Optional
import urllib.parse

logger = logging.getLogger(__name__)

# PayPal webhook header names (matched case-insensitively)
REQUIRED_WEBHOOK_HEADERS = (
    "PAYPAL-AUTH-ALGO",
    "PAYPAL-TRANSMISSION-ID",
    "PAYPAL-CERT-URL",
    "PAYPAL-TRANSMISSION-SIG",
    "PAYPAL-TRANSMISSION-TIME",
)

DEFAULT_MOCK_SECRET = "aegispay_deterministic_webhook_secret_2026"


def _extract_header(headers: Dict[str, Any], name: str) -> Optional[str]:
    """Retrieves a header value case-insensitively."""
    name_lower = name.lower()
    for k, v in headers.items():
        if k.lower() == name_lower:
            return str(v).strip() if v is not None else None
    return None


def validate_cert_url(cert_url: str) -> bool:
    """True for an HTTPS URL whose host is paypal.com or a subdomain of it."""
    if not cert_url:
        return False
    try:
        parsed = urllib.parse.urlparse(cert_url)
    except ValueError:
        return False
    if parsed.scheme.lower() != "https":
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname == "paypal.com" or hostname.endswith(".paypal.com")


def check_webhook_headers(headers: Dict[str, Any]) -> bool:
    """
    Pre-check only: all five signature headers are present and the certificate URL is on paypal.com.
    Passing this proves nothing about authenticity; PayPal's verification call does that.
    """
    values = {name: _extract_header(headers, name) for name in REQUIRED_WEBHOOK_HEADERS}
    if not all(values.values()):
        logger.warning("PayPal webhook rejected: missing required signature headers")
        return False
    if not validate_cert_url(values["PAYPAL-CERT-URL"]):
        logger.warning("PayPal webhook rejected: invalid cert domain '%s'", values["PAYPAL-CERT-URL"])
        return False
    return True


def compute_deterministic_hmac(
    headers: Dict[str, Any],
    raw_body: bytes,
    secret: str = DEFAULT_MOCK_SECRET,
) -> str:
    """HMAC-SHA256 over transmission_id|transmission_time|raw_body, as hex (mock mode and tests)."""
    trans_id = _extract_header(headers, "PAYPAL-TRANSMISSION-ID") or ""
    trans_time = _extract_header(headers, "PAYPAL-TRANSMISSION-TIME") or ""
    msg = f"{trans_id}|{trans_time}|".encode("utf-8") + raw_body
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_mock_webhook_signature(
    headers: Dict[str, Any],
    raw_body: bytes,
    secret: str = DEFAULT_MOCK_SECRET,
) -> bool:
    """
    Verifies a delivery signed with the mock-mode HMAC scheme: the headers must pass
    check_webhook_headers and PAYPAL-TRANSMISSION-SIG must be the HMAC of the body, in hex or base64.
    This is not PayPal's RSA signature and must never be used to trust real deliveries.
    """
    if not check_webhook_headers(headers):
        return False
    supplied = _extract_header(headers, "PAYPAL-TRANSMISSION-SIG") or ""
    expected_hex = compute_deterministic_hmac(headers, raw_body, secret)
    expected_b64 = base64.b64encode(bytes.fromhex(expected_hex)).decode("utf-8")
    if hmac.compare_digest(supplied, expected_hex) or hmac.compare_digest(supplied, expected_b64):
        return True
    logger.warning("Mock webhook signature mismatch")
    return False
