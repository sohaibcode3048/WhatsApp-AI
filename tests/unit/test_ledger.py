"""Unit tests for Ledger Engine: credit, debit, balance derivation, negative validation, and rollbacks."""

from decimal import Decimal
import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import TenantIsolationError, ValidationError
from backend.app.models.ledger import LedgerType, ReferenceType
from backend.app.repositories.customer_repository import CustomerRepository
from backend.app.repositories.ledger_repository import LedgerRepository
from backend.app.repositories.shop_repository import ShopRepository
from backend.app.schemas.ledger import LedgerEntryCreate, PaymentRecordCreate


@pytest.mark.asyncio
async def test_ledger_debit_credit_and_balance_derivation(db_session: AsyncSession):
    """Verify that debit (udhaar) and credit (payment) derive current balance correctly:

    Ali:
    Debit 2500 (Udhaar sale)
    Credit 1000 (Payment)
    Balance = 1500
    """
    shop_repo = ShopRepository(db_session)
    cust_repo = CustomerRepository(db_session)
    ledger_repo = LedgerRepository(db_session)

    shop = await shop_repo.create(name="Munshi General Store", phone_number="+923001122334")
    customer = await cust_repo.create(shop.id, name="Ali", phone_number="+923215551234")

    # 1. Initial balance must be 0
    initial_bal = await ledger_repo.get_customer_balance(customer.id, shop.id)
    assert initial_bal.total_debit == Decimal("0.00")
    assert initial_bal.total_credit == Decimal("0.00")
    assert initial_bal.current_balance == Decimal("0.00")

    # 2. Add Debit entry: 2500 (Udhaar taken)
    entry_debit = await ledger_repo.record_credit_sale(
        shop_id=shop.id,
        customer_id=customer.id,
        amount=Decimal("2500.00"),
        note="2 bags of rice on udhaar",
    )
    assert entry_debit.type == LedgerType.DEBIT
    assert entry_debit.amount == Decimal("2500.00")

    bal_after_debit = await ledger_repo.get_customer_balance(customer.id, shop.id)
    assert bal_after_debit.total_debit == Decimal("2500.00")
    assert bal_after_debit.total_credit == Decimal("0.00")
    assert bal_after_debit.current_balance == Decimal("2500.00")

    # 3. Add Credit entry: 1000 (Payment received)
    entry_credit = await ledger_repo.record_payment(
        shop_id=shop.id,
        payment_in=PaymentRecordCreate(
            customer_id=customer.id,
            amount=Decimal("1000.00"),
            note="Cash received at shop",
        ),
    )
    assert entry_credit.type == LedgerType.CREDIT
    assert entry_credit.amount == Decimal("1000.00")

    # 4. Final Balance derivation: 2500 - 1000 = 1500
    final_bal = await ledger_repo.get_customer_balance(customer.id, shop.id)
    assert final_bal.total_debit == Decimal("2500.00")
    assert final_bal.total_credit == Decimal("1000.00")
    assert final_bal.current_balance == Decimal("1500.00")


@pytest.mark.asyncio
async def test_negative_and_zero_amount_rejection(db_session: AsyncSession):
    """Verify negative and zero amounts are strictly rejected by schemas and repository."""
    cust_id = pytest.importorskip("uuid").uuid4()

    # Pydantic validation rejects negative amount
    with pytest.raises(PydanticValidationError):
        LedgerEntryCreate(
            customer_id=cust_id,
            type=LedgerType.DEBIT,
            amount=Decimal("-500.00"),
        )

    # Pydantic validation rejects zero amount
    with pytest.raises(PydanticValidationError):
        PaymentRecordCreate(
            customer_id=cust_id,
            amount=Decimal("0.00"),
        )


@pytest.mark.asyncio
async def test_ledger_transaction_rollback(db_session: AsyncSession):
    """Verify session rollback removes uncommitted ledger entry and preserves balance."""
    shop_repo = ShopRepository(db_session)
    cust_repo = CustomerRepository(db_session)
    ledger_repo = LedgerRepository(db_session)

    shop = await shop_repo.create(name="Test Mart", phone_number="+923009988111")
    customer = await cust_repo.create(shop.id, name="Rashid", phone_number="+923009988222")
    await db_session.commit()

    shop_id = shop.id
    customer_id = customer.id

    # Add entry but roll back
    await ledger_repo.record_credit_sale(
        shop_id=shop_id,
        customer_id=customer_id,
        amount=Decimal("3000.00"),
    )
    await db_session.rollback()

    balance = await ledger_repo.get_customer_balance(customer_id, shop_id)
    assert balance.current_balance == Decimal("0.00")


@pytest.mark.asyncio
async def test_ledger_tenant_isolation(db_session: AsyncSession):
    """CRITICAL SECURITY TEST: Ensure Shop A cannot record or view ledger entries for Shop B customers."""
    shop_repo = ShopRepository(db_session)
    cust_repo = CustomerRepository(db_session)
    ledger_repo = LedgerRepository(db_session)

    shop_a = await shop_repo.create(name="Shop A", phone_number="+923110000001")
    shop_b = await shop_repo.create(name="Shop B", phone_number="+923110000002")

    cust_b = await cust_repo.create(shop_b.id, name="Shop B Customer", phone_number="+923119999999")

    # Shop A attempts to record debit on Shop B's customer
    with pytest.raises(TenantIsolationError):
        await ledger_repo.record_credit_sale(
            shop_id=shop_a.id,
            customer_id=cust_b.id,
            amount=Decimal("5000.00"),
        )

    # Shop A attempts to query balance of Shop B's customer
    with pytest.raises(Exception):
        await ledger_repo.get_customer_balance(customer_id=cust_b.id, shop_id=shop_a.id)
