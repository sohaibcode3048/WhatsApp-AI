"""Customer model representing retail customers with credit/order history."""

import uuid
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, TenantMixin, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.shop import Shop


class Customer(Base, TimestampMixin, TenantMixin):
    """Customer entity registered within a shop."""

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("shop_id", "phone_number", name="uq_customers_shop_phone"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    shop: Mapped["Shop"] = relationship("Shop", back_populates="customers")
