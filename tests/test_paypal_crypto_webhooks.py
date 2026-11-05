"""
tests/test_paypal_crypto_webhooks.py
Request-level webhook checks in backend.app.services.paypal_webhooks.

Event handling is covered in tests/test_webhooks.py; PayPal's real signature check is a call to its
verify-webhook-signature endpoint (also covered there).
"""

import base64
import hashlib
import hmac
import json

import pytest

from backend.app.services import paypal_webhooks
from backend.app.services.paypal_webhooks import (
    DEFAULT_MOCK_SECRET,
    REQUIRED_WEBHOOK_HEADERS,
    check_webhook_headers,
    compute_deterministic_hmac,
    validate_cert_url,
    verify_mock_webhook_signature,
)

BODY = b'{"id": "WH-1", "event_type": "PAYMENT.CAPTURE.COMPLETED"}'


def headers(**over):
    base = {
        "PAYPAL-AUTH-ALGO": "SHA256withRSA",
        "PAYPAL-TRANSMISSION-ID": "tx-123",
        "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert",
        "PAYPAL-TRANSMISSION-SIG": "anything",
        "PAYPAL-TRANSMISSION-TIME": "2026-11-05T00:00:00Z",
    }
    base.update(over)
    return base


def test_validate_cert_url():
    for good in ("https://api.sandbox.paypal.com/cert", "https://api.paypal.com/cert",
                 "https://notify.paypal.com/cert", "https://paypal.com/cert"):
        assert validate_cert_url(good) is True
    for bad in ("http://api.paypal.com/cert", "https://evil-paypal.com/cert",
                "https://paypal.com.attacker.com/cert", "https://notpaypal.com/cert",
                "https://api.paypal.com@evil.example/cert", "ftp://api.paypal.com/cert", ""):
        assert validate_cert_url(bad) is False, bad


def test_check_webhook_headers_requires_all_five_and_a_paypal_cert_host():
    assert check_webhook_headers(headers()) is True
    assert check_webhook_headers({k.lower(): v for k, v in headers().items()}) is True
    for key in REQUIRED_WEBHOOK_HEADERS:
        broken = headers()
        del broken[key]
        assert check_webhook_headers(broken) is False, key
        assert check_webhook_headers(headers(**{key: ""})) is False, key
    assert check_webhook_headers(headers(**{"PAYPAL-CERT-URL": "https://evil.example/cert"})) is False


def test_the_header_check_does_not_pretend_to_verify_a_signature():
    # Any signature value passes the pre-check; authenticity is PayPal's call to make.
    assert check_webhook_headers(headers(**{"PAYPAL-TRANSMISSION-SIG": "forged"})) is True
    assert not hasattr(paypal_webhooks, "verify_paypal_webhook_signature")
    assert not hasattr(paypal_webhooks, "PayPalWebhookDispatcher")


def test_mock_hmac_signature_round_trip():
    secret = "test_fintech_secret_2026"
    h = headers(**{"PAYPAL-AUTH-ALGO": "HMAC-SHA256"})
    h["PAYPAL-TRANSMISSION-SIG"] = compute_deterministic_hmac(h, BODY, secret=secret)

    assert verify_mock_webhook_signature(h, BODY, secret=secret) is True
    assert verify_mock_webhook_signature(h, BODY, secret="wrong_secret") is False
    assert verify_mock_webhook_signature(h, BODY + b"tampered", secret=secret) is False

    msg = b"tx-123|2026-11-05T00:00:00Z|" + BODY
    b64 = base64.b64encode(hmac.new(secret.encode(), msg, hashlib.sha256).digest()).decode()
    assert verify_mock_webhook_signature({**h, "PAYPAL-TRANSMISSION-SIG": b64}, BODY, secret=secret) is True


@pytest.mark.parametrize("magic", ["sig", "valid_mock_sig", "mock_valid_signature", "mock_valid_anything",
                                   "bad_signature", "invalid", "tampered", ""])
def test_no_magic_signature_strings_are_accepted(magic):
    assert verify_mock_webhook_signature(headers(**{"PAYPAL-TRANSMISSION-SIG": magic}), BODY) is False


def test_mock_signature_also_requires_valid_headers():
    h = headers()
    h["PAYPAL-TRANSMISSION-SIG"] = compute_deterministic_hmac(h, BODY)
    assert verify_mock_webhook_signature(h, BODY) is True
    h["PAYPAL-CERT-URL"] = "https://evil.example/cert"
    assert verify_mock_webhook_signature(h, BODY) is False


def test_default_secret_signs_when_none_is_given():
    h = headers()
    body = json.dumps({"id": "WH-SEC"}).encode()
    h["PAYPAL-TRANSMISSION-SIG"] = compute_deterministic_hmac(h, body)
    assert compute_deterministic_hmac(h, body) == compute_deterministic_hmac(h, body, DEFAULT_MOCK_SECRET)
    assert verify_mock_webhook_signature(h, body) is True
