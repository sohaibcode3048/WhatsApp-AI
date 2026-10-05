"""WhatsApp Cloud API service.

Responsibilities:
  - Webhook challenge verification
  - HMAC signature verification
  - Multi-tenant shop resolution by phone_number_id
  - Idempotent message ingestion via MessageRepository
  - Full inbound message-type handling (text, audio, image, document,
    video, sticker, location, interactive, reaction)
  - Audio message STT pipeline via SpeechService (MODULE 07)
  - Language detection + normalization via LanguageService (MODULE 08)
  - Delivery-status update handling (sent / delivered / read / failed)
  - Outbound text messaging with graceful simulation fallback
"""

import uuid
from typing import Any, Dict, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.exceptions import (
    AuthenticationError,
    LowConfidenceTranscriptionError,
    SpeechTranscriptionError,
    WhatsAppError,
)
from backend.app.core.logging import logger
from backend.app.core.security import verify_webhook_signature
from backend.app.models.message import Message
from backend.app.models.shop import Shop
from backend.app.repositories.message_repository import MessageRepository
from backend.app.repositories.shop_repository import ShopRepository
from backend.app.schemas.whatsapp import (
    WhatsAppIncomingMessage,
    WhatsAppStatusUpdate,
    WhatsAppWebhookPayload,
)
from backend.app.services.speech_service import SpeechService
from backend.app.services.language_service import LanguageService


# ---------------------------------------------------------------------------
# Raw-text extraction helpers
# ---------------------------------------------------------------------------

_MEDIA_TYPE_LABELS: Dict[str, str] = {
    "audio": "AUDIO",
    "image": "IMAGE",
    "document": "DOCUMENT",
    "video": "VIDEO",
    "sticker": "STICKER",
}


def _extract_raw_text(msg: WhatsAppIncomingMessage) -> str:
    """Return a concise, audit-friendly representation of any message type.

    Text messages return the body verbatim.
    Media messages return a placeholder token with the Meta media-id so the
    downstream pipeline (e.g. voice-processing or image-analysis modules) can
    fetch the binary when needed.
    """
    msg_type = msg.type

    if msg_type == "text" and msg.text:
        return msg.text.body

    if msg_type in _MEDIA_TYPE_LABELS:
        media_obj = getattr(msg, msg_type, None)
        if media_obj:
            label = _MEDIA_TYPE_LABELS[msg_type]
            caption = getattr(media_obj, "caption", None)
            suffix = f":{caption}" if caption else ""
            return f"[{label}_ID:{media_obj.id}{suffix}]"

    if msg_type == "location" and msg.location:
        loc = msg.location
        name_part = f":{loc.name}" if loc.name else ""
        return f"[LOCATION:{loc.latitude},{loc.longitude}{name_part}]"

    if msg_type == "interactive" and msg.interactive:
        inter = msg.interactive
        if inter.button_reply:
            return f"[BUTTON_REPLY:{inter.button_reply.id}:{inter.button_reply.title}]"
        if inter.list_reply:
            return f"[LIST_REPLY:{inter.list_reply.id}:{inter.list_reply.title}]"

    if msg_type == "reaction" and msg.reaction:
        return f"[REACTION:{msg.reaction.emoji}:{msg.reaction.message_id}]"

    # Fallback for unknown future types
    return f"[UNSUPPORTED_TYPE:{msg_type}]"


