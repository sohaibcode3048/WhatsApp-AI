"""Order model representing customer orders and transactions."""

from decimal import Decimal
import enum
import uuid
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import Enum, ForeignKey, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, TenantMixin, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.customer import Customer
    from backend.app.models.order_item import OrderItem
    from backend.app.models.shop import Shop


class OrderStatus(str, enum.Enum):
    """Lifecycle statuses for customer orders."""
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class PaymentStatus(str, enum.Enum):
    """Payment settlement status for orders."""
    UNPAID = "UNPAID"
    PARTIAL = "PARTIAL"
    PAID = "PAID"


class Order(Base, TimestampMixin, TenantMixin):
    """Order placed in a shop."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    customer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status_enum", native_enum=False),
        default=OrderStatus.PENDING,
        nullable=False,
        index=True,
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status_enum", native_enum=False),
        default=PaymentStatus.UNPAID,
        nullable=False,
        index=True,
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0.00"),
        nullable=False,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    shop: Mapped["Shop"] = relationship("Shop")
    customer: Mapped[Optional["Customer"]] = relationship("Customer")
    items: Mapped[List["OrderItem"]] = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
