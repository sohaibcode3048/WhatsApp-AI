"""Product model representing inventory and catalog items."""

from decimal import Decimal
import uuid
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, TenantMixin, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.shop import Shop


class Product(Base, TimestampMixin, TenantMixin):
    """Product catalog item with real-time stock tracking."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("shop_id", "sku", name="uq_products_shop_sku"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    current_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    shop: Mapped["Shop"] = relationship("Shop", back_populates="products")

    @property
    def is_low_stock(self) -> bool:
        """Helper checking if current stock is at or below alert threshold."""
        return self.current_stock <= self.low_stock_threshold
