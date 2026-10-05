"""Message Router Service — MODULE 09.

Responsibilities
----------------
1. Resolve sender role  — look up the sender's phone number in the users table.
   If not found, check the customers table; otherwise mark UNKNOWN.
2. Classify intent      — deterministic rule-based classification using the
   pre-computed LanguageService output (ledger_keyword, amounts, name_candidates)
   plus lightweight keyword scanning on the normalized text.
3. Return RoutedMessage — a flat, auditable object consumed by the agent pipeline.

Design notes
------------
* No LLM calls in this module.  Deterministic rules keep latency near zero and
  make every routing decision fully auditable.
* The agent (MODULE 19) may override the intent if it has higher confidence,
  but it must still pass every tool call through the Harness regardless.
* Role resolution is the source of truth for permissions — the sender_type stored
  on the Message row is an early heuristic; the RouterService does the authoritative
  DB lookup every time.
"""

import re
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.logging import logger
from backend.app.models.user import UserRole
from backend.app.repositories.customer_repository import CustomerRepository
from backend.app.repositories.user_repository import UserRepository
from backend.app.schemas.router import (
    MessageIntent,
    RoutedMessage,
    RouterContext,
    SenderRole,
)

# ---------------------------------------------------------------------------
# Role resolution
# ---------------------------------------------------------------------------


