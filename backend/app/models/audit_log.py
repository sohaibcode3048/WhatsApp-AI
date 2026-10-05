"""Audit Log model for immutable security, financial, and operational auditing."""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional
from sqlalchemy import DateTime, ForeignKey, JSON, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, TenantMixin

if TYPE_CHECKING:
    from backend.app.models.shop import Shop
    from backend.app.models.user import User


class AuditLog(Base, TenantMixin):
    """Immutable audit trail for all business-critical operations and tool invocations."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    message_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    tool_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    request_data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    approval_status: Mapped[str] = mapped_column(String(50), default="AUTO_APPROVED", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    shop: Mapped["Shop"] = relationship("Shop")
    user: Mapped[Optional["User"]] = relationship("User")
