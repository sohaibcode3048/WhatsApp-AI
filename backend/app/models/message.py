"""Message model for tracking incoming and outgoing WhatsApp messages."""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base

if TYPE_CHECKING:
    from backend.app.models.shop import Shop


class Message(Base):
    """WhatsApp message log storing raw incoming payloads, normalized text, and processing states."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    shop_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("shops.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    whatsapp_message_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )
    sender_phone: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    sender_type: Mapped[str] = mapped_column(String(20), default="CUSTOMER", nullable=False)
    message_type: Mapped[str] = mapped_column(String(20), default="text", nullable=False)
    raw_message: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    intent: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    processing_status: Mapped[str] = mapped_column(String(50), default="RECEIVED", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    shop: Mapped[Optional["Shop"]] = relationship("Shop")
