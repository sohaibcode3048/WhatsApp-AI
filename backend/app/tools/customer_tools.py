"""Customer Tools — MODULE 12.

Tools:
  find_customer  — search by name substring within the shop
  get_customer   — fetch one customer by ID with basic stats
"""

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import NotFoundError, TenantIsolationError
from backend.app.repositories.customer_repository import CustomerRepository
from backend.app.tools.base import (
    ALL_AUTHENTICATED,
    OWNER_AND_STAFF,
    BaseTool,
    ToolInput,
    ToolResult,
    tool_registry,
)


class FindCustomerInput(ToolInput):
    name_query: str


class GetCustomerInput(ToolInput):
    customer_id: uuid.UUID


class FindCustomerTool(BaseTool):
    name = "find_customer"
    description = (
        "Search customers by name within the shop. "
        "Returns a list of matching customers with ID, name, and phone."
    )
    allowed_roles = OWNER_AND_STAFF
    input_schema = FindCustomerInput

    def _check_business_rules(self, tool_input: FindCustomerInput) -> None:
        if not tool_input.name_query.strip():
            from backend.app.core.exceptions import ValidationError
            raise ValidationError("name_query cannot be blank.")

    async def _check_tenant_isolation(
        self, tool_input: FindCustomerInput, session: AsyncSession
    ) -> None:
        pass  # query is already tenant-scoped

    async def _run(
        self, tool_input: FindCustomerInput, session: AsyncSession
    ) -> ToolResult:
        repo = CustomerRepository(session)
        customers = await repo.find_by_name(
            tool_input.name_query, tool_input.shop_id
        )
        items = [
            {
                "customer_id": str(c.id),
                "name": c.name,
                "phone_number": c.phone_number,
            }
            for c in customers
        ]
        return ToolResult(
            success=True,
            tool_name=self.name,
            data={"customers": items, "count": len(items)},
            message=f"{len(items)} customer(s) found for '{tool_input.name_query}'.",
        )


class GetCustomerTool(BaseTool):
    name = "get_customer"
    description = (
        "Fetch detailed info for one customer by their UUID. "
        "Returns name, phone, and active status."
    )
    allowed_roles = OWNER_AND_STAFF
    input_schema = GetCustomerInput

    def _check_business_rules(self, tool_input: GetCustomerInput) -> None:
        pass

    async def _check_tenant_isolation(
        self, tool_input: GetCustomerInput, session: AsyncSession
    ) -> None:
        repo = CustomerRepository(session)
        customer = await repo.get_by_id(tool_input.customer_id)
        if not customer:
            raise NotFoundError(
                f"Customer {tool_input.customer_id} not found.",
                details={"customer_id": str(tool_input.customer_id)},
            )
        if customer.shop_id != tool_input.shop_id:
            raise TenantIsolationError(
                f"Customer {tool_input.customer_id} does not belong to this shop."
            )

    async def _run(
        self, tool_input: GetCustomerInput, session: AsyncSession
    ) -> ToolResult:
        repo = CustomerRepository(session)
        customer = await repo.get_by_id(tool_input.customer_id)
        return ToolResult(
            success=True,
            tool_name=self.name,
            data={
                "customer_id": str(customer.id),
                "name": customer.name,
                "phone_number": customer.phone_number,
                "notes": customer.notes,
                "is_active": customer.is_active,
            },
            message=f"Customer: {customer.name} ({customer.phone_number})",
        )


tool_registry.register(FindCustomerTool())
tool_registry.register(GetCustomerTool())
