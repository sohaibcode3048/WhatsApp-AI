"""Unit tests for Orders & Inventory: creation, calculation, stock decrements, and rollbacks."""

from decimal import Decimal
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import NotFoundError, TenantIsolationError, ValidationError
from backend.app.models.order import OrderStatus, PaymentStatus
from backend.app.repositories.customer_repository import CustomerRepository
from backend.app.repositories.order_repository import OrderRepository
from backend.app.repositories.product_repository import ProductRepository
from backend.app.repositories.shop_repository import ShopRepository
from backend.app.schemas.order import OrderCreate, OrderItemCreate


@pytest.mark.asyncio
async def test_create_order_calculates_total_and_reduces_stock(db_session: AsyncSession):
    """Verify order creation accurately computes line items, total amount, and reduces inventory."""
    shop_repo = ShopRepository(db_session)
    cust_repo = CustomerRepository(db_session)
    prod_repo = ProductRepository(db_session)
    order_repo = OrderRepository(db_session)

    shop = await shop_repo.create(name="Lahore Cash & Carry", phone_number="+923001239999")
    cust = await cust_repo.create(shop.id, name="Babar Azam", phone_number="+923007778888")

    # Product 1: Milk (stock 10 @ Rs. 200)
    p1 = await prod_repo.create(
        shop_id=shop.id,
        name="Olper's Milk 1L",
        price=Decimal("200.00"),
        current_stock=10,
    )
    # Product 2: Bread (stock 5 @ Rs. 150)
    p2 = await prod_repo.create(
        shop_id=shop.id,
        name="Dawn Bread Large",
        price=Decimal("150.00"),
        current_stock=5,
    )

    # Place order: 2 Milk (2 * 200 = 400) + 3 Bread (3 * 150 = 450) -> Total = 850
    order_in = OrderCreate(
        customer_id=cust.id,
        items=[
            OrderItemCreate(product_id=p1.id, quantity=2),
            OrderItemCreate(product_id=p2.id, quantity=3),
        ],
        notes="Urgent morning delivery",
    )

    order = await order_repo.create_order(shop.id, order_in)

    assert order.id is not None
    assert order.total_amount == Decimal("850.00")
    assert order.status == OrderStatus.CONFIRMED
    assert order.payment_status == PaymentStatus.UNPAID
    assert len(order.items) == 2

    # Verify inventory was reduced
    await db_session.refresh(p1)
    await db_session.refresh(p2)
    assert p1.current_stock == 8   # 10 - 2 = 8
    assert p2.current_stock == 2   # 5 - 3 = 2


@pytest.mark.asyncio
async def test_insufficient_stock_raises_error_and_rolls_back(db_session: AsyncSession):
    """Verify ordering more than available stock raises ValidationError without modifying inventory."""
    shop_repo = ShopRepository(db_session)
    prod_repo = ProductRepository(db_session)
    order_repo = OrderRepository(db_session)

    shop = await shop_repo.create(name="Super Mart", phone_number="+923004445555")
    p1 = await prod_repo.create(
        shop_id=shop.id,
        name="Cooking Oil 5L",
        price=Decimal("2500.00"),
        current_stock=2,
    )
    await db_session.commit()

    # Try to order 5 when only 2 are in stock
    order_in = OrderCreate(
        items=[OrderItemCreate(product_id=p1.id, quantity=5)],
    )

    with pytest.raises(ValidationError) as exc_info:
        await order_repo.create_order(shop.id, order_in)

    assert "Insufficient stock" in str(exc_info.value)

    # Stock must remain completely unchanged
    await db_session.refresh(p1)
    assert p1.current_stock == 2


@pytest.mark.asyncio
async def test_cancel_order_restores_inventory_stock(db_session: AsyncSession):
    """Verify cancelling an order properly restores line item quantities back to product stock."""
    shop_repo = ShopRepository(db_session)
    prod_repo = ProductRepository(db_session)
    order_repo = OrderRepository(db_session)

    shop = await shop_repo.create(name="Fast Grocery", phone_number="+923008889999")
    product = await prod_repo.create(
        shop_id=shop.id,
        name="Green Tea Box",
        price=Decimal("450.00"),
        current_stock=10,
    )

    # Create order for 4 items -> stock becomes 6
    order = await order_repo.create_order(
        shop.id,
        OrderCreate(items=[OrderItemCreate(product_id=product.id, quantity=4)]),
    )
    await db_session.refresh(product)
    assert product.current_stock == 6

    # Cancel the order
    cancelled_order = await order_repo.cancel_order(order.id, shop.id)
    assert cancelled_order.status == OrderStatus.CANCELLED

    # Stock must be restored to 10
    await db_session.refresh(product)
    assert product.current_stock == 10


@pytest.mark.asyncio
async def test_order_tenant_isolation(db_session: AsyncSession):
    """Ensure Shop A cannot place an order using Shop B's product, or view Shop B's orders."""
    shop_repo = ShopRepository(db_session)
    prod_repo = ProductRepository(db_session)
    order_repo = OrderRepository(db_session)

    shop_a = await shop_repo.create(name="Shop Alpha", phone_number="+923010000001")
    shop_b = await shop_repo.create(name="Shop Beta", phone_number="+923010000002")

    prod_b = await prod_repo.create(
        shop_id=shop_b.id,
        name="Shop B Exclusive Item",
        price=Decimal("1000.00"),
        current_stock=10,
    )

    # Shop A attempts to buy Shop B's product
    order_in = OrderCreate(items=[OrderItemCreate(product_id=prod_b.id, quantity=1)])

    with pytest.raises(TenantIsolationError):
        await order_repo.create_order(shop_a.id, order_in)

    # Place valid order in Shop B
    order_b = await order_repo.create_order(shop_b.id, order_in)

    # Shop A attempts to fetch Shop B's order by ID
    lookup = await order_repo.get_by_id_tenant(order_b.id, shop_id=shop_a.id)
    assert lookup is None, "Security breach: Shop A accessed Shop B order!"
