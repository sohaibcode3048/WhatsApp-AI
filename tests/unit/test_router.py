"""MODULE 09 — Customer/Owner Message Router: complete test suite.

Coverage:
  MessageIntent / SenderRole enums — all values present

  RoleResolver
    - known OWNER phone → SenderRole.OWNER
    - known STAFF phone → SenderRole.STAFF
    - customer phone in customers table → SenderRole.CUSTOMER
    - completely unknown phone → SenderRole.UNKNOWN
    - shop_id=None → SenderRole.UNKNOWN

  IntentClassifier (deterministic rules)
    Owner intents:
      - "udhar likh do" + DEBIT → OWNER_LEDGER
      - "jama karo" + CREDIT → OWNER_LEDGER
      - owner + name + amount (no keyword) → OWNER_LEDGER (0.85)
      - "stock check karo" → OWNER_STOCK
      - "daily report chahiye" → OWNER_REPORT
      - "customer list dikhao" → OWNER_CUSTOMER
      - "reminder set karo" → OWNER_CONFIGURATION
      - "order create karo" → OWNER_ORDER
    Customer intents:
      - "mera balance kitna hai" → CUSTOMER_BALANCE_QUERY
      - "order karna hai" → CUSTOMER_ORDER
      - "Coke ka rate kya hai" → CUSTOMER_PRODUCT_QUERY
    Universal:
      - "dukan ka address kya hai" → GENERAL_SHOP_INFO (no auth)
      - unknown role + DEBIT keyword → OWNER_LEDGER (0.7)
      - no matching rule → UNKNOWN (0.5)
    Requires_auth:
      - GENERAL_SHOP_INFO → False
      - CUSTOMER_PRODUCT_QUERY → False
      - OWNER_LEDGER → True

  RouterService.route (integration with DB)
    - OWNER routes to OWNER_LEDGER for udhaar message
    - STAFF routes to OWNER_LEDGER for udhaar message
    - CUSTOMER routes to CUSTOMER_BALANCE_QUERY
    - UNKNOWN sender routes to GENERAL_SHOP_INFO for shop query
    - UNKNOWN sender with ledger keyword → OWNER_LEDGER (0.7 confidence)
    - suggested_tool populated for known intents
    - requires_auth False for GENERAL_SHOP_INFO
    - shop_id None → UNKNOWN role
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.customer import Customer
from backend.app.models.user import User, UserRole
from backend.app.schemas.router import MessageIntent, RoutedMessage, RouterContext, SenderRole
from backend.app.services.router_service import IntentClassifier, RoleResolver, RouterService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(
    raw: str = "test",
    normalized: str | None = None,          # defaults to raw when omitted
    ledger_kw: str = "NONE",
    has_amount: bool = False,
    names: list[str] | None = None,
    sender_phone: str = "923001111111",
    shop_id: str | None = None,
    message_type: str = "text",
) -> RouterContext:
    return RouterContext(
        sender_phone=sender_phone,
        shop_id=shop_id,
        raw_text=raw,
        normalized_text=normalized if normalized is not None else raw,
        message_type=message_type,
        ledger_keyword=ledger_kw,
        has_amount=has_amount,
        name_candidates=names or [],
    )


async def _make_shop_user(db: AsyncSession, phone: str, role: UserRole) -> tuple:
    """Create a minimal Shop + User and return (shop_id, user)."""
    from backend.app.models.shop import Shop

    shop = Shop(
        name="Test Shop",
        phone_number=f"shop_{phone}",
        default_language="roman_urdu",
        currency="PKR",
        settings={},
    )
    db.add(shop)
    await db.flush()

    user = User(
        shop_id=shop.id,
        name="Test User",
        phone_number=phone,
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return shop.id, user


async def _make_customer(db: AsyncSession, phone: str) -> tuple:
    """Create a Shop + Customer and return (shop_id, customer)."""
    from backend.app.models.shop import Shop

    shop = Shop(
        name="Cust Shop",
        phone_number=f"cshop_{phone}",
        default_language="roman_urdu",
        currency="PKR",
        settings={},
    )
    db.add(shop)
    await db.flush()

    customer = Customer(
        shop_id=shop.id,
        name="Test Customer",
        phone_number=phone,
        is_active=True,
    )
    db.add(customer)
    await db.flush()
    return shop.id, customer


# ===========================================================================
# Enum sanity
# ===========================================================================


class TestEnums:
    def test_message_intent_values(self):
        assert MessageIntent.OWNER_LEDGER == "OWNER_LEDGER"
        assert MessageIntent.OWNER_STOCK == "OWNER_STOCK"
        assert MessageIntent.CUSTOMER_BALANCE_QUERY == "CUSTOMER_BALANCE_QUERY"
        assert MessageIntent.GENERAL_SHOP_INFO == "GENERAL_SHOP_INFO"
        assert MessageIntent.UNKNOWN == "UNKNOWN"
        assert MessageIntent.HUMAN_HANDOFF == "HUMAN_HANDOFF"

    def test_sender_role_values(self):
        assert SenderRole.OWNER == "OWNER"
        assert SenderRole.STAFF == "STAFF"
        assert SenderRole.CUSTOMER == "CUSTOMER"
        assert SenderRole.UNKNOWN == "UNKNOWN"


# ===========================================================================
# RoleResolver
# ===========================================================================


class TestRoleResolver:
    async def test_owner_phone_resolves_to_owner(self, db_session: AsyncSession):
        phone = "923009990001"
        shop_id, _ = await _make_shop_user(db_session, phone, UserRole.OWNER)
        await db_session.commit()

        resolver = RoleResolver(db_session)
        role = await resolver.resolve(phone, shop_id)
        assert role == SenderRole.OWNER

    async def test_staff_phone_resolves_to_staff(self, db_session: AsyncSession):
        phone = "923009990002"
        shop_id, _ = await _make_shop_user(db_session, phone, UserRole.STAFF)
        await db_session.commit()

        resolver = RoleResolver(db_session)
        role = await resolver.resolve(phone, shop_id)
        assert role == SenderRole.STAFF

    async def test_customer_phone_resolves_to_customer(self, db_session: AsyncSession):
        phone = "923009990003"
        shop_id, _ = await _make_customer(db_session, phone)
        await db_session.commit()

        resolver = RoleResolver(db_session)
        role = await resolver.resolve(phone, shop_id)
        assert role == SenderRole.CUSTOMER

    async def test_unknown_phone_resolves_to_unknown(self, db_session: AsyncSession):
        from backend.app.models.shop import Shop

        shop = Shop(
            name="Empty Shop",
            phone_number="empty_shop_ph",
            default_language="roman_urdu",
            currency="PKR",
            settings={},
        )
        db_session.add(shop)
        await db_session.commit()

        resolver = RoleResolver(db_session)
        role = await resolver.resolve("923009999999", shop.id)
        assert role == SenderRole.UNKNOWN

    async def test_none_shop_id_resolves_to_unknown(self, db_session: AsyncSession):
        resolver = RoleResolver(db_session)
        role = await resolver.resolve("923001234567", None)
        assert role == SenderRole.UNKNOWN


# ===========================================================================
# IntentClassifier
# ===========================================================================


class TestIntentClassifier:
    def setup_method(self):
        self.clf = IntentClassifier()

    def _classify(self, ctx: RouterContext, role: SenderRole) -> MessageIntent:
        intent, _, _ = self.clf.classify(ctx, role)
        return intent

    def _confidence(self, ctx: RouterContext, role: SenderRole) -> float:
        _, conf, _ = self.clf.classify(ctx, role)
        return conf

    # --- Owner ledger ---
    def test_owner_udhar_likh_do(self):
        ctx = _ctx("Ali ka 2500 udhar likh do", ledger_kw="DEBIT", has_amount=True, names=["Ali"])
        assert self._classify(ctx, SenderRole.OWNER) == MessageIntent.OWNER_LEDGER

    def test_owner_jama_karo(self):
        ctx = _ctx("Ali ne 1000 jama kiya", ledger_kw="CREDIT", has_amount=True, names=["Ali"])
        assert self._classify(ctx, SenderRole.OWNER) == MessageIntent.OWNER_LEDGER

    def test_owner_name_amount_no_keyword_fallback(self):
        # No explicit ledger keyword but owner + name + amount → OWNER_LEDGER at 0.85
        ctx = _ctx("Ahmed 3000", ledger_kw="NONE", has_amount=True, names=["Ahmed"])
        intent, conf, _ = self.clf.classify(ctx, SenderRole.OWNER)
        assert intent == MessageIntent.OWNER_LEDGER
        assert conf == pytest.approx(0.85)

    def test_staff_udhar(self):
        ctx = _ctx("2000 udhar likh do", ledger_kw="DEBIT", has_amount=True)
        assert self._classify(ctx, SenderRole.STAFF) == MessageIntent.OWNER_LEDGER

    # --- Owner stock ---
    def test_owner_stock_check(self):
        ctx = _ctx("Coke kitna stock hai")
        assert self._classify(ctx, SenderRole.OWNER) == MessageIntent.OWNER_STOCK

    def test_owner_stock_update(self):
        ctx = _ctx("50 Coke add stock karo")
        assert self._classify(ctx, SenderRole.OWNER) == MessageIntent.OWNER_STOCK

    # --- Owner report ---
    def test_owner_daily_report(self):
        ctx = _ctx("aaj ki daily sales summary chahiye")
        assert self._classify(ctx, SenderRole.OWNER) == MessageIntent.OWNER_REPORT

    # --- Owner customer management ---
    def test_owner_customer_list(self):
        ctx = _ctx("customer list dikhao")
        assert self._classify(ctx, SenderRole.OWNER) == MessageIntent.OWNER_CUSTOMER

    # --- Owner configuration ---
    def test_owner_reminder_config(self):
        ctx = _ctx("reminder set karo kal subah")
        assert self._classify(ctx, SenderRole.OWNER) == MessageIntent.OWNER_CONFIGURATION

    # --- Owner order ---
    def test_owner_create_order(self):
        ctx = _ctx("new order create karo Ali ke liye", names=["Ali"])
        assert self._classify(ctx, SenderRole.OWNER) == MessageIntent.OWNER_ORDER

    # --- Customer balance ---
    def test_customer_balance_query(self):
        ctx = _ctx("mera balance kitna hai")
        assert self._classify(ctx, SenderRole.CUSTOMER) == MessageIntent.CUSTOMER_BALANCE_QUERY

    def test_customer_mera_udhar(self):
        ctx = _ctx("mera udhar kitna hai")
        assert self._classify(ctx, SenderRole.CUSTOMER) == MessageIntent.CUSTOMER_BALANCE_QUERY

    # --- Customer order ---
    def test_customer_place_order(self):
        ctx = _ctx("mujhe 2 Coke order karna hai")
        assert self._classify(ctx, SenderRole.CUSTOMER) == MessageIntent.CUSTOMER_ORDER

    # --- Customer product query ---
    def test_customer_product_price(self):
        ctx = _ctx("Coke ka rate kya hai")
        assert self._classify(ctx, SenderRole.CUSTOMER) == MessageIntent.CUSTOMER_PRODUCT_QUERY

    # --- Universal intents ---
    def test_shop_info_any_role(self):
        ctx = _ctx("dukan ka address kya hai")
        for role in (SenderRole.OWNER, SenderRole.CUSTOMER, SenderRole.UNKNOWN):
            assert self._classify(ctx, role) == MessageIntent.GENERAL_SHOP_INFO

    def test_unknown_role_ledger_keyword(self):
        ctx = _ctx("2000 udhar", ledger_kw="DEBIT", has_amount=True)
        intent, conf, _ = self.clf.classify(ctx, SenderRole.UNKNOWN)
        assert intent == MessageIntent.OWNER_LEDGER
        assert conf == pytest.approx(0.7)

    def test_no_matching_rule_returns_unknown(self):
        ctx = _ctx("Assalam o alaikum")
        intent, conf, _ = self.clf.classify(ctx, SenderRole.UNKNOWN)
        assert intent == MessageIntent.UNKNOWN
        assert conf == pytest.approx(0.5)

    # --- requires_auth ---
    def test_general_shop_info_no_auth_required(self):
        ctx = _ctx("dukan ka address kya hai")
        result_intent = self._classify(ctx, SenderRole.UNKNOWN)
        assert result_intent == MessageIntent.GENERAL_SHOP_INFO
        # Verify _PUBLIC_INTENTS contains GENERAL_SHOP_INFO
        from backend.app.services.router_service import _PUBLIC_INTENTS
        assert MessageIntent.GENERAL_SHOP_INFO in _PUBLIC_INTENTS

    def test_product_query_no_auth_required(self):
        from backend.app.services.router_service import _PUBLIC_INTENTS
        assert MessageIntent.CUSTOMER_PRODUCT_QUERY in _PUBLIC_INTENTS

    def test_owner_ledger_requires_auth(self):
        from backend.app.services.router_service import _PUBLIC_INTENTS
        assert MessageIntent.OWNER_LEDGER not in _PUBLIC_INTENTS


# ===========================================================================
# RouterService integration (with DB)
# ===========================================================================


class TestRouterService:
    async def test_owner_ledger_route(self, db_session: AsyncSession):
        phone = "923001230001"
        shop_id, _ = await _make_shop_user(db_session, phone, UserRole.OWNER)
        await db_session.commit()

        router = RouterService(db_session)
        ctx = _ctx(
            raw="Ali ka 2500 udhar likh do",
            normalized="Ali ka 2500 udhar likh do",
            ledger_kw="DEBIT",
            has_amount=True,
            names=["Ali"],
            sender_phone=phone,
            shop_id=str(shop_id),
        )
        result = await router.route(ctx)

        assert result.sender_role == SenderRole.OWNER
        assert result.intent == MessageIntent.OWNER_LEDGER
        assert result.confidence == pytest.approx(1.0)
        assert result.requires_auth is True
        assert result.suggested_tool == "add_ledger_entry"

    async def test_staff_ledger_route(self, db_session: AsyncSession):
        phone = "923001230002"
        shop_id, _ = await _make_shop_user(db_session, phone, UserRole.STAFF)
        await db_session.commit()

        router = RouterService(db_session)
        ctx = _ctx(
            raw="2000 udhar likh do",
            normalized="2000 udhar likh do",
            ledger_kw="DEBIT",
            has_amount=True,
            sender_phone=phone,
            shop_id=str(shop_id),
        )
        result = await router.route(ctx)
        assert result.sender_role == SenderRole.STAFF
        assert result.intent == MessageIntent.OWNER_LEDGER

    async def test_customer_balance_route(self, db_session: AsyncSession):
        phone = "923001230003"
        shop_id, _ = await _make_customer(db_session, phone)
        await db_session.commit()

        router = RouterService(db_session)
        ctx = _ctx(
            raw="mera balance kitna hai",
            normalized="mera balance kitna hai",
            sender_phone=phone,
            shop_id=str(shop_id),
        )
        result = await router.route(ctx)
        assert result.sender_role == SenderRole.CUSTOMER
        assert result.intent == MessageIntent.CUSTOMER_BALANCE_QUERY
        assert result.requires_auth is True

    async def test_unknown_sender_shop_info(self, db_session: AsyncSession):
        from backend.app.models.shop import Shop

        shop = Shop(
            name="Info Shop",
            phone_number="infoshop_ph",
            default_language="roman_urdu",
            currency="PKR",
            settings={},
        )
        db_session.add(shop)
        await db_session.commit()

        router = RouterService(db_session)
        ctx = _ctx(
            raw="dukan ka address kya hai",
            normalized="dukan ka address kya hai",
            sender_phone="923009001122",
            shop_id=str(shop.id),
        )
        result = await router.route(ctx)
        assert result.sender_role == SenderRole.UNKNOWN
        assert result.intent == MessageIntent.GENERAL_SHOP_INFO
        assert result.requires_auth is False

    async def test_unknown_sender_with_ledger_keyword(self, db_session: AsyncSession):
        from backend.app.models.shop import Shop

        shop = Shop(
            name="Ledger Shop",
            phone_number="ldgshop_ph",
            default_language="roman_urdu",
            currency="PKR",
            settings={},
        )
        db_session.add(shop)
        await db_session.commit()

        router = RouterService(db_session)
        ctx = _ctx(
            raw="5000 udhar",
            normalized="5000 udhar",
            ledger_kw="DEBIT",
            has_amount=True,
            sender_phone="923009003344",
            shop_id=str(shop.id),
        )
        result = await router.route(ctx)
        assert result.intent == MessageIntent.OWNER_LEDGER
        assert result.confidence == pytest.approx(0.7)

    async def test_suggested_tool_populated(self, db_session: AsyncSession):
        phone = "923001230004"
        shop_id, _ = await _make_shop_user(db_session, phone, UserRole.OWNER)
        await db_session.commit()

        router = RouterService(db_session)
        ctx = _ctx(
            raw="Coke kitna stock hai",
            normalized="Coke kitna stock hai",
            sender_phone=phone,
            shop_id=str(shop_id),
        )
        result = await router.route(ctx)
        assert result.intent == MessageIntent.OWNER_STOCK
        assert result.suggested_tool == "check_stock"

    async def test_none_shop_id_returns_unknown_role(self, db_session: AsyncSession):
        router = RouterService(db_session)
        ctx = _ctx(
            raw="kuch bhi",
            normalized="kuch bhi",
            sender_phone="923009005566",
            shop_id=None,
        )
        result = await router.route(ctx)
        assert result.sender_role == SenderRole.UNKNOWN

    async def test_routed_message_is_pydantic_model(self, db_session: AsyncSession):
        router = RouterService(db_session)
        ctx = _ctx(sender_phone="923009007788", shop_id=None)
        result = await router.route(ctx)
        assert isinstance(result, RoutedMessage)
