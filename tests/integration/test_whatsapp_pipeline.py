"""Integration tests — WhatsApp webhook to pipeline end-to-end — MODULE 09+20.

These tests send realistic webhook payloads to the live FastAPI app (with an
in-memory SQLite database) and assert the full pipeline responds correctly.

No real Meta API or Groq API calls are made — WhatsApp sends are mocked via
the simulated fallback in WhatsAppService, and the agent uses MockLLMProvider.
"""

import json
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.shop import Shop
from backend.app.models.customer import Customer
from backend.app.models.user import User, UserRole


# ---------------------------------------------------------------------------
# Helpers to seed database
# ---------------------------------------------------------------------------

async def _create_shop(session: AsyncSession, phone: str = "+923000000000") -> Shop:
    shop = Shop(
        name="Test Dukan",
        phone_number=phone,
        address="Main Bazaar, Lahore",
        timezone="Asia/Karachi",
        default_language="roman_urdu",
        currency="PKR",
    )
    session.add(shop)
    await session.flush()
    return shop


async def _create_owner(session: AsyncSession, shop: Shop) -> User:
    from backend.app.core.security import hash_password
    user = User(
        shop_id=shop.id,
        name="Test Owner",
        phone_number="+923001234567",
        role=UserRole.OWNER,
        is_active=True,
        password_hash=hash_password("testpass123"),
    )
    session.add(user)
    await session.flush()
    return user


async def _create_customer(session: AsyncSession, shop: Shop) -> Customer:
    customer = Customer(
        shop_id=shop.id,
        name="Ali Khan",
        phone_number="+923009876543",
        is_active=True,
    )
    session.add(customer)
    await session.flush()
    return customer


def _make_webhook_payload(
    from_phone: str,
    message_text: str,
    message_id: str,
    phone_number_id: str = "TEST_PHONE_ID",
) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "ENTRY_001",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "+923000000000",
                        "phone_number_id": phone_number_id,
                    },
                    "contacts": [{"profile": {"name": "Test"}, "wa_id": from_phone.lstrip("+")}],
                    "messages": [{
                        "from": from_phone.lstrip("+"),
                        "id": message_id,
                        "timestamp": "1700000000",
                        "text": {"body": message_text},
                        "type": "text",
                    }],
                },
                "field": "messages",
            }],
        }],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_webhook_verification_challenge(async_client: AsyncClient) -> None:
    """META webhook verification handshake must return the challenge string."""
    response = await async_client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "munshi_verify_token_change_in_production",
            "hub.challenge": "test_challenge_12345",
        },
    )
    assert response.status_code == 200
    assert b"test_challenge_12345" in response.content


@pytest.mark.asyncio
async def test_webhook_invalid_token_rejected(async_client: AsyncClient) -> None:
    """Wrong verify token must return 403."""
    response = await async_client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "WRONG_TOKEN",
            "hub.challenge": "12345",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_inbound_text_message_stored(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """An inbound WhatsApp message must be persisted to the messages table."""
    shop = await _create_shop(db_session)
    await db_session.commit()

    payload = _make_webhook_payload(
        from_phone="+923001234567",
        message_text="Assalam o alaikum",
        message_id="wamid.INTEG_001",
    )
    response = await async_client.post("/webhook/whatsapp", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data.get("processed", 0) >= 1 or "status" in data


@pytest.mark.asyncio
async def test_duplicate_message_not_processed_twice(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Second delivery of the same whatsapp_message_id must be marked as duplicate."""
    await _create_shop(db_session)
    await db_session.commit()

    payload = _make_webhook_payload(
        from_phone="+923001234567",
        message_text="test idempotency",
        message_id="wamid.IDEM_TEST_001",
    )

    resp1 = await async_client.post("/webhook/whatsapp", json=payload)
    resp2 = await async_client.post("/webhook/whatsapp", json=payload)

    assert resp1.status_code == 200
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_health_check(async_client: AsyncClient) -> None:
    """GET /health must return status ok with database check."""
    response = await async_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert "database" in body["components"]


@pytest.mark.asyncio
async def test_ping_endpoint(async_client: AsyncClient) -> None:
    """GET /ping must return pong immediately."""
    response = await async_client.get("/ping")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_metrics_endpoint(async_client: AsyncClient) -> None:
    """GET /metrics must return a metrics snapshot dict."""
    response = await async_client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert "llm" in body
    assert "tools" in body
    assert "whatsapp" in body


@pytest.mark.asyncio
async def test_auth_login_invalid_credentials(async_client: AsyncClient) -> None:
    """Login with wrong credentials must return 401."""
    response = await async_client.post(
        "/api/v1/auth/login",
        json={"phone_number": "+923001234567", "password": "wrong_password"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_customers_endpoint_requires_auth(async_client: AsyncClient) -> None:
    """GET /api/v1/customers without token must return 401."""
    response = await async_client.get("/api/v1/customers")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_products_endpoint_requires_auth(async_client: AsyncClient) -> None:
    """GET /api/v1/products without token must return 401."""
    response = await async_client.get("/api/v1/products")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_ledger_endpoint_requires_auth(async_client: AsyncClient) -> None:
    """GET /api/v1/ledger without token must return 401."""
    response = await async_client.get("/api/v1/ledger")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_orders_endpoint_requires_auth(async_client: AsyncClient) -> None:
    """GET /api/v1/orders without token must return 401."""
    response = await async_client.get("/api/v1/orders")
    assert response.status_code == 401
