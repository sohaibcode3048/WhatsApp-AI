"""Human Handoff & Fallback Handling — MODULE 22.

When the agent cannot safely process a request it triggers a handoff event.
This module:
  - Defines the HumanHandoffEvent dataclass.
  - Provides FallbackHandler which stores the event and optionally notifies
    the shop owner via WhatsApp.
  - Classifies why a handoff was triggered (UNCERTAIN / UNSUPPORTED /
    TOOL_FAILURE / CRITICAL_ERROR / PROMPT_INJECTION).

The agent raises HumanHandoffRequired (from core.exceptions); the pipeline
service catches it and delegates to FallbackHandler.handle().
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from backend.app.core.logging import logger


class HandoffReason(str, Enum):
    UNCERTAIN = "UNCERTAIN"
    UNSUPPORTED = "UNSUPPORTED"
    TOOL_FAILURE = "TOOL_FAILURE"
    CRITICAL_ERROR = "CRITICAL_ERROR"
    PROMPT_INJECTION = "PROMPT_INJECTION"
    LOW_CONFIDENCE_STT = "LOW_CONFIDENCE_STT"


@dataclass
class HumanHandoffEvent:
    """Immutable record of one handoff event."""

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    shop_id: Optional[str] = None
    sender_phone: Optional[str] = None
    message: str = ""
    reason: HandoffReason = HandoffReason.UNCERTAIN
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "shop_id": self.shop_id,
            "sender_phone": self.sender_phone,
            "message": self.message,
            "reason": self.reason.value,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


# In-process store (Redis-backed persistence is a future enhancement)
_HANDOFF_LOG: list[HumanHandoffEvent] = []


class FallbackHandler:
    """Manage human handoff events and owner notifications."""

    async def handle(
        self,
        shop_id: Optional[str],
        sender_phone: Optional[str],
        message: str,
        reason: HandoffReason = HandoffReason.UNCERTAIN,
        metadata: Optional[Dict[str, Any]] = None,
        owner_phone: Optional[str] = None,
        whatsapp_service=None,
        phone_number_id: Optional[str] = None,
    ) -> HumanHandoffEvent:
        """Record a handoff event and optionally notify the shop owner.

        Args:
            shop_id:           Tenant identifier.
            sender_phone:      Phone number of the user who triggered handoff.
            message:           The original user message that could not be handled.
            reason:            Classified reason for the handoff.
            metadata:          Extra context (error details, confidence scores, …).
            owner_phone:       If set, sends a WhatsApp notification to the owner.
            whatsapp_service:  WhatsAppService instance for sending the notification.
            phone_number_id:   Meta phone number ID for outbound messaging.

        Returns:
            The created HumanHandoffEvent.
        """
        event = HumanHandoffEvent(
            shop_id=shop_id,
            sender_phone=sender_phone,
            message=message,
            reason=reason,
            metadata=metadata or {},
        )
        _HANDOFF_LOG.append(event)

        logger.warning(
            "Human handoff triggered.",
            extra={
                "event_id": event.event_id,
                "reason": reason.value,
                "shop_id": shop_id,
                "sender_phone": sender_phone,
                "message_preview": message[:80],
            },
        )

        # Notify owner if possible
        if owner_phone and whatsapp_service:
            notification = (
                f"⚠️ Handoff Alert\n"
                f"Customer: {sender_phone}\n"
                f"Reason: {reason.value}\n"
                f"Message: {message[:120]}"
            )
            try:
                await whatsapp_service.send_text_message(
                    to_phone=owner_phone,
                    text_body=notification,
                    phone_number_id=phone_number_id,
                )
            except Exception as exc:
                logger.error(
                    "Failed to send handoff notification to owner.",
                    extra={"error": str(exc)},
                )

        return event

    def get_recent_events(self, limit: int = 50) -> list[HumanHandoffEvent]:
        """Return the most recent handoff events (newest first)."""
        return list(reversed(_HANDOFF_LOG[-limit:]))

    def count(self) -> int:
        return len(_HANDOFF_LOG)


# Singleton
fallback_handler = FallbackHandler()
