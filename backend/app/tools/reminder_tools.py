"""Reminder Tools — payment reminders for customers.

Tools:
  send_reminder — send an automated or custom payment reminder to a customer via WhatsApp
"""

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import NotFoundError, TenantIsolationError, ValidationError
from backend.app.repositories.customer_repository import CustomerRepository
from backend.app.repositories.ledger_repository import LedgerRepository
from backend.app.services.whatsapp_service import WhatsAppService
from backend.app.tools.base import (
    OWNER_AND_STAFF,
    BaseTool,
    ToolInput,
    ToolResult,
    tool_registry,
)


class SendReminderInput(ToolInput):
    customer_id: uuid.UUID
    amount: Optional[Decimal] = None
    custom_message: Optional[str] = None


class SendReminderTool(BaseTool):
    name = "send_reminder"
    description = (
        "Send a payment reminder message to a customer via WhatsApp. "
        "Calculates the customer's current balance if amount is not specified, "
        "and sends a polite reminder."
    )
    allowed_roles = OWNER_AND_STAFF
    input_schema = SendReminderInput

    def _check_business_rules(self, tool_input: SendReminderInput) -> None:
        if tool_input.amount is not None and tool_input.amount <= Decimal("0"):
            raise ValidationError("Reminder amount must be greater than zero.")

    async def _check_tenant_isolation(
        self, tool_input: SendReminderInput, session: AsyncSession
    ) -> None:
        cust_repo = CustomerRepository(session)
        customer = await cust_repo.get_by_id(tool_input.customer_id)
        if not customer:
            raise NotFoundError(f"Customer {tool_input.customer_id} not found.")
        if customer.shop_id != tool_input.shop_id:
            raise TenantIsolationError(
                f"Customer {tool_input.customer_id} does not belong to shop {tool_input.shop_id}"
            )

    async def _run(
        self, tool_input: SendReminderInput, session: AsyncSession
    ) -> ToolResult:
        cust_repo = CustomerRepository(session)
        customer = await cust_repo.get_by_id(tool_input.customer_id)
        if not customer:
            raise NotFoundError(f"Customer {tool_input.customer_id} not found.")

        ledger_repo = LedgerRepository(session)
        balance_resp = await ledger_repo.get_customer_balance(customer.id, tool_input.shop_id)
        current_balance = balance_resp.current_balance

        reminder_amount = tool_input.amount or current_balance
        if reminder_amount <= Decimal("0"):
            return ToolResult(
                tool_name=self.name,
                success=True,
                data={
                    "customer_id": str(customer.id),
                    "customer_name": customer.name,
                    "phone_number": customer.phone_number,
                    "balance": str(current_balance),
                    "message": "Customer balance is zero or in credit. No reminder sent.",
                    "sent": False,
                },
            )

        if tool_input.custom_message:
            body = tool_input.custom_message
        else:
            body = (
                f"Assalam o alaikum {customer.name} sahib, aap ki taraf dukaan ka baqi "
                f"balance Rs. {reminder_amount:,.2f} hai. Baraye meherbani jald ada farmayein. Shukriya!"
            )

        whatsapp_service = WhatsAppService(session)
        send_result = await whatsapp_service.send_text_message(
            to_phone=customer.phone_number,
            text_body=body,
        )

        await self._write_audit(
            session=session,
            tool_input=tool_input,
            action="SEND_REMINDER",
            entity_type="customer",
            entity_id=customer.id,
            request_data={
                "customer_id": str(customer.id),
                "amount": str(reminder_amount),
            },
            result_data={"sent": True, "provider_status": send_result.get("status")},
        )

        return ToolResult(
            tool_name=self.name,
            success=True,
            data={
                "customer_id": str(customer.id),
                "customer_name": customer.name,
                "phone_number": customer.phone_number,
                "amount": str(reminder_amount),
                "body": body,
                "sent": True,
                "status": send_result.get("status"),
            },
        )


# Auto-register tool
tool_registry.register(SendReminderTool())
