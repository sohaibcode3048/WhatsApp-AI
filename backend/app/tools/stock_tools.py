"""Stock Tools — MODULE 11.

Tools:
  check_stock   — read current stock level for a product
  update_stock  — adjust stock (add or remove units)
  get_low_stock — list products at or below their low_stock_threshold
"""

import uuid
from decimal import Decimal
from typing import Optional

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import NotFoundError, TenantIsolationError, ValidationError
from backend.app.repositories.product_repository import ProductRepository
from backend.app.schemas.router import SenderRole
from backend.app.tools.base import (
    OWNER_AND_STAFF,
    ALL_AUTHENTICATED,
    BaseTool,
    ToolInput,
    ToolResult,
    tool_registry,
)


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------


class CheckStockInput(ToolInput):
    product_id: uuid.UUID


class UpdateStockInput(ToolInput):
    product_id: uuid.UUID
    quantity_change: int = Field(..., description="Positive = add, negative = remove")
    note: Optional[str] = None


class GetLowStockInput(ToolInput):
    pass   # only shop_id needed — inherited from ToolInput


# ---------------------------------------------------------------------------
# check_stock
# ---------------------------------------------------------------------------


class CheckStockTool(BaseTool):
    name = "check_stock"
    description = (
        "Check current stock level for a single product. "
        "Returns product name, SKU, current_stock, and low_stock_threshold."
    )
    allowed_roles = ALL_AUTHENTICATED
    input_schema = CheckStockInput

    def _check_business_rules(self, tool_input: CheckStockInput) -> None:
        pass   # read-only, no business rules needed

    async def _check_tenant_isolation(
        self, tool_input: CheckStockInput, session: AsyncSession
    ) -> None:
        repo = ProductRepository(session)
        product = await repo.get_by_id(tool_input.product_id)
        if not product:
            raise NotFoundError(
                f"Product {tool_input.product_id} not found.",
                details={"product_id": str(tool_input.product_id)},
            )
        if product.shop_id != tool_input.shop_id:
            raise TenantIsolationError(
                f"Product {tool_input.product_id} does not belong to this shop."
            )

    async def _run(
        self, tool_input: CheckStockInput, session: AsyncSession
    ) -> ToolResult:
        repo = ProductRepository(session)
        product = await repo.get_by_id(tool_input.product_id)
        return ToolResult(
            success=True,
            tool_name=self.name,
            data={
                "product_id": str(product.id),
                "name": product.name,
                "sku": product.sku,
                "current_stock": product.current_stock,
                "low_stock_threshold": product.low_stock_threshold,
                "is_low": product.is_low_stock,
            },
            message=(
                f"{product.name}: {product.current_stock} units in stock"
                + (" (LOW STOCK)" if product.is_low_stock else "")
            ),
        )


# ---------------------------------------------------------------------------
# update_stock
# ---------------------------------------------------------------------------


class UpdateStockTool(BaseTool):
    name = "update_stock"
    description = (
        "Adjust product stock. Use positive quantity_change to add units, "
        "negative to remove. Must not result in stock below 0."
    )
    allowed_roles = OWNER_AND_STAFF
    input_schema = UpdateStockInput

    def _check_business_rules(self, tool_input: UpdateStockInput) -> None:
        if tool_input.quantity_change == 0:
            raise ValidationError(
                "quantity_change cannot be zero.",
                details={"quantity_change": tool_input.quantity_change},
            )

    async def _check_tenant_isolation(
        self, tool_input: UpdateStockInput, session: AsyncSession
    ) -> None:
        repo = ProductRepository(session)
        product = await repo.get_by_id(tool_input.product_id)
        if not product:
            raise NotFoundError(
                f"Product {tool_input.product_id} not found.",
                details={"product_id": str(tool_input.product_id)},
            )
        if product.shop_id != tool_input.shop_id:
            raise TenantIsolationError(
                f"Product {tool_input.product_id} does not belong to this shop."
            )

    async def _run(
        self, tool_input: UpdateStockInput, session: AsyncSession
    ) -> ToolResult:
        repo = ProductRepository(session)
        product = await repo.get_by_id(tool_input.product_id)

        new_stock = product.current_stock + tool_input.quantity_change
        if new_stock < 0:
            raise ValidationError(
                f"Stock cannot go below 0. Current: {product.current_stock}, "
                f"change: {tool_input.quantity_change}.",
                details={
                    "current_stock": product.current_stock,
                    "quantity_change": tool_input.quantity_change,
                    "would_be": new_stock,
                },
            )

        old_stock = product.current_stock
        product.current_stock = new_stock
        await session.flush()

        await self._write_audit(
            session,
            tool_input,
            action="STOCK_UPDATE",
            entity_type="product",
            entity_id=product.id,
            request_data={
                "product_id": str(product.id),
                "quantity_change": tool_input.quantity_change,
                "note": tool_input.note,
            },
            result_data={"old_stock": old_stock, "new_stock": new_stock},
        )

        direction = "added" if tool_input.quantity_change > 0 else "removed"
        return ToolResult(
            success=True,
            tool_name=self.name,
            data={
                "product_id": str(product.id),
                "name": product.name,
                "old_stock": old_stock,
                "new_stock": new_stock,
                "quantity_change": tool_input.quantity_change,
            },
            message=(
                f"{abs(tool_input.quantity_change)} units {direction} for "
                f"{product.name}. New stock: {new_stock}."
            ),
        )


# ---------------------------------------------------------------------------
# get_low_stock
# ---------------------------------------------------------------------------


class GetLowStockTool(BaseTool):
    name = "get_low_stock"
    description = (
        "List all products in the shop whose current_stock is at or below "
        "their configured low_stock_threshold."
    )
    allowed_roles = OWNER_AND_STAFF
    input_schema = GetLowStockInput

    def _check_business_rules(self, tool_input: GetLowStockInput) -> None:
        pass

    async def _check_tenant_isolation(
        self, tool_input: GetLowStockInput, session: AsyncSession
    ) -> None:
        pass   # list query is already scoped to shop_id

    async def _run(
        self, tool_input: GetLowStockInput, session: AsyncSession
    ) -> ToolResult:
        repo = ProductRepository(session)
        products = await repo.get_low_stock_products(tool_input.shop_id)
        items = [
            {
                "product_id": str(p.id),
                "name": p.name,
                "current_stock": p.current_stock,
                "low_stock_threshold": p.low_stock_threshold,
            }
            for p in products
        ]
        return ToolResult(
            success=True,
            tool_name=self.name,
            data={"low_stock_products": items, "count": len(items)},
            message=(
                f"{len(items)} product(s) are low on stock."
                if items
                else "All products have sufficient stock."
            ),
        )


# Register all stock tools
tool_registry.register(CheckStockTool())
tool_registry.register(UpdateStockTool())
tool_registry.register(GetLowStockTool())
