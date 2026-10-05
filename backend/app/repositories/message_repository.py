"""Repository for Message persistence and retrieval.

Provides idempotency-safe creation, status transitions, and tenant-scoped queries
for the Message model without any business logic leaking in.
"""

import uuid
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.message import Message
from backend.app.repositories.base_repository import BaseRepository


class MessageRepository(BaseRepository[Message]):
    """Data access layer for WhatsApp Message records."""

    def __init__(self, session: AsyncSession):
        super().__init__(Message, session)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_by_whatsapp_id(self, whatsapp_message_id: str) -> Optional[Message]:
        """Fetch a message by its Meta-assigned whatsapp_message_id.

        Used as the primary idempotency check on every inbound webhook.
        Returns None when the message has never been seen before.
        """
        result = await self.session.execute(
            select(Message).where(Message.whatsapp_message_id == whatsapp_message_id)
        )
        return result.scalar_one_or_none()

    async def list_by_shop(
        self,
        shop_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
        processing_status: Optional[str] = None,
    ) -> List[Message]:
        """Return messages scoped to a shop, optionally filtered by status.

        Results are ordered newest-first so callers always see the latest activity.
        """
        query = (
            select(Message)
            .where(Message.shop_id == shop_id)
            .order_by(Message.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        if processing_status is not None:
            query = query.where(Message.processing_status == processing_status)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_by_sender(
        self,
        shop_id: uuid.UUID,
        sender_phone: str,
        skip: int = 0,
        limit: int = 50,
    ) -> List[Message]:
        """Return all messages from a specific phone number within a shop."""
        result = await self.session.execute(
            select(Message)
            .where(
                Message.shop_id == shop_id,
                Message.sender_phone == sender_phone,
            )
            .order_by(Message.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create_message(
        self,
        *,
        whatsapp_message_id: str,
        shop_id: Optional[uuid.UUID],
        sender_phone: str,
        sender_type: str,
        message_type: str,
        raw_message: str,
        normalized_message: Optional[str] = None,
        intent: Optional[str] = None,
        processing_status: str = "RECEIVED",
    ) -> Message:
        """Persist a new inbound message record.

        Callers must verify idempotency *before* calling this method (use
        ``get_by_whatsapp_id`` first).  The session is flushed so the returned
        object has its ``id`` populated, but the transaction is **not** committed —
        that is the caller's responsibility.
        """
        message = Message(
            whatsapp_message_id=whatsapp_message_id,
            shop_id=shop_id,
            sender_phone=sender_phone,
            sender_type=sender_type,
            message_type=message_type,
            raw_message=raw_message,
            normalized_message=normalized_message or raw_message,
            intent=intent,
            processing_status=processing_status,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def update_status(
        self,
        message_id: uuid.UUID,
        processing_status: str,
        intent: Optional[str] = None,
    ) -> Optional[Message]:
        """Transition a message's processing_status (and optionally set intent).

        Valid transitions::

            RECEIVED → PROCESSING → COMPLETED
                                  → FAILED

        Uses a targeted UPDATE rather than a full ORM load to minimise
        round-trips.  Returns the refreshed Message object, or None if the
        message_id was not found.
        """
        values: dict = {"processing_status": processing_status}
        if intent is not None:
            values["intent"] = intent

        await self.session.execute(
            update(Message)
            .where(Message.id == message_id)
            .values(**values)
        )
        await self.session.flush()
        return await self.get_by_id(message_id)

    async def mark_processing(self, message_id: uuid.UUID) -> Optional[Message]:
        """Convenience: transition RECEIVED → PROCESSING."""
        return await self.update_status(message_id, "PROCESSING")

    async def mark_completed(
        self,
        message_id: uuid.UUID,
        intent: Optional[str] = None,
    ) -> Optional[Message]:
        """Convenience: transition PROCESSING → COMPLETED."""
        return await self.update_status(message_id, "COMPLETED", intent=intent)

    async def mark_failed(
        self,
        message_id: uuid.UUID,
        intent: Optional[str] = None,
    ) -> Optional[Message]:
        """Convenience: transition any status → FAILED."""
        return await self.update_status(message_id, "FAILED", intent=intent)
