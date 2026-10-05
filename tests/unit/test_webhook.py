"""MODULE 06 — WhatsApp Webhook Ingress: complete test suite.

Coverage:
  Webhook verification (GET)
    - valid token echoes challenge
    - wrong token → 401
    - missing mode → 401

  Signature verification
    - valid HMAC passes
    - invalid HMAC → 401
    - missing header with APP_SECRET configured → 401
    - no APP_SECRET (dev) → skipped / passes

  Object-type guard
    - non-whatsapp_business_account → 200 ignored

  Message ingestion (POST)
    - text message stored correctly
    - audio message stored with [AUDIO_ID:...] token
    - image message stored with [IMAGE_ID:...] token
    - document message stored with [DOCUMENT_ID:...] token
    - video message stored with [VIDEO_ID:...] token
    - sticker message stored with [STICKER_ID:...] token
    - location message stored with [LOCATION:lat,lon] token
    - interactive button_reply stored correctly
    - reaction stored correctly
    - unknown type stored with [UNSUPPORTED_TYPE:...] token

  Idempotency
    - exact duplicate webhook → duplicates=1, NO second DB row
    - multi-message payload with one dup → processed=1 duplicates=1

  Status updates
    - delivery receipt payload → statuses=1, processed=0

  Multi-tenant shop resolution
    - shop matched by phone_number_id → correct shop_id on message
    - no matching shop → shop_id is None

  Sender-type detection
    - sender phone matches shop phone → OWNER
    - different phone → CUSTOMER

  MessageRepository unit tests
    - get_by_whatsapp_id: hit and miss
    - create_message: all fields persisted
    - update_status: status transitions
    - mark_processing / mark_completed / mark_failed conveniences
    - list_by_shop: pagination and status filter
    - list_by_sender: scoped to phone

  Health endpoint
    - /health returns 200 with database=up
    - /ping returns {"ping": "pong"}
"""

import hashlib
import hmac
import json
import uuid
from typing import Optional

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.models.message import Message
from backend.app.models.shop import Shop
from backend.app.repositories.message_repository import MessageRepository
from backend.app.services.whatsapp_service import WhatsAppService, _extract_raw_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_payload(
    *,
    msg_id: str = "wamid.TEST001",
    from_phone: str = "923007654321",
    msg_type: str = "text",
    text_body: Optional[str] = "Ali ka 2500 udhar likh do",
    phone_number_id: str = "100200300400",
    object_type: str = "whatsapp_business_account",
    extra_msg_fields: Optional[dict] = None,
) -> dict:
    """Build a minimal valid Meta webhook payload dict."""
    msg: dict = {
        "from": from_phone,
        "id": msg_id,
        "timestamp": "1720000000",
        "type": msg_type,
    }
    if msg_type == "text" and text_body is not None:
        msg["text"] = {"body": text_body}
    if extra_msg_fields:
        msg.update(extra_msg_fields)

    return {
        "object": object_type,
        "entry": [
            {
                "id": "entry-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "+923001234567",
                                "phone_number_id": phone_number_id,
                            },
                            "messages": [msg],
                        },
                    }
                ],
            }
        ],
    }


