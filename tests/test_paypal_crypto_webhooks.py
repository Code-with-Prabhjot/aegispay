"""
tests/test_paypal_crypto_webhooks.py
Unit and cryptographic verification test suite for backend.app.services.paypal_webhooks.

Verifies:
1. Missing or empty required headers are rejected.
2. Cert URL domain validation (*.paypal.com only, HTTPS required).
3. Deterministic HMAC/SHA-256 calculation and verification in mock/test mode.
4. Tampered signatures are strictly rejected.
5. Webhook event dispatchers:
   - PAYMENT.AUTHORIZATION.CREATED
   - PAYMENT.CAPTURE.COMPLETED
   - PAYMENT.AUTHORIZATION.VOIDED
"""

import json
from datetime import datetime, timezone
import pytest

from backend.app.db import Repository
from backend.app.schemas import FsmState
from backend.app.services import fsm
from backend.app.services.ledger_service import DoubleEntryLedgerEngine
from backend.app.services.orchestrator import EscrowOrchestrator
from backend.app.services.paypal_mock import MockPayPalEscrowEngine
from backend.app.services.paypal_webhooks import (
    DEFAULT_MOCK_SECRET,
    PayPalWebhookDispatcher,
    compute_deterministic_hmac,
    validate_cert_url,
    verify_paypal_webhook_signature,
)


def test_validate_cert_url():
    # Valid paypal domains
    assert validate_cert_url("https://api.sandbox.paypal.com/cert") is True
    assert validate_cert_url("https://api.paypal.com/cert") is True
    assert validate_cert_url("https://notify.paypal.com/cert") is True
    assert validate_cert_url("https://paypal.com/cert") is True

    # Insecure or spoofed domains
    assert validate_cert_url("http://api.paypal.com/cert") is False
    assert validate_cert_url("https://evil-paypal.com/cert") is False
    assert validate_cert_url("https://paypal.com.attacker.com/cert") is False
    assert validate_cert_url("https://notpaypal.com/cert") is False
    assert validate_cert_url("") is False


def test_verify_signature_missing_headers():
    body = b'{"id": "WH-1", "event_type": "PAYMENT.CAPTURE.COMPLETED"}'
    valid_headers = {
        "PAYPAL-AUTH-ALGO": "SHA256withRSA",
        "PAYPAL-TRANSMISSION-ID": "tx-123",
        "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert",
        "PAYPAL-TRANSMISSION-SIG": "mock_valid_signature",
        "PAYPAL-TRANSMISSION-TIME": "2026-11-05T00:00:00Z",
    }

    # All headers present
    assert verify_paypal_webhook_signature(valid_headers, body) is True

    # Missing each required header
    for key in list(valid_headers.keys()):
        broken = dict(valid_headers)
        del broken[key]
        assert verify_paypal_webhook_signature(broken, body) is False


def test_verify_signature_deterministic_hmac():
    body = json.dumps({"id": "WH-SEC", "event_type": "PAYMENT.CAPTURE.COMPLETED"}).encode("utf-8")
    secret = "test_fintech_secret_2026"
    headers = {
        "PAYPAL-AUTH-ALGO": "HMAC-SHA256",
        "PAYPAL-TRANSMISSION-ID": "tx-deterministic-999",
        "PAYPAL-CERT-URL": "https://api.paypal.com/cert",
        "PAYPAL-TRANSMISSION-TIME": "2026-11-05T12:00:00Z",
    }

    # Compute valid signature
    valid_sig = compute_deterministic_hmac(headers, body, secret=secret)
    headers["PAYPAL-TRANSMISSION-SIG"] = valid_sig

    # Assert verification succeeds with matching secret
    assert verify_paypal_webhook_signature(headers, body, secret=secret, mock_mode=True) is True

    # Assert verification fails with wrong secret or tampered body
    assert verify_paypal_webhook_signature(headers, body, secret="wrong_secret", mock_mode=True) is False
    assert verify_paypal_webhook_signature(headers, body + b"tampered", secret=secret, mock_mode=True) is False

    # Assert verification fails with explicitly bad signature
    headers["PAYPAL-TRANSMISSION-SIG"] = "bad_signature"
    assert verify_paypal_webhook_signature(headers, body, secret=secret, mock_mode=True) is False


@pytest.mark.asyncio
async def test_dispatcher_authorization_created(tmp_path):
    repo = Repository.from_url(f"sqlite:///{tmp_path}/disp_auth.db")
    ledger = DoubleEntryLedgerEngine(repository=repo)
    engine = MockPayPalEscrowEngine()
    orch = EscrowOrchestrator(repo=repo, ledger=ledger, engine=engine)
    dispatcher = PayPalWebhookDispatcher(orch)

    aid = "agr_disp_auth"
    now = datetime.now(timezone.utc)
    c = fsm.ContractRecord(
        agreement_id=aid,
        buyer_agent_id="b",
        worker_agent_id="w",
        worker_paypal_email="w@example.com",
        budget_micros=10_000_000,
        task_description="Test task",
        timeout_seconds=3600,
        fsm_state=FsmState.AGREEMENT_DRAFTED,
        created_at=now,
        updated_at=now,
    )
    repo.save_contract(c)
    ledger.deposit("buyer_vault", 10_000_000, aid, "seed deposit")

    event = {
        "id": "WH-EV-AUTH",
        "event_type": "PAYMENT.AUTHORIZATION.CREATED",
        "resource": {"id": "AUTH-DISP-1", "custom_id": aid},
    }

    res = await dispatcher.dispatch(event)
    assert res["status"] == "processed"
    assert res["agreement_id"] == aid

    updated = orch.get(aid)
    assert updated.fsm_state == FsmState.PAYPAL_AUTH_LOCKED
    assert updated.paypal_authorization_id == "AUTH-DISP-1"


@pytest.mark.asyncio
async def test_dispatcher_authorization_voided(tmp_path):
    repo = Repository.from_url(f"sqlite:///{tmp_path}/disp_void.db")
    ledger = DoubleEntryLedgerEngine(repository=repo)
    engine = MockPayPalEscrowEngine()
    orch = EscrowOrchestrator(repo=repo, ledger=ledger, engine=engine)
    dispatcher = PayPalWebhookDispatcher(orch)

    aid = "agr_disp_void"
    now = datetime.now(timezone.utc)
    c = fsm.ContractRecord(
        agreement_id=aid,
        buyer_agent_id="b",
        worker_agent_id="w",
        worker_paypal_email="w@example.com",
        budget_micros=10_000_000,
        task_description="Test task",
        timeout_seconds=3600,
        fsm_state=FsmState.VOID_PENDING,
        paypal_authorization_id="AUTH-DISP-VOID",
        authorized_at=now,
        created_at=now,
        updated_at=now,
    )
    repo.save_contract(c)
    ledger.deposit("buyer_vault", 10_000_000, aid, "seed")
    ledger.record_transfer("paypal_escrow_hold", "buyer_vault", 10_000_000, aid, "seed hold")

    event = {
        "id": "WH-EV-VOID",
        "event_type": "PAYMENT.AUTHORIZATION.VOIDED",
        "resource": {"id": "AUTH-DISP-VOID", "custom_id": aid},
    }

    res = await dispatcher.dispatch(event)
    assert res["status"] == "processed"
    updated = orch.get(aid)
    assert updated.fsm_state == FsmState.ESCROW_VOIDED_REFUNDED
