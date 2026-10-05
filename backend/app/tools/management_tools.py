"""Management Tools — add/update customers and products via the agent.

Tools:
  add_customer    — register a new customer in the shop
  add_product     — add a new product to the catalog
  update_product  — update price, stock threshold, or description
  find_product    — search products by name (readable by all roles)
"""

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import ValidationError
from backend.app.repositories.customer_repository import CustomerRepository
from backend.app.repositories.product_repository import ProductRepository
from backend.app.tools.base import (
    ALL_AUTHENTICATED,
    OWNER_AND_STAFF,
    OWNER_ONLY,
    BaseTool,
    ToolInput,
    ToolResult,
    tool_registry,
)


# ---------------------------------------------------------------------------
# add_customer
# ---------------------------------------------------------------------------


class AddCustomerInput(ToolInput):
    name: str
    phone_number: str
    notes: Optional[str] = None


class AddCustomerTool(BaseTool):
    name = "add_customer"
    description = (
        "Register a new customer in the shop. "
        "Requires name and phone_number. "
        "Returns the new customer's UUID."
    )
    allowed_roles = OWNER_AND_STAFF
    input_schema = AddCustomerInput

    def _check_business_rules(self, tool_input: AddCustomerInput) -> None:
        if not tool_input.name.strip():
            raise ValidationError("Customer name cannot be blank.")
        if not tool_input.phone_number.strip():
            raise ValidationError("Customer phone_number cannot be blank.")

    async def _check_tenant_isolation(
        self, tool_input: AddCustomerInput, session: AsyncSession
    ) -> None:
        pass  # no existing entity to verify; shop_id from ToolInput

    async def _run(
        self, tool_input: AddCustomerInput, session: AsyncSession
    ) -> ToolResult:
        repo = CustomerRepository(session)

        # Check for existing customer with same phone
        existing = await repo.get_by_phone(tool_input.phone_number, tool_input.shop_id)
        if existing:
            return ToolResult(
                success=True,
                tool_name=self.name,
                data={
                    "customer_id": str(existing.id),
                    "name": existing.name,
                    "phone_number": existing.phone_number,
                    "already_existed": True,
                },
                message=(
                    f"Customer '{existing.name}' already exists "
                    f"with phone {existing.phone_number}."
                ),
            )

        customer = await repo.create(
            shop_id=tool_input.shop_id,
            name=tool_input.name,
            phone_number=tool_input.phone_number,
            notes=tool_input.notes,
        )

        await self._write_audit(
            session,
            tool_input,
            action="CUSTOMER_CREATED",
            entity_type="customer",
            entity_id=customer.id,
            request_data={
                "name": tool_input.name,
                "phone_number": tool_input.phone_number,
            },
            result_data={"customer_id": str(customer.id)},
        )

        return ToolResult(
            success=True,
            tool_name=self.name,
            data={
                "customer_id": str(customer.id),
                "name": customer.name,
                "phone_number": customer.phone_number,
                "already_existed": False,
            },
            message=f"Customer '{customer.name}' added successfully.",
        )


# ---------------------------------------------------------------------------
# add_product
# ---------------------------------------------------------------------------


class AddProductInput(ToolInput):
    name: str
    price: Decimal
    cost_price: Optional[Decimal] = None
    current_stock: int = 0
    low_stock_threshold: int = 5
    sku: Optional[str] = None
    description: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}


class AddProductTool(BaseTool):
    name = "add_product"
    description = (
        "Add a new product to the shop catalog. "
        "Requires name and price. SKU is optional but recommended."
    )
    allowed_roles = OWNER_ONLY
    input_schema = AddProductInput

    def _check_business_rules(self, tool_input: AddProductInput) -> None:
        if not tool_input.name.strip():
            raise ValidationError("Product name cannot be blank.")
        if tool_input.price <= Decimal("0"):
            raise ValidationError(
                "Product price must be greater than zero.",
                details={"price": str(tool_input.price)},
            )
        if tool_input.current_stock < 0:
            raise ValidationError("current_stock cannot be negative.")
        if tool_input.low_stock_threshold < 0:
            raise ValidationError("low_stock_threshold cannot be negative.")

    async def _check_tenant_isolation(
        self, tool_input: AddProductInput, session: AsyncSession
    ) -> None:
        pass

    async def _run(
        self, tool_input: AddProductInput, session: AsyncSession
    ) -> ToolResult:
        repo = ProductRepository(session)

        # Check for existing product with same SKU
        if tool_input.sku:
            existing = await repo.get_by_sku(tool_input.sku, tool_input.shop_id)
            if existing:
                return ToolResult(
                    success=False,
                    tool_name=self.name,
                    data={"sku": tool_input.sku, "existing_id": str(existing.id)},
                    message=(
                        f"Product with SKU '{tool_input.sku}' already exists: "
                        f"'{existing.name}'."
                    ),
                )

        product = await repo.create(
            shop_id=tool_input.shop_id,
            name=tool_input.name,
            price=tool_input.price,
            cost_price=tool_input.cost_price,
            current_stock=tool_input.current_stock,
            low_stock_threshold=tool_input.low_stock_threshold,
            sku=tool_input.sku,
            description=tool_input.description,
        )

        await self._write_audit(
            session,
            tool_input,
            action="PRODUCT_CREATED",
            entity_type="product",
            entity_id=product.id,
            request_data={
                "name": tool_input.name,
                "price": str(tool_input.price),
                "sku": tool_input.sku,
                "current_stock": tool_input.current_stock,
            },
            result_data={"product_id": str(product.id)},
        )

        return ToolResult(
            success=True,
            tool_name=self.name,
            data={
                "product_id": str(product.id),
                "name": product.name,
                "price": str(product.price),
                "current_stock": product.current_stock,
                "sku": product.sku,
            },
            message=f"Product '{product.name}' added. Price: Rs. {product.price}.",
        )


