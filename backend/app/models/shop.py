"""Shop model representing multi-tenant businesses."""

import uuid
from typing import TYPE_CHECKING, Any, Dict, List
from sqlalchemy import JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.customer import Customer
    from backend.app.models.product import Product
    from backend.app.models.user import User


class Shop(Base, TimestampMixin):
    """Multi-tenant shop entity."""

    __tablename__ = "shops"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_number: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
    )
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Karachi", nullable=False)
    default_language: Mapped[str] = mapped_column(String(20), default="roman_urdu", nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="PKR", nullable=False)
    settings: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )

    # Relationships
    users: Mapped[List["User"]] = relationship(
        "User",
        back_populates="shop",
        cascade="all, delete-orphan",
    )
    customers: Mapped[List["Customer"]] = relationship(
        "Customer",
        back_populates="shop",
        cascade="all, delete-orphan",
    )
    products: Mapped[List["Product"]] = relationship(
        "Product",
        back_populates="shop",
        cascade="all, delete-orphan",
    )
