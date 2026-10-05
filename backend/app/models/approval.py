"""Approval model for high-value operation authorization — MODULE 16."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TYPE_CHECKING
from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.app.models.base import Base, TenantMixin, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.shop import Shop


class Approval(Base, TimestampMixin, TenantMixin):
    """Approval request for high-value / high-risk operations."""

    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    requested_by: Mapped[str] = mapped_column(String(50), nullable=False)
    approved_by: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    shop: Mapped["Shop"] = relationship("Shop")