# ---------------------------------------------------------------------------
# update_product
# ---------------------------------------------------------------------------


class UpdateProductInput(ToolInput):
    product_id: uuid.UUID
    price: Optional[Decimal] = None
    cost_price: Optional[Decimal] = None
    description: Optional[str] = None
    low_stock_threshold: Optional[int] = None
    is_active: Optional[bool] = None

    model_config = {"arbitrary_types_allowed": True}


class UpdateProductTool(BaseTool):
    name = "update_product"
    description = (
        "Update an existing product's price, description, low_stock_threshold, "
        "or active status. Only fields provided are changed."
    )
    allowed_roles = OWNER_ONLY
    input_schema = UpdateProductInput

    def _check_business_rules(self, tool_input: UpdateProductInput) -> None:
        if tool_input.price is not None and tool_input.price <= Decimal("0"):
            raise ValidationError("price must be greater than zero.")
        if tool_input.low_stock_threshold is not None and tool_input.low_stock_threshold < 0:
            raise ValidationError("low_stock_threshold cannot be negative.")
        # Require at least one field to be updated
        fields = [
            tool_input.price, tool_input.cost_price, tool_input.description,
            tool_input.low_stock_threshold, tool_input.is_active,
        ]
        if all(f is None for f in fields):
            raise ValidationError("At least one field must be provided to update.")

    async def _check_tenant_isolation(
        self, tool_input: UpdateProductInput, session: AsyncSession
    ) -> None:
        from backend.app.core.exceptions import NotFoundError, TenantIsolationError
        repo = ProductRepository(session)
        product = await repo.get_by_id(tool_input.product_id)
        if not product:
            raise NotFoundError(f"Product {tool_input.product_id} not found.")
        if product.shop_id != tool_input.shop_id:
            raise TenantIsolationError(
                f"Product {tool_input.product_id} does not belong to this shop."
            )

    async def _run(
        self, tool_input: UpdateProductInput, session: AsyncSession
    ) -> ToolResult:
        repo = ProductRepository(session)
        product = await repo.get_by_id(tool_input.product_id)

        changed: dict = {}
        if tool_input.price is not None:
            changed["price"] = str(tool_input.price)
            product.price = tool_input.price
        if tool_input.cost_price is not None:
            changed["cost_price"] = str(tool_input.cost_price)
            product.cost_price = tool_input.cost_price
        if tool_input.description is not None:
            changed["description"] = tool_input.description
            product.description = tool_input.description
        if tool_input.low_stock_threshold is not None:
            changed["low_stock_threshold"] = tool_input.low_stock_threshold
            product.low_stock_threshold = tool_input.low_stock_threshold
        if tool_input.is_active is not None:
            changed["is_active"] = tool_input.is_active
            product.is_active = tool_input.is_active

        await session.flush()

        await self._write_audit(
            session,
            tool_input,
            action="PRODUCT_UPDATED",
            entity_type="product",
            entity_id=product.id,
            request_data={"product_id": str(tool_input.product_id), "changes": changed},
            result_data={"name": product.name},
        )

        return ToolResult(
            success=True,
            tool_name=self.name,
            data={"product_id": str(product.id), "name": product.name, "changes": changed},
            message=f"Product '{product.name}' updated: {', '.join(changed.keys())}.",
        )


# ---------------------------------------------------------------------------
# find_product
# ---------------------------------------------------------------------------


class FindProductInput(ToolInput):
    name_query: str


class FindProductTool(BaseTool):
    name = "find_product"
    description = (
        "Search products by name substring. "
        "Returns matching products with ID, name, price, and stock level."
    )
    allowed_roles = ALL_AUTHENTICATED
    input_schema = FindProductInput

    def _check_business_rules(self, tool_input: FindProductInput) -> None:
        if not tool_input.name_query.strip():
            raise ValidationError("name_query cannot be blank.")

    async def _check_tenant_isolation(
        self, tool_input: FindProductInput, session: AsyncSession
    ) -> None:
        pass

    async def _run(
        self, tool_input: FindProductInput, session: AsyncSession
    ) -> ToolResult:
        repo = ProductRepository(session)
        products = await repo.find_by_name(tool_input.name_query, tool_input.shop_id)
        items = [
            {
                "product_id": str(p.id),
                "name": p.name,
                "sku": p.sku,
                "price": str(p.price),
                "current_stock": p.current_stock,
                "is_low": p.is_low_stock,
            }
            for p in products
        ]
        return ToolResult(
            success=True,
            tool_name=self.name,
            data={"products": items, "count": len(items)},
            message=f"{len(items)} product(s) found for '{tool_input.name_query}'.",
        )


tool_registry.register(AddCustomerTool())
tool_registry.register(AddProductTool())
tool_registry.register(UpdateProductTool())
tool_registry.register(FindProductTool())
