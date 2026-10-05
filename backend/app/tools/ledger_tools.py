"""Ledger Tools — MODULE 13.

Tools:
  add_ledger_entry  — record udhaar (DEBIT) or payment (CREDIT)
  get_balance       — derive customer balance from immutable ledger
  record_payment    — convenience wrapper for CREDIT entry
"""

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import NotFoundError, TenantIsolationError
from backend.app.models.ledger import LedgerType, ReferenceType
from backend.app.repositories.customer_repository import CustomerRepository
from backend.app.repositories.ledger_repository import LedgerRepository
from backend.app.schemas.ledger import LedgerEntryCreate, PaymentRecordCreate
from backend.app.tools.base import (
    ALL_AUTHENTICATED,
    OWNER_AND_STAFF,
    BaseTool,
    ToolInput,
    ToolResult,
    tool_registry,
)


class AddLedgerEntryInput(ToolInput):
    customer_id: uuid.UUID
    amount: Decimal
    ledger_type: LedgerType          # DEBIT | CREDIT
    note: Optional[str] = None
    reference_type: ReferenceType = ReferenceType.MANUAL_ADJUSTMENT
    reference_id: Optional[uuid.UUID] = None

    model_config = {"arbitrary_types_allowed": True}


class GetBalanceInput(ToolInput):
    customer_id: uuid.UUID


class RecordPaymentInput(ToolInput):
    customer_id: uuid.UUID
    amount: Decimal
    note: Optional[str] = "Payment received"
    reference_id: Optional[uuid.UUID] = None

    model_config = {"arbitrary_types_allowed": True}


class AddLedgerEntryTool(BaseTool):
    name = "add_ledger_entry"
    description = (
        "Record a ledger entry for a customer. "
        "DEBIT = customer owes shop (udhaar). CREDIT = customer paid shop. "
        "Amount must be positive. Requires Haan/Nahi confirmation."
    )
    allowed_roles = OWNER_AND_STAFF
    input_schema = AddLedgerEntryInput
    _REQUIRES_CONFIRMATION = True

    def _build_confirmation_prompt(self, tool_input) -> str:
        return (
            f"Rs. {tool_input.amount} {tool_input.ledger_type.value} "
            f"customer ke liye ledger mein add karna hai. "
            f"Haan / Nahi?"
        )

    def _check_business_rules(self, tool_input: AddLedgerEntryInput) -> None:
        self._require_positive_amount(tool_input.amount)

    async def _check_tenant_isolation(
        self, tool_input: AddLedgerEntryInput, session: AsyncSession
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
        self, tool_input: AddLedgerEntryInput, session: AsyncSession
    ) -> ToolResult:
        repo = LedgerRepository(session)
        entry = await repo.add_entry(
            shop_id=tool_input.shop_id,
            entry_in=LedgerEntryCreate(
                customer_id=tool_input.customer_id,
                type=tool_input.ledger_type,
                amount=tool_input.amount,
                note=tool_input.note,
                reference_type=tool_input.reference_type,
                reference_id=tool_input.reference_id,
            ),
        )

        # Fetch fresh balance for response
        balance_resp = await repo.get_customer_balance(
            tool_input.customer_id, tool_input.shop_id
        )

        await self._write_audit(
            session,
            tool_input,
            action=f"LEDGER_{tool_input.ledger_type.value}",
            entity_type="ledger",
            entity_id=entry.id,
            request_data={
                "customer_id": str(tool_input.customer_id),
                "amount": str(tool_input.amount),
                "type": tool_input.ledger_type.value,
                "note": tool_input.note,
            },
            result_data={
                "entry_id": str(entry.id),
                "new_balance": str(balance_resp.current_balance),
            },
        )

        direction = "owed to shop" if tool_input.ledger_type == LedgerType.DEBIT else "received from customer"
        return ToolResult(
            success=True,
            tool_name=self.name,
            data={
                "entry_id": str(entry.id),
                "customer_id": str(tool_input.customer_id),
                "customer_name": balance_resp.customer_name,
                "amount": str(tool_input.amount),
                "type": tool_input.ledger_type.value,
                "new_balance": str(balance_resp.current_balance),
                "currency": balance_resp.currency,
            },
            message=(
                f"Rs. {tool_input.amount} {direction} for {balance_resp.customer_name}. "
                f"Current balance: Rs. {balance_resp.current_balance}."
            ),
        )


