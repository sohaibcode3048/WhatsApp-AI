"""WhatsApp Cloud API webhook endpoints.

Two endpoints:
  GET  /webhook/whatsapp  — Meta subscription challenge handshake
  POST /webhook/whatsapp  — Inbound message & status-update ingestion

Design notes:
  - Raw body is read once and stored in request.state so Pydantic can also
    parse the JSON without a second read.
  - Signature verification runs before any database work.
  - Only payloads with object == "whatsapp_business_account" are processed;
    everything else is acknowledged with 200 and ignored.
  - After persisting each message, PipelineService.handle_message() is called
    to run the full AI pipeline: normalise → route → agent → reply.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import get_db_session
from backend.app.core.logging import logger
from backend.app.schemas.whatsapp import WhatsAppWebhookPayload
from backend.app.services.whatsapp_service import WhatsAppService

router = APIRouter(prefix="/webhook/whatsapp", tags=["WhatsApp Webhook"])

_EXPECTED_OBJECT = "whatsapp_business_account"


# ---------------------------------------------------------------------------
# GET — Meta challenge verification
# ---------------------------------------------------------------------------


@router.get(
    "",
    summary="Meta Webhook Verification Handshake",
    description=(
        "Called by Meta once when a new webhook subscription is registered. "
        "Validates hub.verify_token and echoes back hub.challenge."
    ),
)
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Echo Meta's challenge string after verifying the subscription token."""
    service = WhatsAppService(db)
    challenge = service.verify_webhook_token(hub_mode, hub_verify_token, hub_challenge)
    return Response(content=challenge, media_type="text/plain", status_code=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST — Inbound message & status ingestion
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    summary="Meta Webhook Incoming Events",
    description=(
        "Receives all inbound WhatsApp events: messages (text, audio, image, "
        "document, video, sticker, location, interactive, reaction) and delivery "
        "status updates (sent / delivered / read / failed).  "
        "HMAC-SHA256 signature is verified before any processing.  "
        "Each new text/audio message is passed through the full AI pipeline."
    ),
)
async def receive_webhook(
    request: Request,
    payload: WhatsAppWebhookPayload,
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Verify, ingest, process and acknowledge a Meta webhook payload."""

    # ---- signature verification ------------------------------------------------
    raw_body = await request.body()
    service = WhatsAppService(db)
    service.verify_signature(raw_body, x_hub_signature_256)

    # ---- object-type guard -----------------------------------------------------
    if payload.object != _EXPECTED_OBJECT:
        logger.info(
            "Received non-WhatsApp webhook object — ignoring.",
            extra={"object": payload.object},
        )
        return {
            "status": "ignored",
            "reason": f"Unsupported object type: {payload.object}",
            "processed": 0,
            "duplicates": 0,
            "statuses": 0,
        }

    # ---- ingest messages + run AI pipeline in one transaction ------------------
    counts = await service.process_webhook_payload(payload)
    await db.commit()

    logger.info("Webhook payload processed.", extra=counts)
    return {"status": "success", **counts}
