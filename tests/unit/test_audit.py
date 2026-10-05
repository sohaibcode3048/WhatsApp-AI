"""Unit tests for Audit System: action tracking, secret redaction, and rollback atomicity."""

from decimal import Decimal
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.user import UserRole
from backend.app.repositories.customer_repository import CustomerRepository
from backend.app.repositories.ledger_repository import LedgerRepository
from backend.app.repositories.shop_repository import ShopRepository
from backend.app.repositories.user_repository import UserRepository
from backend.app.services.audit_service import AuditService


@pytest.mark.asyncio
async def test_audit_log_creation_and_secret_redaction(db_session: AsyncSession):
    """Verify audit log creation with automatic sensitive token redaction."""
    shop_repo = ShopRepository(db_session)
    user_repo = UserRepository(db_session)
    audit_service = AuditService(db_session)

    shop = await shop_repo.create(name="Audit Mart", phone_number="+923001230001")
    user = await user_repo.create(shop.id, name="Admin", phone_number="+923001230001", role=UserRole.OWNER)

    # Log action containing a sensitive API key in the request
    audit_entry = await audit_service.record_event(
        shop_id=shop.id,
        user_id=user.id,
        action="UPDATE_PRODUCT_PRICE",
        entity_type="PRODUCT",
        entity_id="prod-123",
        tool_name="update_product_tool",
        request_data={
            "old_price": "200.00",
            "new_price": "250.00",
            "api_key": "secret-super-key-999",
        },
        result_data={"status": "SUCCESS"},
    )

    assert audit_entry.id is not None
    assert audit_entry.action == "UPDATE_PRODUCT_PRICE"
    assert audit_entry.request_data["new_price"] == "250.00"
    # Verify sensitive data redaction
    assert audit_entry.request_data["api_key"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_atomic_transaction_rollback_with_audit(db_session: AsyncSession):
    """Verify that if an atomic transaction rolls back, both the mutation and the audit log roll back."""
    shop_repo = ShopRepository(db_session)
    cust_repo = CustomerRepository(db_session)
    ledger_repo = LedgerRepository(db_session)
    audit_service = AuditService(db_session)

    shop = await shop_repo.create(name="Transaction Shop", phone_number="+923007770001")
    customer = await cust_repo.create(shop.id, name="Zahid", phone_number="+923007770002")
    await db_session.commit()

    shop_id = shop.id
    customer_id = customer.id

    # Simulate a transaction that writes to ledger and audit, but rolls back
    await ledger_repo.record_credit_sale(shop_id, customer_id, Decimal("5000.00"), "Failed purchase")
    await audit_service.record_event(
        shop_id=shop_id,
        action="CREATE_LEDGER_ENTRY",
        entity_type="LEDGER",
        entity_id=str(customer_id),
    )

    # Roll back transaction
    await db_session.rollback()

    # Balance must be 0 and audit trail for this action must be empty
    bal = await ledger_repo.get_customer_balance(customer_id, shop_id)
    assert bal.current_balance == Decimal("0.00")

    audit_trail = await audit_service.get_shop_audit_trail(shop_id)
    assert len(audit_trail) == 0


@pytest.mark.asyncio
async def test_audit_tenant_isolation(db_session: AsyncSession):
    """Ensure Shop A cannot view Shop B audit logs."""
    shop_repo = ShopRepository(db_session)
    audit_service = AuditService(db_session)

    shop_a = await shop_repo.create(name="Alpha Store", phone_number="+923005550001")
    shop_b = await shop_repo.create(name="Beta Store", phone_number="+923005550002")

    await audit_service.record_event(
        shop_id=shop_b.id,
        action="SENSITIVE_OWNER_CONFIG",
        entity_type="SETTINGS",
    )
    await db_session.commit()

    # Shop A audit trail must have 0 entries
    trail_a = await audit_service.get_shop_audit_trail(shop_a.id)
    assert len(trail_a) == 0

    # Shop B audit trail must have 1 entry
    trail_b = await audit_service.get_shop_audit_trail(shop_b.id)
    assert len(trail_b) == 1
