"""MODULEs 10–14 — Tool Framework, Stock, Customer, Ledger, Order Tools.

Coverage:
  BaseTool / ToolRegistry (MODULE 10)
    - ToolInput validates required fields
    - ToolResult schema
    - RBAC: unauthorized role raises AuthorizationError
    - RBAC: authorized role passes through
    - Schema validation failure raises ValidationError
    - tool_registry.get unknown name raises ToolExecutionError
    - tool_registry.list_tools returns metadata
    - tool_registry contains check

  Stock Tools (MODULE 11)
    - check_stock: returns product name + stock level
    - check_stock: NotFoundError for unknown product
    - check_stock: TenantIsolationError for wrong shop
    - check_stock: is_low flag correct
    - update_stock: add units increases stock
    - update_stock: remove units decreases stock
    - update_stock: cannot go below 0 → ValidationError
    - update_stock: zero change → ValidationError
    - update_stock: writes audit log
    - get_low_stock: returns only low-stock items
    - get_low_stock: empty list when all stock sufficient

  Customer Tools (MODULE 12)
    - find_customer: returns matches
    - find_customer: empty query → ValidationError
    - find_customer: empty result OK
    - get_customer: returns correct data
    - get_customer: NotFoundError for unknown
    - get_customer: TenantIsolationError for wrong shop

  Ledger Tools (MODULE 13)
    - add_ledger_entry DEBIT: entry created, balance updated
    - add_ledger_entry CREDIT: entry created, balance updated
    - add_ledger_entry: negative amount → ValidationError
    - add_ledger_entry: unknown customer → NotFoundError
    - add_ledger_entry: wrong shop customer → TenantIsolationError
    - get_balance: returns correct balance
    - record_payment: creates CREDIT entry, updates balance

  Order Tools (MODULE 14)
    - create_order: creates order, decrements stock, returns total
    - create_order: insufficient stock → ValidationError
    - create_order: writes audit log
    - get_order: returns order with items
    - get_order: NotFoundError for wrong shop
    - cancel_order: cancels and restores stock
    - cancel_order: already cancelled is idempotent
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import (
    AuthorizationError,
    NotFoundError,
    TenantIsolationError,
    ToolExecutionError,
    ValidationError,
)
from backend.app.models.customer import Customer
from backend.app.models.ledger import LedgerType
from backend.app.models.order import OrderStatus
from backend.app.models.product import Product
from backend.app.models.shop import Shop
from backend.app.schemas.router import SenderRole
from backend.app.tools import tool_registry  # importing package triggers all registrations
from backend.app.tools.base import (
    OWNER_AND_STAFF,
    BaseTool,
    ToolInput,
    ToolResult,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_shop(db: AsyncSession, phone: str = "shop_ph_001") -> Shop:
    shop = Shop(
        name="Tool Test Shop",
        phone_number=phone,
        default_language="roman_urdu",
        currency="PKR",
        settings={},
    )
    db.add(shop)
    await db.flush()
    return shop


async def _make_product(
    db: AsyncSession,
    shop_id: uuid.UUID,
    name: str = "Coke 1.5L",
    stock: int = 20,
    threshold: int = 5,
    price: Decimal = Decimal("250.00"),
) -> Product:
    product = Product(
        shop_id=shop_id,
        name=name,
        price=price,
        current_stock=stock,
        low_stock_threshold=threshold,
        is_active=True,
    )
    db.add(product)
    await db.flush()
    return product


async def _make_customer(
    db: AsyncSession,
    shop_id: uuid.UUID,
    name: str = "Ali",
    phone: str = "923001000001",
) -> Customer:
    customer = Customer(
        shop_id=shop_id,
        name=name,
        phone_number=phone,
        is_active=True,
    )
    db.add(customer)
    await db.flush()
    return customer


def _base_input(shop_id: uuid.UUID, role: SenderRole = SenderRole.OWNER) -> dict:
    return {
        "shop_id": str(shop_id),
        "sender_phone": "923001234567",
        "sender_role": role,
    }


# ===========================================================================
# MODULE 10 — Tool Framework
# ===========================================================================


class TestToolFramework:
    def test_tool_registry_get_unknown_raises(self):
        with pytest.raises(ToolExecutionError, match="Unknown tool"):
            tool_registry.get("nonexistent_tool_xyz")

    def test_tool_registry_contains(self):
        assert "check_stock" in tool_registry
        assert "nonexistent_xyz" not in tool_registry

    def test_tool_registry_list_tools_returns_metadata(self):
        tools = tool_registry.list_tools()
        names = [t["name"] for t in tools]
        assert "check_stock" in names
        assert "add_ledger_entry" in names
        assert "create_order" in names
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "allowed_roles" in t

    async def test_unauthorized_role_raises(self, db_session: AsyncSession):
        """CUSTOMER trying to call update_stock → AuthorizationError."""
        shop = await _make_shop(db_session, "auth_test_shop")
        product = await _make_product(db_session, shop.id)
        await db_session.commit()

        tool = tool_registry.get("update_stock")
        raw = {
            **_base_input(shop.id, SenderRole.CUSTOMER),
            "product_id": str(product.id),
            "quantity_change": 5,
        }
        with pytest.raises(AuthorizationError):
            await tool.execute(raw, db_session)

    async def test_authorized_role_passes(self, db_session: AsyncSession):
        """OWNER calling check_stock → succeeds."""
        shop = await _make_shop(db_session, "auth_ok_shop")
        product = await _make_product(db_session, shop.id)
        await db_session.commit()

        tool = tool_registry.get("check_stock")
        raw = {
            **_base_input(shop.id, SenderRole.OWNER),
            "product_id": str(product.id),
        }
        result = await tool.execute(raw, db_session)
        assert result.success is True

    async def test_schema_validation_failure_raises(self, db_session: AsyncSession):
        """Passing wrong type for product_id → ValidationError."""
        shop = await _make_shop(db_session, "schema_test_shop")
        await db_session.commit()

        tool = tool_registry.get("check_stock")
        raw = {
            **_base_input(shop.id, SenderRole.OWNER),
            "product_id": "not-a-uuid",
        }
        with pytest.raises(ValidationError):
            await tool.execute(raw, db_session)

    def test_tool_result_schema(self):
        r = ToolResult(success=True, tool_name="test_tool", data={"key": "value"}, message="ok")
        assert r.success is True
        assert r.data["key"] == "value"
        assert r.tool_name == "test_tool"


# ===========================================================================
# MODULE 11 — Stock Tools
# ===========================================================================


class TestStockTools:
    async def test_check_stock_returns_data(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "stock_chk_1")
        product = await _make_product(db_session, shop.id, stock=30)
        await db_session.commit()

        tool = tool_registry.get("check_stock")
        result = await tool.execute(
            {**_base_input(shop.id), "product_id": str(product.id)},
            db_session,
        )
        assert result.success is True
        assert result.data["current_stock"] == 30
        assert result.data["name"] == "Coke 1.5L"
        assert result.data["is_low"] is False

    async def test_check_stock_not_found(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "stock_404_2")
        await db_session.commit()

        tool = tool_registry.get("check_stock")
        with pytest.raises(NotFoundError):
            await tool.execute(
                {**_base_input(shop.id), "product_id": str(uuid.uuid4())},
                db_session,
            )

    async def test_check_stock_tenant_isolation(self, db_session: AsyncSession):
        shop_a = await _make_shop(db_session, "stock_iso_a")
        shop_b = await _make_shop(db_session, "stock_iso_b")
        product = await _make_product(db_session, shop_a.id)
        await db_session.commit()

        tool = tool_registry.get("check_stock")
        with pytest.raises(TenantIsolationError):
            await tool.execute(
                {**_base_input(shop_b.id), "product_id": str(product.id)},
                db_session,
            )

    async def test_check_stock_low_flag(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "stock_low_1")
        product = await _make_product(db_session, shop.id, stock=3, threshold=5)
        await db_session.commit()

        tool = tool_registry.get("check_stock")
        result = await tool.execute(
            {**_base_input(shop.id), "product_id": str(product.id)},
            db_session,
        )
        assert result.data["is_low"] is True

    async def test_update_stock_add_units(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "upd_stock_1")
        product = await _make_product(db_session, shop.id, stock=10)
        await db_session.commit()

        tool = tool_registry.get("update_stock")
        result = await tool.execute(
            {
                **_base_input(shop.id),
                "product_id": str(product.id),
                "quantity_change": 5,
            },
            db_session,
        )
        assert result.success is True
        assert result.data["new_stock"] == 15
        assert result.data["old_stock"] == 10

    async def test_update_stock_remove_units(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "upd_stock_2")
        product = await _make_product(db_session, shop.id, stock=10)
        await db_session.commit()

        tool = tool_registry.get("update_stock")
        result = await tool.execute(
            {
                **_base_input(shop.id),
                "product_id": str(product.id),
                "quantity_change": -3,
            },
            db_session,
        )
        assert result.data["new_stock"] == 7

    async def test_update_stock_below_zero_raises(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "upd_stock_3")
        product = await _make_product(db_session, shop.id, stock=5)
        await db_session.commit()

        tool = tool_registry.get("update_stock")
        with pytest.raises(ValidationError, match="below 0"):
            await tool.execute(
                {
                    **_base_input(shop.id),
                    "product_id": str(product.id),
                    "quantity_change": -10,
                },
                db_session,
            )

    async def test_update_stock_zero_change_raises(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "upd_stock_4")
        product = await _make_product(db_session, shop.id)
        await db_session.commit()

        tool = tool_registry.get("update_stock")
        with pytest.raises(ValidationError, match="zero"):
            await tool.execute(
                {
                    **_base_input(shop.id),
                    "product_id": str(product.id),
                    "quantity_change": 0,
                },
                db_session,
            )

    async def test_update_stock_writes_audit(self, db_session: AsyncSession):
        from sqlalchemy import select
        from backend.app.models.audit_log import AuditLog

        shop = await _make_shop(db_session, "upd_audit_1")
        product = await _make_product(db_session, shop.id, stock=10)
        await db_session.commit()

        tool = tool_registry.get("update_stock")
        await tool.execute(
            {
                **_base_input(shop.id),
                "product_id": str(product.id),
                "quantity_change": 5,
            },
            db_session,
        )
        await db_session.commit()

        result = await db_session.execute(
            select(AuditLog).where(AuditLog.shop_id == shop.id)
        )
        logs = result.scalars().all()
        assert any(log.action == "STOCK_UPDATE" for log in logs)

    async def test_get_low_stock_returns_low_products(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "low_stk_1")
        await _make_product(db_session, shop.id, name="Low Item", stock=2, threshold=5)
        await _make_product(db_session, shop.id, name="OK Item", stock=20, threshold=5)
        await db_session.commit()

        tool = tool_registry.get("get_low_stock")
        result = await tool.execute(_base_input(shop.id), db_session)
        names = [p["name"] for p in result.data["low_stock_products"]]
        assert "Low Item" in names
        assert "OK Item" not in names

    async def test_get_low_stock_empty_when_sufficient(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "low_stk_2")
        await _make_product(db_session, shop.id, stock=100, threshold=5)
        await db_session.commit()

        tool = tool_registry.get("get_low_stock")
        result = await tool.execute(_base_input(shop.id), db_session)
        assert result.data["count"] == 0


# ===========================================================================
# MODULE 12 — Customer Tools
# ===========================================================================


class TestCustomerTools:
    async def test_find_customer_returns_matches(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "cust_find_1")
        await _make_customer(db_session, shop.id, name="Ali Khan", phone="923001000010")
        await _make_customer(db_session, shop.id, name="Aliya Bibi", phone="923001000011")
        await db_session.commit()

        tool = tool_registry.get("find_customer")
        result = await tool.execute(
            {**_base_input(shop.id), "name_query": "ali"},
            db_session,
        )
        assert result.success is True
        assert result.data["count"] == 2

    async def test_find_customer_blank_query_raises(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "cust_blank_1")
        await db_session.commit()

        tool = tool_registry.get("find_customer")
        with pytest.raises(ValidationError):
            await tool.execute(
                {**_base_input(shop.id), "name_query": "   "},
                db_session,
            )

    async def test_find_customer_empty_result_ok(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "cust_empty_1")
        await db_session.commit()

        tool = tool_registry.get("find_customer")
        result = await tool.execute(
            {**_base_input(shop.id), "name_query": "ZZZnonexistent"},
            db_session,
        )
        assert result.data["count"] == 0

    async def test_get_customer_returns_data(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "cust_get_1")
        customer = await _make_customer(db_session, shop.id, name="Fatima")
        await db_session.commit()

        tool = tool_registry.get("get_customer")
        result = await tool.execute(
            {**_base_input(shop.id), "customer_id": str(customer.id)},
            db_session,
        )
        assert result.data["name"] == "Fatima"
        assert result.data["is_active"] is True

    async def test_get_customer_not_found(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "cust_404_1")
        await db_session.commit()

        tool = tool_registry.get("get_customer")
        with pytest.raises(NotFoundError):
            await tool.execute(
                {**_base_input(shop.id), "customer_id": str(uuid.uuid4())},
                db_session,
            )

    async def test_get_customer_tenant_isolation(self, db_session: AsyncSession):
        shop_a = await _make_shop(db_session, "cust_iso_a")
        shop_b = await _make_shop(db_session, "cust_iso_b")
        customer = await _make_customer(db_session, shop_a.id, phone="923001000099")
        await db_session.commit()

        tool = tool_registry.get("get_customer")
        with pytest.raises(TenantIsolationError):
            await tool.execute(
                {**_base_input(shop_b.id), "customer_id": str(customer.id)},
                db_session,
            )


# ===========================================================================
# MODULE 13 — Ledger Tools
# ===========================================================================


@pytest.fixture(autouse=False)
def disable_confirmation(monkeypatch):
    """Bypass confirmation for ledger tool unit tests.
    
    The confirmation flow is tested separately in test_harness.py.
    Unit tests for the ledger tools focus on the DB logic, not the
    confirmation prompt.
    """
    from backend.app.tools import ledger_tools
    monkeypatch.setattr(ledger_tools.AddLedgerEntryTool, "_REQUIRES_CONFIRMATION", False)
    monkeypatch.setattr(ledger_tools.RecordPaymentTool, "_REQUIRES_CONFIRMATION", False)


class TestLedgerTools:
    async def test_add_ledger_entry_debit(self, db_session: AsyncSession, disable_confirmation):
        shop = await _make_shop(db_session, "ldg_debit_1")
        customer = await _make_customer(db_session, shop.id, phone="923001001001")
        await db_session.commit()

        tool = tool_registry.get("add_ledger_entry")
        result = await tool.execute(
            {
                **_base_input(shop.id),
                "customer_id": str(customer.id),
                "amount": "2500.00",
                "ledger_type": "DEBIT",
                "note": "Ali ka udhar",
            },
            db_session,
        )
        assert result.success is True
        assert Decimal(result.data["new_balance"]) == Decimal("2500.00")
        assert result.data["type"] == "DEBIT"

    async def test_add_ledger_entry_credit(self, db_session: AsyncSession, disable_confirmation):
        shop = await _make_shop(db_session, "ldg_credit_1")
        customer = await _make_customer(db_session, shop.id, phone="923001001002")
        await db_session.commit()

        # First add debit
        tool = tool_registry.get("add_ledger_entry")
        await tool.execute(
            {
                **_base_input(shop.id),
                "customer_id": str(customer.id),
                "amount": "3000.00",
                "ledger_type": "DEBIT",
                "note": "Udhaar",
            },
            db_session,
        )
        # Then add credit
        result = await tool.execute(
            {
                **_base_input(shop.id),
                "customer_id": str(customer.id),
                "amount": "1000.00",
                "ledger_type": "CREDIT",
                "note": "Partial payment",
            },
            db_session,
        )
        assert Decimal(result.data["new_balance"]) == Decimal("2000.00")

    async def test_add_ledger_entry_negative_amount_raises(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "ldg_neg_1")
        customer = await _make_customer(db_session, shop.id, phone="923001001003")
        await db_session.commit()

        tool = tool_registry.get("add_ledger_entry")
        with pytest.raises(ValidationError):
            await tool.execute(
                {
                    **_base_input(shop.id),
                    "customer_id": str(customer.id),
                    "amount": "-500.00",
                    "ledger_type": "DEBIT",
                },
                db_session,
            )

    async def test_add_ledger_entry_unknown_customer(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "ldg_404_1")
        await db_session.commit()

        tool = tool_registry.get("add_ledger_entry")
        with pytest.raises((NotFoundError, ValidationError)):
            await tool.execute(
                {
                    **_base_input(shop.id),
                    "customer_id": str(uuid.uuid4()),
                    "amount": "500.00",
                    "ledger_type": "DEBIT",
                },
                db_session,
            )

    async def test_add_ledger_entry_wrong_shop_raises(self, db_session: AsyncSession):
        shop_a = await _make_shop(db_session, "ldg_iso_a")
        shop_b = await _make_shop(db_session, "ldg_iso_b")
        customer = await _make_customer(db_session, shop_a.id, phone="923001001004")
        await db_session.commit()

        tool = tool_registry.get("add_ledger_entry")
        with pytest.raises(TenantIsolationError):
            await tool.execute(
                {
                    **_base_input(shop_b.id),
                    "customer_id": str(customer.id),
                    "amount": "500.00",
                    "ledger_type": "DEBIT",
                },
                db_session,
            )

    async def test_get_balance_returns_correct_balance(self, db_session: AsyncSession, disable_confirmation):
        shop = await _make_shop(db_session, "ldg_bal_1")
        customer = await _make_customer(db_session, shop.id, phone="923001001005")
        await db_session.commit()

        add_tool = tool_registry.get("add_ledger_entry")
        await add_tool.execute(
            {
                **_base_input(shop.id),
                "customer_id": str(customer.id),
                "amount": "5000.00",
                "ledger_type": "DEBIT",
            },
            db_session,
        )
        await add_tool.execute(
            {
                **_base_input(shop.id),
                "customer_id": str(customer.id),
                "amount": "2000.00",
                "ledger_type": "CREDIT",
            },
            db_session,
        )

        bal_tool = tool_registry.get("get_balance")
        result = await bal_tool.execute(
            {**_base_input(shop.id), "customer_id": str(customer.id)},
            db_session,
        )
        assert Decimal(result.data["current_balance"]) == Decimal("3000.00")

    async def test_record_payment_creates_credit(self, db_session: AsyncSession, disable_confirmation):
        shop = await _make_shop(db_session, "ldg_pay_1")
        customer = await _make_customer(db_session, shop.id, phone="923001001006")
        await db_session.commit()

        # Add debit first
        add_tool = tool_registry.get("add_ledger_entry")
        await add_tool.execute(
            {
                **_base_input(shop.id),
                "customer_id": str(customer.id),
                "amount": "4000.00",
                "ledger_type": "DEBIT",
            },
            db_session,
        )

        pay_tool = tool_registry.get("record_payment")
        result = await pay_tool.execute(
            {
                **_base_input(shop.id),
                "customer_id": str(customer.id),
                "amount": "1500.00",
            },
            db_session,
        )
        assert result.success is True
        assert Decimal(result.data["new_balance"]) == Decimal("2500.00")


# ===========================================================================
# MODULE 14 — Order Tools
# ===========================================================================


class TestOrderTools:
    async def test_create_order_success(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "ord_create_1")
        customer = await _make_customer(db_session, shop.id, phone="923001002001")
        product = await _make_product(db_session, shop.id, stock=50, price=Decimal("100.00"))
        await db_session.commit()

        tool = tool_registry.get("create_order")
        result = await tool.execute(
            {
                **_base_input(shop.id),
                "customer_id": str(customer.id),
                "items": [{"product_id": str(product.id), "quantity": 3}],
            },
            db_session,
        )
        assert result.success is True
        assert Decimal(result.data["total_amount"]) == Decimal("300.00")
        assert result.data["status"] == "CONFIRMED"

    async def test_create_order_decrements_stock(self, db_session: AsyncSession):
        from sqlalchemy import select

        shop = await _make_shop(db_session, "ord_stock_1")
        product = await _make_product(db_session, shop.id, stock=20)
        await db_session.commit()

        tool = tool_registry.get("create_order")
        await tool.execute(
            {
                **_base_input(shop.id),
                "items": [{"product_id": str(product.id), "quantity": 5}],
            },
            db_session,
        )
        await db_session.commit()

        result = await db_session.execute(
            select(Product).where(Product.id == product.id)
        )
        refreshed = result.scalar_one()
        assert refreshed.current_stock == 15

    async def test_create_order_insufficient_stock_raises(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "ord_insuff_1")
        product = await _make_product(db_session, shop.id, stock=3)
        await db_session.commit()

        tool = tool_registry.get("create_order")
        with pytest.raises(ValidationError, match="Insufficient stock"):
            await tool.execute(
                {
                    **_base_input(shop.id),
                    "items": [{"product_id": str(product.id), "quantity": 10}],
                },
                db_session,
            )

    async def test_create_order_writes_audit(self, db_session: AsyncSession):
        from sqlalchemy import select
        from backend.app.models.audit_log import AuditLog

        shop = await _make_shop(db_session, "ord_audit_1")
        product = await _make_product(db_session, shop.id, stock=20)
        await db_session.commit()

        tool = tool_registry.get("create_order")
        await tool.execute(
            {
                **_base_input(shop.id),
                "items": [{"product_id": str(product.id), "quantity": 1}],
            },
            db_session,
        )
        await db_session.commit()

        result = await db_session.execute(
            select(AuditLog).where(AuditLog.shop_id == shop.id)
        )
        logs = result.scalars().all()
        assert any(log.action == "ORDER_CREATED" for log in logs)

    async def test_get_order_returns_items(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "ord_get_1")
        product = await _make_product(db_session, shop.id, stock=20)
        await db_session.commit()

        create_tool = tool_registry.get("create_order")
        create_result = await create_tool.execute(
            {
                **_base_input(shop.id),
                "items": [{"product_id": str(product.id), "quantity": 2}],
            },
            db_session,
        )

        get_tool = tool_registry.get("get_order")
        result = await get_tool.execute(
            {**_base_input(shop.id), "order_id": create_result.data["order_id"]},
            db_session,
        )
        assert result.success is True
        assert len(result.data["items"]) == 1
        assert result.data["items"][0]["quantity"] == 2

    async def test_get_order_wrong_shop_raises(self, db_session: AsyncSession):
        shop_a = await _make_shop(db_session, "ord_iso_a")
        shop_b = await _make_shop(db_session, "ord_iso_b")
        product = await _make_product(db_session, shop_a.id, stock=20)
        await db_session.commit()

        create_tool = tool_registry.get("create_order")
        create_result = await create_tool.execute(
            {
                **_base_input(shop_a.id),
                "items": [{"product_id": str(product.id), "quantity": 1}],
            },
            db_session,
        )

        get_tool = tool_registry.get("get_order")
        with pytest.raises(NotFoundError):
            await get_tool.execute(
                {**_base_input(shop_b.id), "order_id": create_result.data["order_id"]},
                db_session,
            )

    async def test_cancel_order_restores_stock(self, db_session: AsyncSession):
        from sqlalchemy import select

        shop = await _make_shop(db_session, "ord_cancel_1")
        product = await _make_product(db_session, shop.id, stock=20)
        await db_session.commit()

        create_tool = tool_registry.get("create_order")
        create_result = await create_tool.execute(
            {
                **_base_input(shop.id),
                "items": [{"product_id": str(product.id), "quantity": 5}],
            },
            db_session,
        )

        cancel_tool = tool_registry.get("cancel_order")
        cancel_result = await cancel_tool.execute(
            {**_base_input(shop.id), "order_id": create_result.data["order_id"]},
            db_session,
        )
        assert cancel_result.data["status"] == "CANCELLED"
        await db_session.commit()

        result = await db_session.execute(
            select(Product).where(Product.id == product.id)
        )
        refreshed = result.scalar_one()
        assert refreshed.current_stock == 20  # stock restored

    async def test_cancel_order_idempotent(self, db_session: AsyncSession):
        shop = await _make_shop(db_session, "ord_idem_1")
        product = await _make_product(db_session, shop.id, stock=20)
        await db_session.commit()

        create_tool = tool_registry.get("create_order")
        create_result = await create_tool.execute(
            {
                **_base_input(shop.id),
                "items": [{"product_id": str(product.id), "quantity": 2}],
            },
            db_session,
        )
        order_id = create_result.data["order_id"]

        cancel_tool = tool_registry.get("cancel_order")
        r1 = await cancel_tool.execute(
            {**_base_input(shop.id), "order_id": order_id}, db_session
        )
        r2 = await cancel_tool.execute(
            {**_base_input(shop.id), "order_id": order_id}, db_session
        )
        assert r1.data["status"] == "CANCELLED"
        assert r2.data["status"] == "CANCELLED"
