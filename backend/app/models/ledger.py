"""Immutable ledger model for financial credit and debit accounting."""

from decimal import Decimal
import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, TenantMixin

if TYPE_CHECKING:
    from backend.app.models.customer import Customer
    from backend.app.models.shop import Shop
    from backend.app.models.user import User


class LedgerType(str, enum.Enum):
    """Ledger transaction direction.

    DEBIT: Money owed by customer to shop (e.g., udhaar/credit sale). Increases balance due.
    CREDIT: Money paid by customer to shop (e.g., cash payment). Decreases balance due.
    """
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class ReferenceType(str, enum.Enum):
    """Source reference for the ledger entry."""
    ORDER = "ORDER"
    PAYMENT = "PAYMENT"
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"
    REFUND = "REFUND"


class LedgerEntry(Base, TenantMixin):
    """Append-only immutable financial ledger entry.

    Records are never updated or deleted. Corrections are made via new entries.
    """

    __tablename__ = "ledger"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[LedgerType] = mapped_column(
        Enum(LedgerType, name="ledger_type_enum", native_enum=False),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    reference_type: Mapped[ReferenceType] = mapped_column(
        Enum(ReferenceType, name="reference_type_enum", native_enum=False),
        default=ReferenceType.MANUAL_ADJUSTMENT,
        nullable=False,
    )
    reference_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    shop: Mapped["Shop"] = relationship("Shop")
    customer: Mapped["Customer"] = relationship("Customer")
    creator: Mapped[Optional["User"]] = relationship("User")
