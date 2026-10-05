"""User model representing owners, staff, and registered users."""

import enum
import uuid
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, Enum, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional

from backend.app.models.base import Base, TenantMixin, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.shop import Shop


class UserRole(str, enum.Enum):
    """Roles defining system access boundaries."""
    OWNER = "OWNER"
    STAFF = "STAFF"
    CUSTOMER = "CUSTOMER"
    UNKNOWN = "UNKNOWN"


class User(Base, TimestampMixin, TenantMixin):
    """User entity bound to a specific shop."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("shop_id", "phone_number", name="uq_users_shop_phone"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role_enum", native_enum=False),
        default=UserRole.CUSTOMER,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relationships
    shop: Mapped["Shop"] = relationship("Shop", back_populates="users")