class RoleResolver:
    """Resolve the authenticated role of a sender within a tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._user_repo = UserRepository(session)
        self._customer_repo = CustomerRepository(session)

    async def resolve(
        self,
        sender_phone: str,
        shop_id: Optional[uuid.UUID],
    ) -> SenderRole:
        """Return the sender's role within the given shop.

        Lookup order:
          1. users table  → OWNER / STAFF (registered shop users)
          2. customers table → CUSTOMER (registered retail customer)
          3. Not found → UNKNOWN
        """
        if shop_id is None:
            return SenderRole.UNKNOWN

        # Check users table first (OWNER / STAFF)
        user = await self._user_repo.get_by_phone_and_shop(sender_phone, shop_id)
        if user:
            role_map = {
                UserRole.OWNER: SenderRole.OWNER,
                UserRole.STAFF: SenderRole.STAFF,
                UserRole.CUSTOMER: SenderRole.CUSTOMER,
            }
            return role_map.get(user.role, SenderRole.UNKNOWN)

        # Check customers table
        customer = await self._customer_repo.get_by_phone(sender_phone, shop_id)
        if customer:
            return SenderRole.CUSTOMER

        return SenderRole.UNKNOWN


# ---------------------------------------------------------------------------
# Intent classification (deterministic rules)
# ---------------------------------------------------------------------------

# --- Owner-specific intent signals ---

_LEDGER_WORDS = re.compile(
    r"\b(udhar|udhaar|ادھار|khata|khate|کھاتہ|"
    r"likh\s*do|لکھ\s*دو|credit\s*kar|credit\s*karo|"
    r"jama|جمع|wasool|وصول|ada\s*kiya|ادا\s*کیا|"
    r"paid|payment|wapas|واپس|baaki|باقی|bakaya|بقایا)\b",
    re.IGNORECASE,
)

_STOCK_WORDS = re.compile(
    r"\b(stock|inventory|available|kitna\s*hai|"
    r"quantity|maal|مال|"
    r"add\s*stock|update\s*stock|stock\s*update|"
    r"low\s*stock|stock\s*check|restock|"
    r"کتنا\s*ہے|دستیاب)\b",
    re.IGNORECASE,
)

_ORDER_WORDS = re.compile(
    r"\b(order|آرڈر|book|بک|cancel|منسوخ|"
    r"deliver|delivery|ڈیلیوری|"
    r"create\s*order|new\s*order|place\s*order)\b",
    re.IGNORECASE,
)

_REPORT_WORDS = re.compile(
    r"\b(report|summary|sales|آمدنی|"
    r"daily|روزانہ|weekly|monthly|income|"
    r"total\s*sales|kitni\s*sales|sales\s*summary)\b",
    re.IGNORECASE,
)

_CUSTOMER_MGMT_WORDS = re.compile(
    r"\b(customer\s*list|customers|find\s*customer|"
    r"customer\s*info|customer\s*details|add\s*customer|"
    r"new\s*customer|delete\s*customer)\b",
    re.IGNORECASE,
)

_CONFIG_WORDS = re.compile(
    r"\b(setting|configure|reminder|notification|"
    r"language|currency|timezone|schedule|alert)\b",
    re.IGNORECASE,
)

# --- Customer-specific intent signals ---

_BALANCE_WORDS = re.compile(
    r"\b(balance|باقی\s*رقم|mera\s*udhar|mera\s*hisab|"
    r"mujhe\s*kitna|my\s*balance|how\s*much\s*do\s*i\s*owe)\b",
    re.IGNORECASE,
)

_PRODUCT_QUERY_WORDS = re.compile(
    r"\b(rate|price|قیمت|cost|kitna|کتنا|"
    r"available|دستیاب|hai\s*kya|kya\s*hai|"
    r"how\s*much|what\s*is\s*the\s*price)\b",
    re.IGNORECASE,
)

_SHOP_INFO_WORDS = re.compile(
    r"\b(address|پتہ|hours|اوقات|timing|location|"
    r"return\s*policy|delivery\s*charges|"
    r"open|close|contact|phone|number)\b",
    re.IGNORECASE,
)


class IntentClassifier:
    """Rule-based intent classifier using pre-computed LanguageService output."""

    def classify(self, ctx: RouterContext, role: SenderRole) -> tuple[MessageIntent, float, str]:
        """Return (intent, confidence, reason).

        confidence is always 1.0 for rule-based matches.
        Returns UNKNOWN with 0.5 when no rule fires.
        """
        text = ctx.normalized_text
        ledger_kw = ctx.ledger_keyword   # "DEBIT" | "CREDIT" | "NONE"
        has_amount = ctx.has_amount
        has_names = bool(ctx.name_candidates)

        # ------------------------------------------------------------------
        # Owner intents (only checked when sender is OWNER or STAFF)
        # ------------------------------------------------------------------
        if role in (SenderRole.OWNER, SenderRole.STAFF):

            # Ledger — strongest signal: explicit udhaar/jama keyword
            if ledger_kw in ("DEBIT", "CREDIT") or _LEDGER_WORDS.search(text):
                return MessageIntent.OWNER_LEDGER, 1.0, "ledger keyword detected"

            # Ledger fallback — owner + name + amount with no other context
            if role == SenderRole.OWNER and has_amount and has_names:
                return MessageIntent.OWNER_LEDGER, 0.85, "owner + name + amount → likely ledger"

            if _STOCK_WORDS.search(text):
                return MessageIntent.OWNER_STOCK, 1.0, "stock keyword detected"

            if _REPORT_WORDS.search(text):
                return MessageIntent.OWNER_REPORT, 1.0, "report keyword detected"

            if _CUSTOMER_MGMT_WORDS.search(text):
                return MessageIntent.OWNER_CUSTOMER, 1.0, "customer management keyword"

            if _CONFIG_WORDS.search(text):
                return MessageIntent.OWNER_CONFIGURATION, 1.0, "configuration keyword"

            if _ORDER_WORDS.search(text):
                return MessageIntent.OWNER_ORDER, 1.0, "order keyword detected"

        # ------------------------------------------------------------------
        # Customer intents
        # ------------------------------------------------------------------
        if role == SenderRole.CUSTOMER:

            if _BALANCE_WORDS.search(text):
                return MessageIntent.CUSTOMER_BALANCE_QUERY, 1.0, "balance query keyword"

            if _SHOP_INFO_WORDS.search(text):
                return MessageIntent.GENERAL_SHOP_INFO, 1.0, "shop info keyword"

            if _ORDER_WORDS.search(text):
                return MessageIntent.CUSTOMER_ORDER, 1.0, "order keyword"

            if _PRODUCT_QUERY_WORDS.search(text):
                return MessageIntent.CUSTOMER_PRODUCT_QUERY, 1.0, "product query keyword"

        # ------------------------------------------------------------------
        # Universal intents (any role)
        # ------------------------------------------------------------------
        if _SHOP_INFO_WORDS.search(text):
            return MessageIntent.GENERAL_SHOP_INFO, 1.0, "shop info keyword"

        # Product queries accessible to everyone
        if _PRODUCT_QUERY_WORDS.search(text):
            return MessageIntent.CUSTOMER_PRODUCT_QUERY, 1.0, "product query keyword"

        # Owner with ledger keyword but UNKNOWN role (e.g. first-time message)
        if ledger_kw in ("DEBIT", "CREDIT"):
            return MessageIntent.OWNER_LEDGER, 0.7, "ledger keyword but role unknown"

        return MessageIntent.UNKNOWN, 0.5, "no matching rule"


# ---------------------------------------------------------------------------
# RouterService (public API)
# ---------------------------------------------------------------------------

# Intents that never require authentication (public shop info)
_PUBLIC_INTENTS = {MessageIntent.GENERAL_SHOP_INFO, MessageIntent.CUSTOMER_PRODUCT_QUERY}

# Suggested tools per intent (hints for the agent)
_INTENT_TOOL_MAP: dict[MessageIntent, str] = {
    MessageIntent.OWNER_LEDGER: "add_ledger_entry",
    MessageIntent.OWNER_STOCK: "check_stock",
    MessageIntent.OWNER_ORDER: "create_order",
    MessageIntent.OWNER_REPORT: "daily_summary",
    MessageIntent.OWNER_CUSTOMER: "find_customer",
    MessageIntent.CUSTOMER_BALANCE_QUERY: "get_balance",
    MessageIntent.CUSTOMER_ORDER: "create_order",
    MessageIntent.CUSTOMER_PRODUCT_QUERY: "check_stock",
}


class RouterService:
    """Resolve sender role and classify message intent.

    Usage::

        router = RouterService(db_session)
        result = await router.route(context)
        # result.sender_role == SenderRole.OWNER
        # result.intent == MessageIntent.OWNER_LEDGER
    """

    def __init__(self, session: AsyncSession) -> None:
        self._resolver = RoleResolver(session)
        self._classifier = IntentClassifier()

    async def route(self, ctx: RouterContext) -> RoutedMessage:
        """Resolve role + classify intent for one inbound message.

        This method is the single entry point used by the agent pipeline.
        It never raises — on any unexpected error it returns HUMAN_HANDOFF.
        """
        try:
            shop_id = uuid.UUID(ctx.shop_id) if ctx.shop_id else None
            role = await self._resolver.resolve(ctx.sender_phone, shop_id)
            intent, confidence, reason = self._classifier.classify(ctx, role)

            result = RoutedMessage(
                sender_role=role,
                intent=intent,
                confidence=confidence,
                requires_auth=intent not in _PUBLIC_INTENTS,
                suggested_tool=_INTENT_TOOL_MAP.get(intent),
                reason=reason,
            )

            logger.info(
                "Message routed.",
                extra={
                    "sender_phone": ctx.sender_phone,
                    "role": role.value,
                    "intent": intent.value,
                    "confidence": confidence,
                },
            )
            return result

        except Exception as exc:  # pragma: no cover
            logger.error(
                "RouterService error — returning HUMAN_HANDOFF.",
                extra={"error": str(exc)},
            )
            return RoutedMessage(
                sender_role=SenderRole.UNKNOWN,
                intent=MessageIntent.HUMAN_HANDOFF,
                confidence=0.0,
                requires_auth=True,
                reason=f"routing error: {exc}",
            )
