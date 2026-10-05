"""Unit tests for configuration, logging sanitization, and exceptions."""

from decimal import Decimal
import pytest

from backend.app.core.config import Settings
from backend.app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ConfirmationRequired,
    MunshiException,
    TenantIsolationError,
    ValidationError,
)
from backend.app.core.logging import sanitize_payload
from backend.app.core.security import compute_idempotency_hash, verify_webhook_signature


def test_settings_defaults():
    """Verify application settings initialize with safe and correct defaults."""
    cfg = Settings()
    assert cfg.APP_NAME == "whatsapp-ai-munshi"
    assert cfg.PORT == 8000
    assert cfg.LARGE_CREDIT_APPROVAL_THRESHOLD == Decimal("10000.00")
    assert cfg.DEFAULT_SHOP_CURRENCY == "PKR"
    assert cfg.DEFAULT_SHOP_LANGUAGE == "roman_urdu"


def test_sensitive_logging_sanitizer():
    """Verify passwords, tokens, and keys are redacted from logs."""
    raw_payload = {
        "user": "munshi_admin",
        "api_key": "secret-123",
        "nested": {
            "whatsapp_access_token": "token-abc",
            "safe_field": 42,
        },
        "items": [{"password": "pass", "name": "rice"}],
    }

    sanitized = sanitize_payload(raw_payload)
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["nested"]["whatsapp_access_token"] == "[REDACTED]"
    assert sanitized["nested"]["safe_field"] == 42
    assert sanitized["items"][0]["password"] == "[REDACTED]"
    assert sanitized["items"][0]["name"] == "rice"


def test_exceptions_hierarchy():
    """Verify custom domain exceptions expose correct status codes and codes."""
    val_err = ValidationError("Invalid amount", details={"field": "amount"})
    assert val_err.status_code == 422
    assert val_err.code == "VALIDATION_ERROR"
    assert val_err.details == {"field": "amount"}

    auth_err = AuthenticationError("Invalid signature")
    assert auth_err.status_code == 401

    tenant_err = TenantIsolationError("Shop mismatch")
    assert tenant_err.status_code == 403
    assert tenant_err.code == "TENANT_ISOLATION_VIOLATION"

    conf_err = ConfirmationRequired("Confirm Rs. 500", "act-1", "Ali ka 500 likh doon?")
    assert conf_err.status_code == 200
    assert conf_err.code == "CONFIRMATION_REQUIRED"
    assert conf_err.details["pending_action_id"] == "act-1"


def test_security_signature_verification():
    """Verify Meta WhatsApp Webhook SHA256 HMAC verification."""
    secret = "my_meta_app_secret"
    payload = b'{"object":"whatsapp_business_account"}'
    import hmac
    import hashlib
    valid_sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(payload, valid_sig, secret) is True
    assert verify_webhook_signature(payload, "sha256=invalidhash", secret) is False
    assert verify_webhook_signature(payload, "", secret) is False


def test_idempotency_hash():
    """Verify deterministic hash generation for requests."""
    h1 = compute_idempotency_hash("shop-1", "msg-123", "2500")
    h2 = compute_idempotency_hash("shop-1", "msg-123", "2500")
    h3 = compute_idempotency_hash("shop-1", "msg-124", "2500")

    assert h1 == h2
    assert h1 != h3
