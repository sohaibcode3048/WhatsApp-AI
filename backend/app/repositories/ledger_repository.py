"""Repository for managing immutable ledger records and deriving financial balances."""

from decimal import Decimal
import uuid
from typing import List, Optional
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import NotFoundError, TenantIsolationError, ValidationError
from backend.app.models.customer import Customer
from backend.app.models.ledger import LedgerEntry, LedgerType, ReferenceType
from backend.app.models.shop import Shop
from backend.app.repositories.base_repository import TenantRepository
from backend.app.schemas.ledger import CustomerBalanceResponse, LedgerEntryCreate, PaymentRecordCreate


class LedgerRepository(TenantRepository[LedgerEntry]):
    """Data access layer for immutable financial ledger entries."""

    def __init__(self, session: AsyncSession):
        super().__init__(LedgerEntry, session)

    async def add_entry(
        self,
        shop_id: uuid.UUID,
        entry_in: LedgerEntryCreate,
        created_by: Optional[uuid.UUID] = None,
    ) -> LedgerEntry:
        """Append an immutable financial entry to the ledger."""
        if entry_in.amount <= Decimal("0.00"):
            raise ValidationError(
                f"Ledger amount must be greater than zero. Received: {entry_in.amount}",
                details={"amount": str(entry_in.amount)},
            )

        # Verify customer existence and tenant ownership
        cust_result = await self.session.execute(
            select(Customer).where(Customer.id == entry_in.customer_id)
        )
        customer = cust_result.scalar_one_or_none()
        if not customer:
            raise NotFoundError(f"Customer '{entry_in.customer_id}' not found.")
        if customer.shop_id != shop_id:
            raise TenantIsolationError(
                f"Customer '{customer.name}' does not belong to this shop.",
                details={"customer_id": str(customer.id), "shop_id": str(shop_id)},
            )

        entry = LedgerEntry(
            shop_id=shop_id,
            customer_id=customer.id,
            type=entry_in.type,
            amount=entry_in.amount,
            note=entry_in.note,
            reference_type=entry_in.reference_type,
            reference_id=entry_in.reference_id,
            created_by=created_by,
        )

        self.session.add(entry)
        await self.session.flush()
        return entry

    async def record_credit_sale(
        self,
        shop_id: uuid.UUID,
        customer_id: uuid.UUID,
        amount: Decimal,
        note: Optional[str] = "Udhaar / Credit sale",
        reference_type: ReferenceType = ReferenceType.ORDER,
        reference_id: Optional[uuid.UUID] = None,
        created_by: Optional[uuid.UUID] = None,
    ) -> LedgerEntry:
        """Convenience method to record money owed by the customer (DEBIT)."""
        entry_in = LedgerEntryCreate(
            customer_id=customer_id,
            type=LedgerType.DEBIT,
            amount=amount,
            note=note,
            reference_type=reference_type,
            reference_id=reference_id,
        )
        return await self.add_entry(shop_id, entry_in, created_by=created_by)

    async def record_payment(
        self,
        shop_id: uuid.UUID,
        payment_in: PaymentRecordCreate,
        created_by: Optional[uuid.UUID] = None,
    ) -> LedgerEntry:
        """Convenience method to record a payment received from customer (CREDIT)."""
        entry_in = LedgerEntryCreate(
            customer_id=payment_in.customer_id,
            type=LedgerType.CREDIT,
            amount=payment_in.amount,
            note=payment_in.note,
            reference_type=ReferenceType.PAYMENT,
            reference_id=payment_in.reference_id,
        )
        return await self.add_entry(shop_id, entry_in, created_by=created_by)

    async def get_customer_balance(
        self,
        customer_id: uuid.UUID,
        shop_id: uuid.UUID,
    ) -> CustomerBalanceResponse:
        """Derive the real-time financial balance for a customer from immutable entries.

        Current Balance = SUM(DEBIT) - SUM(CREDIT)
        """
        cust_result = await self.session.execute(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.shop_id == shop_id,
            )
        )
        customer = cust_result.scalar_one_or_none()
        if not customer:
            raise NotFoundError(f"Customer '{customer_id}' not found in this shop.")

        # Query shop currency
        shop_result = await self.session.execute(
            select(Shop.currency).where(Shop.id == shop_id)
        )
        currency = shop_result.scalar_one_or_none() or "PKR"

        # Aggregation query deriving total_debit and total_credit
        query = select(
            func.coalesce(
                func.sum(
                    case((LedgerEntry.type == LedgerType.DEBIT, LedgerEntry.amount), else_=0)
                ),
                0,
            ).label("total_debit"),
            func.coalesce(
                func.sum(
                    case((LedgerEntry.type == LedgerType.CREDIT, LedgerEntry.amount), else_=0)
                ),
                0,
            ).label("total_credit"),
        ).where(
            LedgerEntry.shop_id == shop_id,
            LedgerEntry.customer_id == customer_id,
        )

        result = await self.session.execute(query)
        row = result.one()
        total_debit = Decimal(str(row.total_debit))
        total_credit = Decimal(str(row.total_credit))
        current_balance = total_debit - total_credit

        return CustomerBalanceResponse(
            customer_id=customer.id,
            customer_name=customer.name,
            total_debit=total_debit,
            total_credit=total_credit,
            current_balance=current_balance,
            currency=currency,
        )

    async def get_customer_statement(
        self,
        customer_id: uuid.UUID,
        shop_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> List[LedgerEntry]:
        """Fetch chronologically ordered ledger entries for a customer."""
        query = (
            select(LedgerEntry)
            .where(
                LedgerEntry.shop_id == shop_id,
                LedgerEntry.customer_id == customer_id,
            )
            .order_by(LedgerEntry.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())