class GetBalanceTool(BaseTool):
    name = "get_balance"
    description = (
        "Get the current outstanding balance for a customer. "
        "Balance = SUM(DEBIT) - SUM(CREDIT). Positive = customer owes."
    )
    allowed_roles = ALL_AUTHENTICATED
    input_schema = GetBalanceInput

    def _check_business_rules(self, tool_input: GetBalanceInput) -> None:
        pass

    async def _check_tenant_isolation(
        self, tool_input: GetBalanceInput, session: AsyncSession
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
        self, tool_input: GetBalanceInput, session: AsyncSession
    ) -> ToolResult:
        repo = LedgerRepository(session)
        balance = await repo.get_customer_balance(
            tool_input.customer_id, tool_input.shop_id
        )
        return ToolResult(
            success=True,
            tool_name=self.name,
            data={
                "customer_id": str(balance.customer_id),
                "customer_name": balance.customer_name,
                "total_debit": str(balance.total_debit),
                "total_credit": str(balance.total_credit),
                "current_balance": str(balance.current_balance),
                "currency": balance.currency,
            },
            message=(
                f"{balance.customer_name} ka balance: "
                f"Rs. {balance.current_balance} {balance.currency}."
            ),
        )


class RecordPaymentTool(BaseTool):
    name = "record_payment"
    description = (
        "Record a cash or transfer payment received from a customer. "
        "Creates a CREDIT ledger entry and reduces the outstanding balance."
    )
    allowed_roles = OWNER_AND_STAFF
    input_schema = RecordPaymentInput
    _REQUIRES_CONFIRMATION = True

    def _build_confirmation_prompt(self, tool_input) -> str:
        return (
            f"Rs. {tool_input.amount} payment customer se receive hua hai. "
            f"Ledger mein add karna hai? Haan / Nahi?"
        )

    def _check_business_rules(self, tool_input: RecordPaymentInput) -> None:
        self._require_positive_amount(tool_input.amount)

    async def _check_tenant_isolation(
        self, tool_input: RecordPaymentInput, session: AsyncSession
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
        self, tool_input: RecordPaymentInput, session: AsyncSession
    ) -> ToolResult:
        repo = LedgerRepository(session)
        entry = await repo.record_payment(
            shop_id=tool_input.shop_id,
            payment_in=PaymentRecordCreate(
                customer_id=tool_input.customer_id,
                amount=tool_input.amount,
                note=tool_input.note,
                reference_id=tool_input.reference_id,
            ),
        )
        balance = await repo.get_customer_balance(
            tool_input.customer_id, tool_input.shop_id
        )

        await self._write_audit(
            session,
            tool_input,
            action="PAYMENT_RECEIVED",
            entity_type="ledger",
            entity_id=entry.id,
            request_data={
                "customer_id": str(tool_input.customer_id),
                "amount": str(tool_input.amount),
                "note": tool_input.note,
            },
            result_data={
                "entry_id": str(entry.id),
                "new_balance": str(balance.current_balance),
            },
        )

        return ToolResult(
            success=True,
            tool_name=self.name,
            data={
                "entry_id": str(entry.id),
                "customer_name": balance.customer_name,
                "amount": str(tool_input.amount),
                "new_balance": str(balance.current_balance),
                "currency": balance.currency,
            },
            message=(
                f"Rs. {tool_input.amount} payment received from {balance.customer_name}. "
                f"Remaining balance: Rs. {balance.current_balance}."
            ),
        )


tool_registry.register(AddLedgerEntryTool())
tool_registry.register(GetBalanceTool())
tool_registry.register(RecordPaymentTool())