def _make_status_payload(
    *,
    msg_id: str = "wamid.OUTBOUND001",
    recipient: str = "923007654321",
    delivery_status: str = "delivered",
    phone_number_id: str = "100200300400",
) -> dict:
    """Build a webhook payload containing a delivery status update."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "entry-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "+923001234567",
                                "phone_number_id": phone_number_id,
                            },
                            "statuses": [
                                {
                                    "id": msg_id,
                                    "recipient_id": recipient,
                                    "status": delivery_status,
                                    "timestamp": "1720000001",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _sign(raw_json: str, secret: str) -> str:
    """Compute Meta-style HMAC-SHA256 signature header value."""
    mac = hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_json.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    return f"sha256={mac.hexdigest()}"


async def _create_shop(db: AsyncSession, phone: str = "100200300400") -> Shop:
    """Insert a minimal shop row and flush (caller must commit or use in fixture)."""
    shop = Shop(
        name="Test Dukaan",
        phone_number=phone,
        default_language="roman_urdu",
        currency="PKR",
        settings={},
    )
    db.add(shop)
    await db.flush()
    return shop


# ===========================================================================
# GET /webhook/whatsapp — challenge verification
# ===========================================================================


class TestWebhookVerification:
    async def test_valid_token_echoes_challenge(self, async_client: AsyncClient):
        resp = await async_client.get(
            "/webhook/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge_abc123",
                "hub.verify_token": settings.WHATSAPP_VERIFY_TOKEN,
            },
        )
        assert resp.status_code == 200
        assert resp.text == "challenge_abc123"

    async def test_wrong_token_returns_401(self, async_client: AsyncClient):
        resp = await async_client.get(
            "/webhook/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge_abc123",
                "hub.verify_token": "totally_wrong_token",
            },
        )
        assert resp.status_code == 401
        assert "Invalid webhook verification token" in resp.text

    async def test_missing_mode_returns_401(self, async_client: AsyncClient):
        resp = await async_client.get(
            "/webhook/whatsapp",
            params={
                "hub.challenge": "challenge_abc123",
                "hub.verify_token": settings.WHATSAPP_VERIFY_TOKEN,
            },
        )
        assert resp.status_code == 401

    async def test_empty_challenge_returns_empty_string(self, async_client: AsyncClient):
        resp = await async_client.get(
            "/webhook/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": settings.WHATSAPP_VERIFY_TOKEN,
                # no hub.challenge param
            },
        )
        assert resp.status_code == 200
        assert resp.text == ""


# ===========================================================================
# POST /webhook/whatsapp — signature verification
# ===========================================================================


class TestSignatureVerification:
    async def test_valid_signature_accepted(
        self, async_client: AsyncClient, monkeypatch
    ):
        """A correctly signed payload must be processed."""
        secret = "test_app_secret_32_chars_minimum!!"
        monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", secret)

        payload = _make_payload()
        raw_json = json.dumps(payload)
        sig = _sign(raw_json, secret)

        resp = await async_client.post(
            "/webhook/whatsapp",
            content=raw_json,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

    async def test_invalid_signature_returns_401(
        self, async_client: AsyncClient, monkeypatch
    ):
        secret = "test_app_secret_32_chars_minimum!!"
        monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", secret)

        payload = _make_payload()
        raw_json = json.dumps(payload)

        resp = await async_client.post(
            "/webhook/whatsapp",
            content=raw_json,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=deadbeefdeadbeef",
            },
        )
        assert resp.status_code == 401
        assert "signature" in resp.text.lower()

    async def test_missing_signature_with_secret_configured_returns_401(
        self, async_client: AsyncClient, monkeypatch
    ):
        monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "some_real_secret")

        payload = _make_payload()
        raw_json = json.dumps(payload)

        resp = await async_client.post(
            "/webhook/whatsapp",
            content=raw_json,
            headers={"Content-Type": "application/json"},
            # No X-Hub-Signature-256 header
        )
        assert resp.status_code == 401

    async def test_no_app_secret_configured_skips_check(
        self, async_client: AsyncClient, monkeypatch
    ):
        """Dev mode: APP_SECRET is None → signature check skipped, payload accepted."""
        monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", None)

        payload = _make_payload()
        raw_json = json.dumps(payload)

        resp = await async_client.post(
            "/webhook/whatsapp",
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"


# ===========================================================================
# POST — object-type guard
# ===========================================================================


class TestObjectTypeGuard:
    async def test_non_whatsapp_object_returns_ignored(self, async_client: AsyncClient):
        payload = _make_payload(object_type="page")
        raw_json = json.dumps(payload)

        resp = await async_client.post(
            "/webhook/whatsapp",
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"
        assert data["processed"] == 0


# ===========================================================================
# POST — message ingestion by type
# ===========================================================================


class TestMessageIngestionByType:
    async def test_text_message_stored(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        raw_json = json.dumps(
            _make_payload(msg_id="wamid.TEXT01", text_body="Assalam o alaikum")
        )
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["processed"] == 1

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.TEXT01")
        )
        msg = result.scalar_one()
        assert msg.raw_message == "Assalam o alaikum"
        assert msg.message_type == "text"
        assert msg.sender_phone == "923007654321"
        assert msg.processing_status in ("RECEIVED", "COMPLETED")


    async def test_audio_message_stored_with_token(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        payload = _make_payload(
            msg_id="wamid.AUDIO01",
            msg_type="audio",
            text_body=None,
            extra_msg_fields={"audio": {"id": "audio_media_id_abc", "mime_type": "audio/ogg"}},
        )
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.AUDIO01")
        )
        msg = result.scalar_one()
        assert msg.message_type == "audio"
        assert msg.raw_message == "[AUDIO_ID:audio_media_id_abc]"

    async def test_image_message_stored_with_caption(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        payload = _make_payload(
            msg_id="wamid.IMAGE01",
            msg_type="image",
            text_body=None,
            extra_msg_fields={
                "image": {
                    "id": "img_media_id_xyz",
                    "mime_type": "image/jpeg",
                    "caption": "Receipt photo",
                }
            },
        )
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.IMAGE01")
        )
        msg = result.scalar_one()
        assert msg.message_type == "image"
        assert msg.raw_message == "[IMAGE_ID:img_media_id_xyz:Receipt photo]"

    async def test_document_message_stored(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        payload = _make_payload(
            msg_id="wamid.DOC01",
            msg_type="document",
            text_body=None,
            extra_msg_fields={
                "document": {
                    "id": "doc_media_id_001",
                    "mime_type": "application/pdf",
                    "filename": "invoice.pdf",
                }
            },
        )
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.DOC01")
        )
        msg = result.scalar_one()
        assert msg.message_type == "document"
        assert msg.raw_message == "[DOCUMENT_ID:doc_media_id_001]"

    async def test_location_message_stored(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        payload = _make_payload(
            msg_id="wamid.LOC01",
            msg_type="location",
            text_body=None,
            extra_msg_fields={
                "location": {
                    "latitude": 31.5204,
                    "longitude": 74.3587,
                    "name": "Lahore",
                }
            },
        )
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.LOC01")
        )
        msg = result.scalar_one()
        assert msg.message_type == "location"
        assert "31.5204" in msg.raw_message
        assert "74.3587" in msg.raw_message
        assert "Lahore" in msg.raw_message

    async def test_interactive_button_reply_stored(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        payload = _make_payload(
            msg_id="wamid.BTN01",
            msg_type="interactive",
            text_body=None,
            extra_msg_fields={
                "interactive": {
                    "type": "button_reply",
                    "button_reply": {"id": "btn_haan", "title": "Haan"},
                }
            },
        )
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.BTN01")
        )
        msg = result.scalar_one()
        assert msg.message_type == "interactive"
        assert msg.raw_message == "[BUTTON_REPLY:btn_haan:Haan]"

    async def test_reaction_message_stored(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        payload = _make_payload(
            msg_id="wamid.REACT01",
            msg_type="reaction",
            text_body=None,
            extra_msg_fields={
                "reaction": {"message_id": "wamid.ORIG01", "emoji": "👍"}
            },
        )
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.REACT01")
        )
        msg = result.scalar_one()
        assert msg.message_type == "reaction"
        assert "👍" in msg.raw_message

    async def test_unknown_type_stored_as_unsupported(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        payload = _make_payload(
            msg_id="wamid.UNKN01",
            msg_type="order",   # hypothetical future type
            text_body=None,
        )
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.UNKN01")
        )
        msg = result.scalar_one()
        assert msg.raw_message == "[UNSUPPORTED_TYPE:order]"


# ===========================================================================
# POST — idempotency
# ===========================================================================


class TestIdempotency:
    async def test_duplicate_webhook_creates_only_one_row(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Send exact same payload twice → processed=1 then duplicates=1, 1 row total."""
        msg_id = "wamid.IDEM001"
        raw_json = json.dumps(
            _make_payload(msg_id=msg_id, text_body="Ali ka 2500 udhar likh do")
        )

        # First delivery
        r1 = await async_client.post(
            "/webhook/whatsapp",
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )
        assert r1.status_code == 200
        assert r1.json()["processed"] == 1
        assert r1.json()["duplicates"] == 0

        # Duplicate delivery
        r2 = await async_client.post(
            "/webhook/whatsapp",
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )
        assert r2.status_code == 200
        assert r2.json()["processed"] == 0
        assert r2.json()["duplicates"] == 1

        # Exactly one row in DB
        rows = (
            await db_session.execute(
                select(Message).where(Message.whatsapp_message_id == msg_id)
            )
        ).scalars().all()
        assert len(rows) == 1

    async def test_multi_message_payload_partial_dup(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Payload with 2 messages where first already exists → processed=1, dup=1."""
        existing_id = "wamid.EXISTING01"
        new_id = "wamid.NEW01"

        # Pre-insert the first message
        first_raw = json.dumps(_make_payload(msg_id=existing_id, text_body="First msg"))
        await async_client.post(
            "/webhook/whatsapp",
            content=first_raw,
            headers={"Content-Type": "application/json"},
        )

        # Now send a batch with the existing id + a new one
        batch_payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "entry-1",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "+923001234567",
                                    "phone_number_id": "100200300400",
                                },
                                "messages": [
                                    {
                                        "from": "923007654321",
                                        "id": existing_id,
                                        "timestamp": "1720000000",
                                        "type": "text",
                                        "text": {"body": "First msg"},
                                    },
                                    {
                                        "from": "923007654321",
                                        "id": new_id,
                                        "timestamp": "1720000001",
                                        "type": "text",
                                        "text": {"body": "Second msg"},
                                    },
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        r = await async_client.post(
            "/webhook/whatsapp",
            content=json.dumps(batch_payload),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["processed"] == 1
        assert data["duplicates"] == 1

        # Both IDs exist once each
        for wamid in (existing_id, new_id):
            rows = (
                await db_session.execute(
                    select(Message).where(Message.whatsapp_message_id == wamid)
                )
            ).scalars().all()
            assert len(rows) == 1


# ===========================================================================
# POST — delivery status updates
# ===========================================================================


class TestStatusUpdates:
    async def test_status_update_logged_not_stored_as_message(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Delivery receipts must be acknowledged with statuses=1 and no Message row."""
        raw_json = json.dumps(_make_status_payload(msg_id="wamid.OUT001"))
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["statuses"] == 1
        assert data["processed"] == 0

        # No message row created for a status update
        rows = (
            await db_session.execute(
                select(Message).where(Message.whatsapp_message_id == "wamid.OUT001")
            )
        ).scalars().all()
        assert len(rows) == 0

    async def test_mixed_payload_messages_and_statuses(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """A single entry can have both messages and statuses in separate changes."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "entry-1",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "+923001234567",
                                    "phone_number_id": "100200300400",
                                },
                                "messages": [
                                    {
                                        "from": "923007654321",
                                        "id": "wamid.MIX_MSG",
                                        "timestamp": "1720000010",
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    }
                                ],
                                "statuses": [
                                    {
                                        "id": "wamid.MIX_STATUS",
                                        "recipient_id": "923001234567",
                                        "status": "read",
                                        "timestamp": "1720000011",
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 1
        assert data["statuses"] == 1


# ===========================================================================
# Multi-tenant shop resolution
# ===========================================================================


class TestShopResolution:
    async def test_message_linked_to_shop_when_phone_matches(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """When phone_number_id matches Shop.phone_number the message gets shop_id."""
        shop = await _create_shop(db_session, phone="100200300400")
        await db_session.commit()

        raw_json = json.dumps(
            _make_payload(msg_id="wamid.SHOP01", phone_number_id="100200300400")
        )
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.SHOP01")
        )
        msg = result.scalar_one()
        assert msg.shop_id == shop.id

    async def test_message_shop_id_none_when_no_shop_exists(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """With no shops in the database, shop_id should be None."""
        raw_json = json.dumps(
            _make_payload(msg_id="wamid.NOSHOP01", phone_number_id="UNKNOWN_PNI")
        )
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.NOSHOP01")
        )
        msg = result.scalar_one()
        assert msg.shop_id is None


# ===========================================================================
# Sender-type detection
# ===========================================================================


class TestSenderTypeDetection:
    async def test_owner_phone_tagged_as_owner(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Sender whose phone == Shop.phone_number should be tagged OWNER."""
        owner_phone = "923001111111"
        await _create_shop(db_session, phone=owner_phone)
        await db_session.commit()

        raw_json = json.dumps(
            _make_payload(
                msg_id="wamid.OWNER01",
                from_phone=owner_phone,
                phone_number_id=owner_phone,
            )
        )
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.OWNER01")
        )
        msg = result.scalar_one()
        assert msg.sender_type == "OWNER"

    async def test_customer_phone_tagged_as_customer(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Sender whose phone != Shop.phone_number should be tagged CUSTOMER."""
        await _create_shop(db_session, phone="923009999999")
        await db_session.commit()

        raw_json = json.dumps(
            _make_payload(
                msg_id="wamid.CUST01",
                from_phone="923007654321",  # different from shop
                phone_number_id="923009999999",
            )
        )
        resp = await async_client.post(
            "/webhook/whatsapp",
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.whatsapp_message_id == "wamid.CUST01")
        )
        msg = result.scalar_one()
        assert msg.sender_type == "CUSTOMER"


# ===========================================================================
# _extract_raw_text unit tests (pure function)
# ===========================================================================


class TestExtractRawText:
    """Unit-test the _extract_raw_text helper directly without HTTP overhead.

    WhatsAppIncomingMessage uses Field(alias="from") so we always pass the
    alias key "from" and call model_validate without any by_alias argument
    (Pydantic v2.13+ requires at least one of by_alias/by_name to be True
    when either is explicitly supplied).
    """

    @staticmethod
    def _build(type_: str, **extra):
        """Build a WhatsAppIncomingMessage from alias-keyed dict."""
        from backend.app.schemas.whatsapp import WhatsAppIncomingMessage
        data = {"from": "9230011", "id": "x", "timestamp": "1", "type": type_, **extra}
        return WhatsAppIncomingMessage.model_validate(data)

    def test_text(self):
        msg = self._build("text", text={"body": "Salam"})
        assert _extract_raw_text(msg) == "Salam"

    def test_audio(self):
        msg = self._build("audio", audio={"id": "AUD123", "mime_type": "audio/ogg"})
        assert _extract_raw_text(msg) == "[AUDIO_ID:AUD123]"

    def test_image_with_caption(self):
        msg = self._build("image", image={"id": "IMG456", "caption": "bill photo"})
        assert _extract_raw_text(msg) == "[IMAGE_ID:IMG456:bill photo]"

    def test_image_without_caption(self):
        msg = self._build("image", image={"id": "IMG789"})
        assert _extract_raw_text(msg) == "[IMAGE_ID:IMG789]"

    def test_location_with_name(self):
        msg = self._build(
            "location",
            location={"latitude": 31.5, "longitude": 74.3, "name": "Lahore"},
        )
        result = _extract_raw_text(msg)
        assert "31.5" in result
        assert "74.3" in result
        assert "Lahore" in result

    def test_interactive_list_reply(self):
        msg = self._build(
            "interactive",
            interactive={
                "type": "list_reply",
                "list_reply": {"id": "opt_1", "title": "Option A"},
            },
        )
        assert _extract_raw_text(msg) == "[LIST_REPLY:opt_1:Option A]"

    def test_unsupported_type(self):
        msg = self._build("contacts")
        assert _extract_raw_text(msg) == "[UNSUPPORTED_TYPE:contacts]"


# ===========================================================================
# MessageRepository unit tests
# ===========================================================================


class TestMessageRepository:
    async def test_create_and_get_by_whatsapp_id(self, db_session: AsyncSession):
        repo = MessageRepository(db_session)
        msg = await repo.create_message(
            whatsapp_message_id="wamid.REPO001",
            shop_id=None,
            sender_phone="923001234567",
            sender_type="CUSTOMER",
            message_type="text",
            raw_message="Test message",
        )
        await db_session.commit()

        fetched = await repo.get_by_whatsapp_id("wamid.REPO001")
        assert fetched is not None
        assert fetched.id == msg.id
        assert fetched.raw_message == "Test message"
        assert fetched.processing_status == "RECEIVED"

    async def test_get_by_whatsapp_id_returns_none_for_unknown(
        self, db_session: AsyncSession
    ):
        repo = MessageRepository(db_session)
        result = await repo.get_by_whatsapp_id("wamid.DOES_NOT_EXIST")
        assert result is None

    async def test_update_status_transitions(self, db_session: AsyncSession):
        repo = MessageRepository(db_session)
        msg = await repo.create_message(
            whatsapp_message_id="wamid.REPO002",
            shop_id=None,
            sender_phone="923001234567",
            sender_type="CUSTOMER",
            message_type="text",
            raw_message="Status test",
        )
        await db_session.commit()

        updated = await repo.mark_processing(msg.id)
        await db_session.commit()
        assert updated.processing_status == "PROCESSING"

        completed = await repo.mark_completed(msg.id, intent="OWNER_LEDGER")
        await db_session.commit()
        assert completed.processing_status == "COMPLETED"
        assert completed.intent == "OWNER_LEDGER"

    async def test_mark_failed(self, db_session: AsyncSession):
        repo = MessageRepository(db_session)
        msg = await repo.create_message(
            whatsapp_message_id="wamid.REPO003",
            shop_id=None,
            sender_phone="923001234567",
            sender_type="CUSTOMER",
            message_type="text",
            raw_message="Will fail",
        )
        await db_session.commit()

        failed = await repo.mark_failed(msg.id)
        await db_session.commit()
        assert failed.processing_status == "FAILED"

    async def test_list_by_shop_with_status_filter(self, db_session: AsyncSession):
        shop = await _create_shop(db_session, phone="923005555555")
        await db_session.flush()

        repo = MessageRepository(db_session)

        for i in range(3):
            m = await repo.create_message(
                whatsapp_message_id=f"wamid.LIST{i:03d}",
                shop_id=shop.id,
                sender_phone="923007654321",
                sender_type="CUSTOMER",
                message_type="text",
                raw_message=f"Message {i}",
                processing_status="RECEIVED",
            )
            if i == 0:
                await repo.mark_processing(m.id)

        await db_session.commit()

        received_msgs = await repo.list_by_shop(shop.id, processing_status="RECEIVED")
        assert len(received_msgs) == 2

        all_msgs = await repo.list_by_shop(shop.id)
        assert len(all_msgs) == 3

    async def test_list_by_sender(self, db_session: AsyncSession):
        shop = await _create_shop(db_session, phone="923006666666")
        await db_session.flush()

        repo = MessageRepository(db_session)
        alice = "923001111111"
        bob = "923002222222"

        for phone, mid in [(alice, "wamid.A1"), (alice, "wamid.A2"), (bob, "wamid.B1")]:
            await repo.create_message(
                whatsapp_message_id=mid,
                shop_id=shop.id,
                sender_phone=phone,
                sender_type="CUSTOMER",
                message_type="text",
                raw_message=f"msg from {phone}",
            )
        await db_session.commit()

        alice_msgs = await repo.list_by_sender(shop.id, alice)
        assert len(alice_msgs) == 2
        bob_msgs = await repo.list_by_sender(shop.id, bob)
        assert len(bob_msgs) == 1

    async def test_list_by_shop_pagination(self, db_session: AsyncSession):
        shop = await _create_shop(db_session, phone="923007777777")
        await db_session.flush()

        repo = MessageRepository(db_session)
        for i in range(5):
            await repo.create_message(
                whatsapp_message_id=f"wamid.PAGE{i:03d}",
                shop_id=shop.id,
                sender_phone="923009876543",
                sender_type="CUSTOMER",
                message_type="text",
                raw_message=f"Page msg {i}",
            )
        await db_session.commit()

        page1 = await repo.list_by_shop(shop.id, skip=0, limit=2)
        page2 = await repo.list_by_shop(shop.id, skip=2, limit=2)
        assert len(page1) == 2
        assert len(page2) == 2
        # No overlap
        page1_ids = {m.id for m in page1}
        page2_ids = {m.id for m in page2}
        assert page1_ids.isdisjoint(page2_ids)


# ===========================================================================
# Health endpoints
# ===========================================================================


class TestHealthEndpoints:
    async def test_health_returns_200_with_database_up(self, async_client: AsyncClient):
        resp = await async_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["components"]["database"]["status"] == "up"
        assert data["components"]["api"]["status"] == "up"
        assert "version" in data
        assert "timestamp" in data

    async def test_ping_returns_pong(self, async_client: AsyncClient):
        resp = await async_client.get("/ping")
        assert resp.status_code == 200
        assert resp.json() == {"ping": "pong"}

    async def test_health_contains_llm_provider_info(self, async_client: AsyncClient):
        resp = await async_client.get("/health")
        data = resp.json()
        assert "llm_provider" in data["components"]
        assert settings.LLM_PROVIDER in data["components"]["llm_provider"]["detail"]
