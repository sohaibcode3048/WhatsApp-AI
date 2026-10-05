"""Order Tools — MODULE 14.

Tools:
  create_order  — place a new order and atomically decrement stock
  get_order     — fetch one order with all line items
  cancel_order  — cancel an order and restore stock
"""

import uuid
from decimal import Decimal
from typing import List, Optional

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import NotFoundError, TenantIsolationError
from backend.app.models.order import OrderStatus
from backend.app.repositories.order_repository import OrderRepository
from backend.app.schemas.order import OrderCreate, OrderItemCreate
from backend.app.tools.base import (
    OWNER_AND_STAFF,
    BaseTool,
    ToolInput,
    ToolResult,
    tool_registry,
)


class OrderItemInput(OrderItemCreate):
    pass


class CreateOrderInput(ToolInput):
    customer_id: Optional[uuid.UUID] = None
    items: List[OrderItemInput] = Field(min_length=1)
    notes: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}


class GetOrderInput(ToolInput):
    order_id: uuid.UUID


class CancelOrderInput(ToolInput):
    order_id: uuid.UUID


class CreateOrderTool(BaseTool):
    name = "create_order"
    description = (
        "Place a new order for a customer. Atomically validates stock, "
        "decrements inventory, and creates the order record. "
        "Each item needs product_id and quantity."
    )
    allowed_roles = OWNER_AND_STAFF
    input_schema = CreateOrderInput

    def _check_business_rules(self, tool_input: CreateOrderInput) -> None:
        if not tool_input.items:
            from backend.app.core.exceptions import ValidationError
            raise ValidationError("Order must have at least one item.")

    async def _check_tenant_isolation(
        self, tool_input: CreateOrderInput, session: AsyncSession
    ) -> None:
        pass  # OrderRepository.create_order does product-level tenant checks

    async def _run(
        self, tool_input: CreateOrderInput, session: AsyncSession
    ) -> ToolResult:
        repo = OrderRepository(session)
        order = await repo.create_order(
            shop_id=tool_input.shop_id,
            order_in=OrderCreate(
                customer_id=tool_input.customer_id,
                items=[
                    OrderItemCreate(
                        product_id=item.product_id,
                        quantity=item.quantity,
                        unit_price=item.unit_price,
                    )
                    for item in tool_input.items
                ],
                notes=tool_input.notes,
            ),
        )

        await self._write_audit(
            session,
            tool_input,
            action="ORDER_CREATED",
            entity_type="order",
            entity_id=order.id,
            request_data={
                "customer_id": str(tool_input.customer_id) if tool_input.customer_id else None,
                "items": [
                    {"product_id": str(i.product_id), "quantity": i.quantity}
                    for i in tool_input.items
                ],
                "notes": tool_input.notes,
            },
            result_data={
                "order_id": str(order.id),
                "total_amount": str(order.total_amount),
                "status": order.status.value,
            },
        )

        items_summary = [
            {
                "product_id": str(item.product_id),
                "product_name": item.product.name if item.product else "Unknown",
                "quantity": item.quantity,
                "unit_price": str(item.unit_price),
                "subtotal": str(item.subtotal),
            }
            for item in order.items
        ]

        return ToolResult(
            success=True,
            tool_name=self.name,
            data={
                "order_id": str(order.id),
                "status": order.status.value,
                "total_amount": str(order.total_amount),
                "items": items_summary,
            },
            message=f"Order created. Total: Rs. {order.total_amount}.",
        )


class GetOrderTool(BaseTool):
    name = "get_order"
    description = "Fetch order details including all line items by order UUID."
    allowed_roles = OWNER_AND_STAFF
    input_schema = GetOrderInput

    def _check_business_rules(self, tool_input: GetOrderInput) -> None:
        pass

    async def _check_tenant_isolation(
        self, tool_input: GetOrderInput, session: AsyncSession
    ) -> None:
        repo = OrderRepository(session)
        order = await repo.get_by_id_tenant(tool_input.order_id, tool_input.shop_id)
        if not order:
            raise NotFoundError(
                f"Order {tool_input.order_id} not found.",
                details={"order_id": str(tool_input.order_id)},
            )

    async def _run(
        self, tool_input: GetOrderInput, session: AsyncSession
    ) -> ToolResult:
        repo = OrderRepository(session)
        order = await repo.get_by_id_tenant(tool_input.order_id, tool_input.shop_id)
        items_data = [
            {
                "product_id": str(item.product_id),
                "product_name": item.product.name if item.product else "Unknown",
                "quantity": item.quantity,
                "unit_price": str(item.unit_price),
                "subtotal": str(item.subtotal),
            }
            for item in order.items
        ]
        return ToolResult(
            success=True,
            tool_name=self.name,
            data={
                "order_id": str(order.id),
                "status": order.status.value,
                "payment_status": order.payment_status.value,
                "total_amount": str(order.total_amount),
                "items": items_data,
                "notes": order.notes,
            },
            message=f"Order {order.id}: {order.status.value}, Rs. {order.total_amount}.",
        )


class CancelOrderTool(BaseTool):
    name = "cancel_order"
    description = (
        "Cancel an order and restore all product stock levels. "
        "Already-cancelled orders are returned as-is (idempotent)."
    )
    allowed_roles = OWNER_AND_STAFF
    input_schema = CancelOrderInput

    def _check_business_rules(self, tool_input: CancelOrderInput) -> None:
        pass

    async def _check_tenant_isolation(
        self, tool_input: CancelOrderInput, session: AsyncSession
    ) -> None:
        repo = OrderRepository(session)
        order = await repo.get_by_id_tenant(tool_input.order_id, tool_input.shop_id)
        if not order:
            raise NotFoundError(
                f"Order {tool_input.order_id} not found.",
                details={"order_id": str(tool_input.order_id)},
            )

    async def _run(
        self, tool_input: CancelOrderInput, session: AsyncSession
    ) -> ToolResult:
        repo = OrderRepository(session)
        order = await repo.cancel_order(tool_input.order_id, tool_input.shop_id)

        await self._write_audit(
            session,
            tool_input,
            action="ORDER_CANCELLED",
            entity_type="order",
            entity_id=order.id,
            request_data={"order_id": str(tool_input.order_id)},
            result_data={"status": order.status.value},
        )

        return ToolResult(
            success=True,
            tool_name=self.name,
            data={
                "order_id": str(order.id),
                "status": order.status.value,
                "total_amount": str(order.total_amount),
            },
            message=f"Order {order.id} cancelled. Stock restored.",
        )


tool_registry.register(CreateOrderTool())
tool_registry.register(GetOrderTool())
tool_registry.register(CancelOrderTool())