def _sender_type_from_phone(sender_phone: str, shop: Optional[Shop]) -> str:
    """Infer a basic sender_type from the phone number.

    A full role-lookup (USER table check) happens later in the router module.
    Here we only differentiate the shop owner's own number so that webhook
    messages from the owner are tagged correctly from the start.
    """
    if shop and shop.phone_number == sender_phone:
        return "OWNER"
    return "CUSTOMER"


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class WhatsAppService:
    """Service managing Meta WhatsApp webhook events and messaging interactions."""

    def __init__(
        self,
        session: AsyncSession,
        speech_service: Optional[SpeechService] = None,
        language_service: Optional[LanguageService] = None,
    ) -> None:
        self.session = session
        self._message_repo = MessageRepository(session)
        self._shop_repo = ShopRepository(session)
        self._speech_service: Optional[SpeechService] = speech_service
        # LanguageService is pure/sync — one instance per request is fine
        self._language_service: LanguageService = language_service or LanguageService()

    # ------------------------------------------------------------------
    # Verification helpers
    # ------------------------------------------------------------------

    def verify_webhook_token(
        self,
        mode: Optional[str],
        token: Optional[str],
        challenge: Optional[str],
    ) -> str:
        """Verify Meta's webhook challenge GET handshake.

        Raises AuthenticationError on any mismatch so the route can return 401.
        """
        if mode == "subscribe" and token == settings.WHATSAPP_VERIFY_TOKEN:
            logger.info("WhatsApp webhook verified successfully.")
            return challenge or ""
        logger.warning(
            "Invalid webhook verification attempt.",
            extra={"mode": mode},
        )
        raise AuthenticationError("Invalid webhook verification token")

    def verify_signature(self, raw_body: bytes, signature_header: Optional[str]) -> None:
        """Verify SHA-256 HMAC signature from Meta.

        In development (APP_SECRET not configured) the check is skipped with a
        warning so engineers can test without a live Meta app.

        Raises AuthenticationError if the signature is present but invalid.
        """
        if not settings.WHATSAPP_APP_SECRET:
            logger.warning(
                "WHATSAPP_APP_SECRET not configured — skipping signature check (dev mode)."
            )
            return

        if not signature_header or not verify_webhook_signature(
            raw_body, signature_header, settings.WHATSAPP_APP_SECRET
        ):
            logger.error("WhatsApp webhook HMAC signature verification failed.")
            raise AuthenticationError("Invalid WhatsApp webhook signature")

    # ------------------------------------------------------------------
    # Shop resolution (multi-tenant)
    # ------------------------------------------------------------------

    async def resolve_shop(self, phone_number_id: str) -> Optional[Shop]:
        """Resolve the tenant Shop from the Meta phone_number_id in the webhook.

        Strategy:
          1. Match ``phone_number_id`` against ``Shop.phone_number`` (production).
          2. Fall back to the first shop in the database (single-tenant / dev).

        Returns None if no shop exists at all.
        """
        # Attempt exact match on the phone_number_id Meta sent
        shop = await self._shop_repo.get_by_phone(phone_number_id)
        if shop:
            return shop

        # Dev / single-tenant fallback
        result = await self.session.execute(select(Shop).limit(1))
        shop = result.scalar_one_or_none()
        if shop:
            logger.debug(
                "Shop resolved via single-tenant fallback.",
                extra={"phone_number_id": phone_number_id, "shop_id": str(shop.id)},
            )
        else:
            logger.warning(
                "No shop found for phone_number_id — message stored without shop_id.",
                extra={"phone_number_id": phone_number_id},
            )
        return shop

    # ------------------------------------------------------------------
    # Inbound message ingestion
    # ------------------------------------------------------------------

    async def ingest_message(
        self,
        msg: WhatsAppIncomingMessage,
        phone_number_id: str,
        sender_phone: str,
    ) -> Tuple[Message, bool]:
        """Idempotently persist one inbound WhatsApp message.

        For audio messages the STT pipeline runs automatically:
          - Successful transcription → normalized_message = transcript
          - LowConfidenceTranscriptionError → normalized_message stays as
            the [AUDIO_ID:…] token; the caller is responsible for sending
            a clarification reply to the user.
          - SpeechTranscriptionError (download/API failure) → same fallback,
            logged as a warning.

        Returns ``(Message, is_duplicate)``.  The session is flushed but NOT
        committed — callers must call ``await db.commit()``.
        """
        # ---- idempotency check ------------------------------------------------
        existing = await self._message_repo.get_by_whatsapp_id(msg.id)
        if existing:
            logger.info(
                "Duplicate WhatsApp message — skipping.",
                extra={"whatsapp_message_id": msg.id},
            )
            return existing, True

        # ---- shop resolution --------------------------------------------------
        shop = await self.resolve_shop(phone_number_id)
        shop_id: Optional[uuid.UUID] = shop.id if shop else None

        # ---- extract raw text -------------------------------------------------
        raw_text = _extract_raw_text(msg)
        sender_type = _sender_type_from_phone(sender_phone, shop)

        # ---- STT pipeline for audio messages ----------------------------------
        normalized_text = raw_text          # default: same as raw
        stt_low_confidence = False

        if msg.type == "audio" and msg.audio and settings.WHATSAPP_ACCESS_TOKEN:
            normalized_text, stt_low_confidence = await self._run_stt(
                media_id=msg.audio.id,
                mime_type=msg.audio.mime_type or "audio/ogg",
                raw_fallback=raw_text,
            )

        # ---- language normalisation (MODULE 08) ------------------------------
        # Run on text/audio content. Skip for media tokens like [IMAGE_ID:…]
        # since there is nothing linguistic to parse in them.
        is_parseable = msg.type in ("text", "audio") and not normalized_text.startswith("[")
        if is_parseable:
            norm_result = self._language_service.normalize(normalized_text)
            normalized_message_stored = norm_result.model_dump_json()
        else:
            normalized_message_stored = normalized_text

        # ---- persist ----------------------------------------------------------
        new_message = await self._message_repo.create_message(
            whatsapp_message_id=msg.id,
            shop_id=shop_id,
            sender_phone=sender_phone,
            sender_type=sender_type,
            message_type=msg.type,
            raw_message=raw_text,
            normalized_message=normalized_message_stored,
            processing_status="RECEIVED",
        )

        logger.info(
            "Inbound message ingested.",
            extra={
                "message_id": str(new_message.id),
                "whatsapp_message_id": msg.id,
                "type": msg.type,
                "shop_id": str(shop_id),
                "sender_type": sender_type,
                "stt_low_confidence": stt_low_confidence,
            },
        )
        return new_message, False

    async def _run_stt(
        self,
        media_id: str,
        mime_type: str,
        raw_fallback: str,
    ) -> Tuple[str, bool]:
        """Run the STT pipeline for an audio message.

        Returns:
            (normalized_text, stt_low_confidence)

        On low confidence or download failure the raw_fallback token is
        returned with stt_low_confidence=True so the caller can send a
        clarification prompt to the user.
        """
        speech = self._speech_service or SpeechService()
        access_token = settings.WHATSAPP_ACCESS_TOKEN or ""

        try:
            result = await speech.process_audio_message(
                media_id=media_id,
                access_token=access_token,
                mime_type=mime_type,
                language_hint=settings.DEFAULT_SHOP_LANGUAGE[:2],  # "ro" → "ur" etc.
                check_confidence=True,
            )
            return result.text, False

        except LowConfidenceTranscriptionError as exc:
            logger.warning(
                "STT low confidence — keeping audio token.",
                extra={
                    "media_id": media_id,
                    "confidence": exc.details.get("confidence"),
                    "partial": exc.details.get("partial_transcript", "")[:80],
                },
            )
            return raw_fallback, True

        except SpeechTranscriptionError as exc:
            logger.warning(
                "STT pipeline failed — keeping audio token.",
                extra={"media_id": media_id, "error": str(exc)},
            )
            return raw_fallback, True

    # ------------------------------------------------------------------
    # Delivery-status update handling
    # ------------------------------------------------------------------

    async def handle_status_update(self, status_update: WhatsAppStatusUpdate) -> None:
        """Log delivery/read receipts for outbound messages.

        Status updates (sent / delivered / read / failed) are logged for
        observability.  Full outbound-message tracking (matching against an
        outbound_messages table) is deferred to Module 24 — Observability.
        """
        log_level = "error" if status_update.status == "failed" else "info"
        getattr(logger, log_level)(
            "WhatsApp status update received.",
            extra={
                "outbound_message_id": status_update.id,
                "recipient": status_update.recipient_id,
                "status": status_update.status,
                "timestamp": status_update.timestamp,
                "errors": status_update.errors,
            },
        )

    # ------------------------------------------------------------------
    # Outbound messaging
    # ------------------------------------------------------------------

    async def send_text_message(
        self,
        to_phone: str,
        text_body: str,
        phone_number_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send an outbound text message via Meta WhatsApp Cloud API.

        Falls back to a simulation log when credentials are absent so that
        development/testing never requires a real Meta app to be configured.

        Raises WhatsAppError on a non-transient API failure.
        """
        pn_id = phone_number_id or settings.WHATSAPP_PHONE_NUMBER_ID

        if not settings.WHATSAPP_ACCESS_TOKEN or not pn_id:
            logger.info(
                "[SIMULATED WHATSAPP OUTBOUND]",
                extra={"to": to_phone, "body": text_body},
            )
            return {"status": "simulated", "to": to_phone, "text": text_body}

        url = (
            f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}"
            f"/{pn_id}/messages"
        )
        headers = {
            "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "text",
            "text": {"preview_url": False, "body": text_body},
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
            except httpx.RequestError as exc:
                logger.error(
                    "Network error sending WhatsApp message.",
                    extra={"to": to_phone, "error": str(exc)},
                )
                raise WhatsAppError(
                    f"Network error reaching Meta API: {exc}",
                    details={"to": to_phone},
                ) from exc

        if resp.status_code >= 400:
            logger.error(
                "Meta API returned error for outbound message.",
                extra={"to": to_phone, "status": resp.status_code, "body": resp.text},
            )
            raise WhatsAppError(
                f"Meta API error {resp.status_code}",
                details={"to": to_phone, "response": resp.text},
            )

        logger.info(
            "WhatsApp message sent successfully.",
            extra={"to": to_phone, "status": resp.status_code},
        )
        return resp.json()

    # ------------------------------------------------------------------
    # Batch processor — called by the route
    # ------------------------------------------------------------------

    async def process_webhook_payload(
        self, payload: WhatsAppWebhookPayload
    ) -> Dict[str, int]:
        """Process all entries in a validated webhook payload.

        For every new (non-duplicate) text or audio message the full AI
        pipeline is invoked via PipelineService:
          normalise → route → agent → WhatsApp reply

        Returns a dict with counts: ``processed``, ``duplicates``, ``statuses``.
        Does NOT commit the session — the route owns the transaction boundary.
        """
        # Import here to avoid circular import (pipeline_service imports this module)
        from backend.app.services.pipeline_service import PipelineService

        pipeline = PipelineService(db=self.session)

        processed = 0
        duplicates = 0
        status_updates = 0

        for entry in payload.entry:
            for change in entry.changes:
                val = change.value
                phone_number_id = val.metadata.phone_number_id

                # Inbound messages
                if val.messages:
                    for msg in val.messages:
                        persisted_msg, is_dup = await self.ingest_message(
                            msg=msg,
                            phone_number_id=phone_number_id,
                            sender_phone=msg.from_,
                        )
                        if is_dup:
                            duplicates += 1
                        else:
                            processed += 1
                            # Only run the AI pipeline for text / audio messages.
                            # Images, documents, stickers, etc. are stored for
                            # future modules but don't trigger the agent yet.
                            if msg.type in ("text", "audio"):
                                try:
                                    shop = await self.resolve_shop(phone_number_id)
                                    await pipeline.handle_message(
                                        message_id=persisted_msg.id,
                                        whatsapp_message_id=msg.id,
                                        raw_text=persisted_msg.raw_message,
                                        normalized_message_json=persisted_msg.normalized_message,
                                        sender_phone=msg.from_,
                                        shop_id=shop.id if shop else None,
                                        phone_number_id=phone_number_id,
                                    )
                                except Exception as exc:
                                    logger.error(
                                        "Pipeline error for message — sending fallback reply.",
                                        extra={
                                            "message_id": str(persisted_msg.id),
                                            "error": str(exc),
                                        },
                                    )
                                    # Best-effort fallback so the user always gets a response
                                    try:
                                        await self.send_text_message(
                                            to_phone=msg.from_,
                                            text_body=(
                                                "System mein temporary masla hai. "
                                                "Thodi der baad dobara try karein."
                                            ),
                                            phone_number_id=phone_number_id,
                                        )
                                    except Exception:
                                        pass

                # Delivery / read receipts
                if val.statuses:
                    for status_upd in val.statuses:
                        await self.handle_status_update(status_upd)
                        status_updates += 1

        return {
            "processed": processed,
            "duplicates": duplicates,
            "statuses": status_updates,
        }
