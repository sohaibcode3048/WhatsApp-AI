"""Unit tests for Repositories and Tenant Isolation enforcement."""

from decimal import Decimal
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.user import UserRole
from backend.app.repositories.customer_repository import CustomerRepository
from backend.app.repositories.product_repository import ProductRepository
from backend.app.repositories.shop_repository import ShopRepository
from backend.app.repositories.user_repository import UserRepository


@pytest.mark.asyncio
async def test_shop_and_user_repository(db_session: AsyncSession):
    """Test Shop and User repository operations."""
    shop_repo = ShopRepository(db_session)
    user_repo = UserRepository(db_session)

    shop = await shop_repo.create(
        name="Al-Rehman General Store",
        phone_number="+923011112233",
        address="F-10 Markaz, Islamabad",
    )
    assert shop.id is not None

    found_shop = await shop_repo.get_by_phone("+923011112233")
    assert found_shop is not None
    assert found_shop.id == shop.id

    # Create Owner user
    user = await user_repo.create(
        shop_id=shop.id,
        name="Rehman",
        phone_number="+923011112233",
        role=UserRole.OWNER,
    )
    assert user.id is not None

    fetched_user = await user_repo.get_by_phone_and_shop("+923011112233", shop.id)
    assert fetched_user is not None
    assert fetched_user.role == UserRole.OWNER


@pytest.mark.asyncio
async def test_customer_repository_tenant_isolation(db_session: AsyncSession):
    """CRITICAL SECURITY TEST: Ensure Shop A cannot access or see Shop B customers."""
    shop_repo = ShopRepository(db_session)
    cust_repo = CustomerRepository(db_session)

    shop_a = await shop_repo.create(name="Shop Alpha", phone_number="+923000000001")
    shop_b = await shop_repo.create(name="Shop Beta", phone_number="+923000000002")

    # Create customer with same name in both shops
    cust_a = await cust_repo.create(
        shop_id=shop_a.id,
        name="Muhammad Ali",
        phone_number="+923211111111",
        notes="Regular customer at Shop A",
    )
    cust_b = await cust_repo.create(
        shop_id=shop_b.id,
        name="Muhammad Ali",
        phone_number="+923222222222",
        notes="Customer at Shop B",
    )

    # 1. Search for 'Ali' from Shop A perspective
    results_a = await cust_repo.find_by_name("ali", shop_id=shop_a.id)
    assert len(results_a) == 1
    assert results_a[0].id == cust_a.id
    assert results_a[0].phone_number == "+923211111111"

    # 2. Search for 'Ali' from Shop B perspective
    results_b = await cust_repo.find_by_name("Ali", shop_id=shop_b.id)
    assert len(results_b) == 1
    assert results_b[0].id == cust_b.id
    assert results_b[0].phone_number == "+923222222222"

    # 3. Direct ID lookup across tenant boundaries MUST return None
    cross_lookup = await cust_repo.get_by_id_tenant(cust_b.id, shop_id=shop_a.id)
    assert cross_lookup is None, "Security violation: Shop A accessed Shop B customer by ID!"


@pytest.mark.asyncio
async def test_product_repository_tenant_isolation_and_low_stock(db_session: AsyncSession):
    """Ensure product catalog and inventory searches are strictly isolated per tenant."""
    shop_repo = ShopRepository(db_session)
    prod_repo = ProductRepository(db_session)

    shop_a = await shop_repo.create(name="Beverage Point", phone_number="+923001112201")
    shop_b = await shop_repo.create(name="Corner Mart", phone_number="+923001112202")

    # Product in Shop A (low stock)
    prod_a = await prod_repo.create(
        shop_id=shop_a.id,
        name="Coca Cola 1.5L",
        price=Decimal("250.00"),
        current_stock=2,
        low_stock_threshold=5,
        sku="COKE-15",
    )

    # Product in Shop B (plenty of stock)
    prod_b = await prod_repo.create(
        shop_id=shop_b.id,
        name="Coca Cola 1.5L",
        price=Decimal("240.00"),
        current_stock=50,
        low_stock_threshold=5,
        sku="COKE-15",
    )

    # Low stock query for Shop A must return 1 product
    low_a = await prod_repo.list_low_stock(shop_id=shop_a.id)
    assert len(low_a) == 1
    assert low_a[0].id == prod_a.id

    # Low stock query for Shop B must return 0 products
    low_b = await prod_repo.list_low_stock(shop_id=shop_b.id)
    assert len(low_b) == 0

    # Cross-tenant SKU lookup
    sku_lookup_a = await prod_repo.get_by_sku("COKE-15", shop_id=shop_a.id)
    assert sku_lookup_a is not None
    assert sku_lookup_a.price == Decimal("250.00")

    cross_tenant_lookup = await prod_repo.get_by_id_tenant(prod_b.id, shop_id=shop_a.id)
    assert cross_tenant_lookup is None, "Security violation: Shop A accessed Shop B product by ID!"
