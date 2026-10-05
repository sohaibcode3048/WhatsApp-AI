"""Repository for managing orders and transactional stock decrements."""

from decimal import Decimal
import uuid
from typing import List, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.exceptions import NotFoundError, TenantIsolationError, ValidationError
from backend.app.models.order import Order, OrderStatus, PaymentStatus
from backend.app.models.order_item import OrderItem
from backend.app.models.product import Product
from backend.app.repositories.base_repository import TenantRepository
from backend.app.schemas.order import OrderCreate


class OrderRepository(TenantRepository[Order]):
    """Data access layer for orders and atomic inventory operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(Order, session)

    async def get_by_id_tenant(self, entity_id: uuid.UUID, shop_id: uuid.UUID) -> Optional[Order]:
        """Fetch order with items ensuring tenant isolation."""
        result = await self.session.execute(
            select(Order)
            .options(selectinload(Order.items).joinedload(OrderItem.product))
            .where(
                Order.id == entity_id,
                Order.shop_id == shop_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        shop_id: uuid.UUID,
        customer_id: Optional[uuid.UUID] = None,
        status: Optional[OrderStatus] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[Order]:
        """List orders for a tenant with optional customer or status filtering."""
        query = (
            select(Order)
            .options(selectinload(Order.items).joinedload(OrderItem.product))
            .where(Order.shop_id == shop_id)
        )
        if customer_id:
            query = query.where(Order.customer_id == customer_id)
        if status:
            query = query.where(Order.status == status)

        query = query.order_by(Order.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def create_order(
        self,
        shop_id: uuid.UUID,
        order_in: OrderCreate,
    ) -> Order:
        """Atomically validate stock, decrement inventory, and create order.

        Uses two-phase execution:
        Phase 1: Validate all products and ensure stock sufficiency across the board.
        Phase 2: Deduct stock, generate line items, and persist order.
        """
        validated_items: List[Tuple[Product, int, Decimal]] = []

        # Phase 1: Pre-validation of all line items
        for item_in in order_in.items:
            prod_result = await self.session.execute(
                select(Product).where(Product.id == item_in.product_id)
            )
            product = prod_result.scalar_one_or_none()
            if not product:
                raise NotFoundError(f"Product with ID '{item_in.product_id}' was not found.")
            if product.shop_id != shop_id:
                raise TenantIsolationError(
                    f"Product '{product.name}' does not belong to this shop.",
                    details={"product_id": str(product.id), "shop_id": str(shop_id)},
                )

            # Check stock availability
            if product.current_stock < item_in.quantity:
                raise ValidationError(
                    f"Insufficient stock for '{product.name}'. "
                    f"Available: {product.current_stock}, Requested: {item_in.quantity}",
                    details={
                        "product_id": str(product.id),
                        "available_stock": product.current_stock,
                        "requested_quantity": item_in.quantity,
                    },
                )

            unit_price = item_in.unit_price if item_in.unit_price is not None else product.price
            validated_items.append((product, item_in.quantity, unit_price))

        # Phase 2: Mutation and persistence
        total_amount = Decimal("0.00")
        order_items: List[OrderItem] = []

        for product, quantity, unit_price in validated_items:
            # Deduct inventory stock
            product.current_stock -= quantity

            subtotal = unit_price * quantity
            total_amount += subtotal

            order_item = OrderItem(
                product_id=product.id,
                quantity=quantity,
                unit_price=unit_price,
                subtotal=subtotal,
            )
            order_items.append(order_item)

        # Create master Order
        order = Order(
            shop_id=shop_id,
            customer_id=order_in.customer_id,
            status=OrderStatus.CONFIRMED,
            payment_status=PaymentStatus.UNPAID,
            total_amount=total_amount,
            notes=order_in.notes,
            items=order_items,
        )

        self.session.add(order)
        await self.session.flush()
        return order

    async def cancel_order(
        self,
        order_id: uuid.UUID,
        shop_id: uuid.UUID,
    ) -> Order:
        """Cancel an order and return inventory stock back to catalog."""
        order = await self.get_by_id_tenant(order_id, shop_id)
        if not order:
            raise NotFoundError(f"Order '{order_id}' not found.")

        if order.status == OrderStatus.CANCELLED:
            return order

        # Restore inventory stock for all line items
        for item in order.items:
            prod_result = await self.session.execute(
                select(Product).where(Product.id == item.product_id)
            )
            product = prod_result.scalar_one_or_none()
            if product:
                product.current_stock += item.quantity

        order.status = OrderStatus.CANCELLED
        await self.session.flush()
        return order
