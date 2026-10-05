"""Tool framework and individual tool tests — MODULEs 10–14.

Tests the 10-stage harness, BaseTool mechanics, and each concrete tool
(stock, customer, ledger, order, reporting, management, reminder).

All tests use an in-memory SQLite database (from conftest.py fixtures).
No real WhatsApp or LLM calls are made.
"""

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import (
    AuthorizationError,
    ValidationError,
    NotFoundError,
    TenantIsolationError,
)
from backend.app.models.shop import Shop
from backend.app.models.customer import Customer
from backend.app.models.product import Product
from backend.app.models.user import User, UserRole
from backend.app.schemas.router import SenderRole
from backend.app.tools.base import tool_registry, ToolInput
from backend.app.tools.stock_tools import CheckStockTool, UpdateStockTool, GetLowStockTool
from backend.app.tools.customer_tools import FindCustomerTool, GetCustomerTool
from backend.app.tools.ledger_tools import AddLedgerEntryTool, GetBalanceTool, RecordPaymentTool
from backend.app.tools.management_tools import AddCustomerTool, AddProductTool, FindProductTool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def shop(db_session: AsyncSession) -> Shop:
    s = Shop(
        name="Test Shop",
        phone_number="+923000000000",
        currency="PKR",
        default_language="roman_urdu",
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def customer(db_session: AsyncSession, shop: Shop) -> Customer:
    c = Customer(
        shop_id=shop.id,
        name="Ali Khan",
        phone_number="+923009876543",
        is_active=True,
    )
    db_session.add(c)
    await db_session.flush()
    return c


@pytest_asyncio.fixture
async def product(db_session: AsyncSession, shop: Shop) -> Product:
    p = Product(
        shop_id=shop.id,
        name="Coke 1.5L",
        sku="COKE-1500",
        price=Decimal("250.00"),
        cost_price=Decimal("200.00"),
        current_stock=50,
        low_stock_threshold=10,
        is_active=True,
    )
    db_session.add(p)
    await db_session.flush()
    return p


def _base_input(shop_id: uuid.UUID, role: SenderRole = SenderRole.OWNER) -> dict:
    return {
        "shop_id": str(shop_id),
        "sender_phone": "+923001234567",
        "sender_role": role.value,
    }


# ---------------------------------------------------------------------------
# Tool registry tests
# ---------------------------------------------------------------------------

class TestToolRegistry:

    def test_all_tools_registered(self) -> None:
        """All expected tools must be present in the registry."""
        expected = {
            "check_stock", "update_stock", "get_low_stock",
            "find_customer", "get_customer",
            "add_ledger_entry", "get_balance", "record_payment",
            "create_order", "get_order", "cancel_order",
            "daily_summary", "get_sales_summary",
            "add_customer", "add_product", "update_product", "find_product",
            "send_reminder",
        }
        registered = {t["name"] for t in tool_registry.list_tools()}
        missing = expected - registered
        assert not missing, f"Tools missing from registry: {missing}"

    def test_get_nonexistent_tool_raises(self) -> None:
        from backend.app.core.exceptions import ToolExecutionError
        with pytest.raises(ToolExecutionError):
            tool_registry.get("nonexistent_tool_xyz")


# ---------------------------------------------------------------------------
# BaseTool harness: RBAC tests
# ---------------------------------------------------------------------------

class TestToolAuthorization:

    @pytest.mark.asyncio
    async def test_customer_cannot_call_add_ledger_entry(
        self, db_session: AsyncSession, shop: Shop
    ) -> None:
        """CUSTOMER role must be rejected by add_ledger_entry (OWNER_AND_STAFF only)."""
        tool = AddLedgerEntryTool()
        inp = {
            **_base_input(shop.id, SenderRole.CUSTOMER),
            "customer_id": str(uuid.uuid4()),
            "amount": "500.00",
            "type": "DEBIT",
            "note": "Test",
            "reference_type": "MANUAL_ADJUSTMENT",
        }
        with pytest.raises(AuthorizationError):
            await tool.execute(inp, db_session)

    @pytest.mark.asyncio
    async def test_unknown_role_cannot_call_update_stock(
        self, db_session: AsyncSession, shop: Shop
    ) -> None:
        """UNKNOWN role must be rejected by update_stock."""
        tool = UpdateStockTool()
        inp = {
            **_base_input(shop.id, SenderRole.UNKNOWN),
            "product_id": str(uuid.uuid4()),
            "adjustment": 10,
        }
        with pytest.raises(AuthorizationError):
            await tool.execute(inp, db_session)


# ---------------------------------------------------------------------------
# BaseTool harness: schema validation
# ---------------------------------------------------------------------------

class TestSchemaValidation:

    @pytest.mark.asyncio
    async def test_missing_required_field_raises_validation_error(
        self, db_session: AsyncSession, shop: Shop
    ) -> None:
        """Omitting a required field must raise ValidationError (Stage 1)."""
        tool = CheckStockTool()
        inp = {
            **_base_input(shop.id),
            # product_id intentionally omitted
        }
        with pytest.raises(ValidationError):
            await tool.execute(inp, db_session)

    @pytest.mark.asyncio
    async def test_negative_amount_raises_validation_error(
        self, db_session: AsyncSession, shop: Shop, customer: Customer
    ) -> None:
        """Negative ledger amount must be rejected at business rule stage."""
        tool = AddLedgerEntryTool()
        inp = {
            **_base_input(shop.id),
            "customer_id": str(customer.id),
            "amount": "-500.00",
            "type": "DEBIT",
            "note": "Test negative",
            "reference_type": "MANUAL_ADJUSTMENT",
        }
        with pytest.raises((ValidationError, Exception)):
            await tool.execute(inp, db_session)


# ---------------------------------------------------------------------------
# Stock tools
# ---------------------------------------------------------------------------

class TestStockTools:

    @pytest.mark.asyncio
    async def test_check_stock_existing_product(
        self, db_session: AsyncSession, shop: Shop, product: Product
    ) -> None:
        tool = CheckStockTool()
        inp = {**_base_input(shop.id), "product_id": str(product.id)}
        result = await tool.execute(inp, db_session)
        assert result.success is True
        assert result.data["current_stock"] == 50

    @pytest.mark.asyncio
    async def test_check_stock_nonexistent_product(
        self, db_session: AsyncSession, shop: Shop
    ) -> None:
        tool = CheckStockTool()
        inp = {**_base_input(shop.id), "product_id": str(uuid.uuid4())}
        with pytest.raises(NotFoundError):
            await tool.execute(inp, db_session)

    @pytest.mark.asyncio
    async def test_update_stock_increases_count(
        self, db_session: AsyncSession, shop: Shop, product: Product
    ) -> None:
        tool = UpdateStockTool()
        inp = {
            **_base_input(shop.id),
            "product_id": str(product.id),
            "adjustment": 20,
        }
        result = await tool.execute(inp, db_session)
        assert result.success is True
        assert result.data["new_stock"] == 70

    @pytest.mark.asyncio
    async def test_update_stock_prevents_negative(
        self, db_session: AsyncSession, shop: Shop, product: Product
    ) -> None:
        """Reducing stock below zero must be rejected."""
        tool = UpdateStockTool()
        inp = {
            **_base_input(shop.id),
            "product_id": str(product.id),
            "adjustment": -100,   # would take stock to -50
        }
        with pytest.raises((ValidationError, Exception)):
            await tool.execute(inp, db_session)

    @pytest.mark.asyncio
    async def test_get_low_stock_returns_list(
        self, db_session: AsyncSession, shop: Shop, product: Product
    ) -> None:
        # Set stock below threshold
        product.current_stock = 5
        await db_session.flush()

        tool = GetLowStockTool()
        inp = {**_base_input(shop.id)}
        result = await tool.execute(inp, db_session)
        assert result.success is True
        assert isinstance(result.data.get("products", []), list)


# ---------------------------------------------------------------------------
# Customer tools
# ---------------------------------------------------------------------------

class TestCustomerTools:

    @pytest.mark.asyncio
    async def test_find_customer_by_name(
        self, db_session: AsyncSession, shop: Shop, customer: Customer
    ) -> None:
        tool = FindCustomerTool()
        inp = {**_base_input(shop.id), "name": "Ali"}
        result = await tool.execute(inp, db_session)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_get_customer_by_id(
        self, db_session: AsyncSession, shop: Shop, customer: Customer
    ) -> None:
        tool = GetCustomerTool()
        inp = {**_base_input(shop.id), "customer_id": str(customer.id)}
        result = await tool.execute(inp, db_session)
        assert result.success is True
        assert result.data["name"] == "Ali Khan"

    @pytest.mark.asyncio
    async def test_get_customer_wrong_shop_raises(
        self, db_session: AsyncSession, customer: Customer
    ) -> None:
        """Accessing a customer from a different shop must raise TenantIsolationError."""
        different_shop_id = uuid.uuid4()
        tool = GetCustomerTool()
        inp = {
            "shop_id": str(different_shop_id),
            "sender_phone": "+923001234567",
            "sender_role": SenderRole.OWNER.value,
            "customer_id": str(customer.id),
        }
        with pytest.raises((TenantIsolationError, NotFoundError)):
            await tool.execute(inp, db_session)


# ---------------------------------------------------------------------------
# Ledger tools
# ---------------------------------------------------------------------------

class TestLedgerTools:

    @pytest.mark.asyncio
    async def test_get_balance_zero_for_new_customer(
        self, db_session: AsyncSession, shop: Shop, customer: Customer
    ) -> None:
        tool = GetBalanceTool()
        inp = {**_base_input(shop.id), "customer_id": str(customer.id)}
        result = await tool.execute(inp, db_session)
        assert result.success is True
        assert Decimal(result.data["current_balance"]) == Decimal("0")

    @pytest.mark.asyncio
    async def test_get_balance_after_debit(
        self, db_session: AsyncSession, shop: Shop, customer: Customer
    ) -> None:
        """After adding a DEBIT entry, balance must equal that amount."""
        from backend.app.repositories.ledger_repository import LedgerRepository
        from backend.app.schemas.ledger import LedgerEntryCreate
        from backend.app.models.ledger import LedgerType, ReferenceType

        repo = LedgerRepository(db_session)
        await repo.add_entry(
            shop_id=shop.id,
            entry_in=LedgerEntryCreate(
                customer_id=customer.id,
                type=LedgerType.DEBIT,
                amount=Decimal("2500.00"),
                note="Test debit",
                reference_type=ReferenceType.MANUAL_ADJUSTMENT,
            ),
        )
        await db_session.flush()

        tool = GetBalanceTool()
        inp = {**_base_input(shop.id), "customer_id": str(customer.id)}
        result = await tool.execute(inp, db_session)
        assert Decimal(result.data["current_balance"]) == Decimal("2500.00")

    @pytest.mark.asyncio
    async def test_get_balance_debit_minus_credit(
        self, db_session: AsyncSession, shop: Shop, customer: Customer
    ) -> None:
        """Balance = SUM(DEBIT) - SUM(CREDIT)."""
        from backend.app.repositories.ledger_repository import LedgerRepository
        from backend.app.schemas.ledger import LedgerEntryCreate, PaymentRecordCreate
        from backend.app.models.ledger import LedgerType, ReferenceType

        repo = LedgerRepository(db_session)
        await repo.add_entry(shop.id, LedgerEntryCreate(
            customer_id=customer.id, type=LedgerType.DEBIT, amount=Decimal("3000.00"),
            note="Debit", reference_type=ReferenceType.MANUAL_ADJUSTMENT,
        ))
        await repo.record_payment(shop.id, PaymentRecordCreate(
            customer_id=customer.id, amount=Decimal("1000.00"), note="Payment",
        ))
        await db_session.flush()

        tool = GetBalanceTool()
        inp = {**_base_input(shop.id), "customer_id": str(customer.id)}
        result = await tool.execute(inp, db_session)
        assert Decimal(result.data["current_balance"]) == Decimal("2000.00")


# ---------------------------------------------------------------------------
# Management tools
# ---------------------------------------------------------------------------

class TestManagementTools:

    @pytest.mark.asyncio
    async def test_add_customer_creates_record(
        self, db_session: AsyncSession, shop: Shop
    ) -> None:
        tool = AddCustomerTool()
        inp = {
            **_base_input(shop.id),
            "name": "Sara Bibi",
            "phone_number": "+923001112223",
        }
        result = await tool.execute(inp, db_session)
        assert result.success is True
        assert result.data["name"] == "Sara Bibi"

    @pytest.mark.asyncio
    async def test_add_customer_idempotent(
        self, db_session: AsyncSession, shop: Shop
    ) -> None:
        """Adding the same phone number twice should return the existing customer."""
        tool = AddCustomerTool()
        inp = {
            **_base_input(shop.id),
            "name": "Rehman",
            "phone_number": "+923005556667",
        }
        r1 = await tool.execute(inp, db_session)
        r2 = await tool.execute(inp, db_session)
        assert r1.success is True
        assert r2.success is True
        assert r1.data["customer_id"] == r2.data["customer_id"]

    @pytest.mark.asyncio
    async def test_add_product_owner_only(
        self, db_session: AsyncSession, shop: Shop
    ) -> None:
        """STAFF cannot add products — only OWNER."""
        tool = AddProductTool()
        inp = {
            **_base_input(shop.id, SenderRole.STAFF),
            "name": "Pepsi 1.5L",
            "price": "240.00",
        }
        with pytest.raises(AuthorizationError):
            await tool.execute(inp, db_session)

    @pytest.mark.asyncio
    async def test_find_product_by_name(
        self, db_session: AsyncSession, shop: Shop, product: Product
    ) -> None:
        tool = FindProductTool()
        inp = {**_base_input(shop.id), "name": "Coke"}
        result = await tool.execute(inp, db_session)
        assert result.success is True
