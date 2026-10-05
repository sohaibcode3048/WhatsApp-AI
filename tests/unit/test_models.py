"""Unit tests for core SQLAlchemy declarative entities."""

from decimal import Decimal
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.customer import Customer
from backend.app.models.product import Product
from backend.app.models.shop import Shop
from backend.app.models.user import User, UserRole


@pytest.mark.asyncio
async def test_shop_and_user_creation(db_session: AsyncSession):
    """Test Shop and Owner user creation with relationships."""
    shop = Shop(
        name="Madina Super Store",
        phone_number="+923001234567",
        address="Commercial Market, Rawalpindi",
        settings={"sms_reminders": True},
    )
    db_session.add(shop)
    await db_session.flush()

    assert isinstance(shop.id, uuid.UUID)
    assert shop.currency == "PKR"
    assert shop.settings["sms_reminders"] is True

    user = User(
        shop_id=shop.id,
        name="Haji Muhammad",
        phone_number="+923001234567",
        role=UserRole.OWNER,
    )
    db_session.add(user)
    await db_session.flush()

    assert user.role == UserRole.OWNER
    assert user.is_active is True
    assert user.created_at is not None


@pytest.mark.asyncio
async def test_product_model_and_low_stock_property(db_session: AsyncSession):
    """Verify product entity stock tracking and low stock helper."""
    shop = Shop(
        name="Bismillah Kiryana",
        phone_number="+923119876543",
    )
    db_session.add(shop)
    await db_session.flush()

    # Product with normal stock
    p1 = Product(
        shop_id=shop.id,
        name="Basmati Rice 5kg",
        price=Decimal("1750.00"),
        cost_price=Decimal("1500.00"),
        current_stock=20,
        low_stock_threshold=5,
    )
    # Product with low stock
    p2 = Product(
        shop_id=shop.id,
        name="Cooking Oil 1L",
        price=Decimal("520.00"),
        current_stock=3,
        low_stock_threshold=5,
    )
    db_session.add_all([p1, p2])
    await db_session.flush()

    assert p1.is_low_stock is False
    assert p2.is_low_stock is True
