"""Schemas for message routing — MODULE 09.

RouterContext  — everything the RouterService knows about one inbound message.
RoutedMessage  — the output of routing: who sent it, what they want, next action.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel


class MessageIntent(str, Enum):
    """Classified intent of an inbound message.

    Owner intents
    -------------
    OWNER_LEDGER        — record udhaar / payment (financial mutation)
    OWNER_STOCK         — check or update inventory
    OWNER_ORDER         — create / view / cancel an order
    OWNER_REPORT        — ask for a daily/weekly summary
    OWNER_CUSTOMER      — look up or manage a customer record
    OWNER_CONFIGURATION — configure shop settings, reminders, etc.

    Customer intents
    ----------------
    CUSTOMER_ORDER          — place or check an order
    CUSTOMER_PRODUCT_QUERY  — ask about product pricing / availability
    CUSTOMER_BALANCE_QUERY  — ask own ledger balance

    General
    -------
    GENERAL_SHOP_INFO — ask about shop hours, address, policies (→ RAG)
    HUMAN_HANDOFF     — agent is uncertain; escalate to owner
    UNKNOWN           — cannot classify; ask for clarification
    """

    OWNER_LEDGER = "OWNER_LEDGER"
    OWNER_STOCK = "OWNER_STOCK"
    OWNER_ORDER = "OWNER_ORDER"
    OWNER_REPORT = "OWNER_REPORT"
    OWNER_CUSTOMER = "OWNER_CUSTOMER"
    OWNER_CONFIGURATION = "OWNER_CONFIGURATION"

    CUSTOMER_ORDER = "CUSTOMER_ORDER"
    CUSTOMER_PRODUCT_QUERY = "CUSTOMER_PRODUCT_QUERY"
    CUSTOMER_BALANCE_QUERY = "CUSTOMER_BALANCE_QUERY"

    GENERAL_SHOP_INFO = "GENERAL_SHOP_INFO"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"
    UNKNOWN = "UNKNOWN"


class SenderRole(str, Enum):
    """Resolved role of the message sender for this tenant."""
    OWNER = "OWNER"
    STAFF = "STAFF"
    CUSTOMER = "CUSTOMER"
    UNKNOWN = "UNKNOWN"


class RouterContext(BaseModel):
    """Input context passed into the RouterService."""

    # Identity
    sender_phone: str
    shop_id: Optional[str] = None       # UUID as str; None if shop not resolved

    # Message content
    raw_text: str
    normalized_text: str                 # from NormalizedMessage.normalized_text
    message_type: str = "text"           # "text" | "audio" | "image" | …

    # Pre-computed from LanguageService
    ledger_keyword: str = "NONE"         # "DEBIT" | "CREDIT" | "NONE"
    has_amount: bool = False
    name_candidates: list[str] = []


class RoutedMessage(BaseModel):
    """Output of the RouterService for one inbound message."""

    sender_role: SenderRole
    intent: MessageIntent
    confidence: float                    # 0.0 – 1.0 (rule-based = 1.0)
    requires_auth: bool = True           # False only for truly public queries
    suggested_tool: Optional[str] = None # hint for the agent (non-binding)
    reason: str = ""                     # brief human-readable explanation
