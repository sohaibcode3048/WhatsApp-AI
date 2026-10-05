"""Schemas for language detection and message normalization — MODULE 08."""

from decimal import Decimal
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class LanguageCode(str, Enum):
    URDU = "ur"
    ROMAN_URDU = "ro"
    ENGLISH = "en"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class LedgerKeyword(str, Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"
    NONE = "NONE"


class ExtractedAmount(BaseModel):
    raw_token: str
    value: Decimal
    currency: Optional[str] = None


class NormalizedMessage(BaseModel):
    raw_text: str
    normalized_text: str
    language: LanguageCode = LanguageCode.UNKNOWN
    ledger_keyword: LedgerKeyword = LedgerKeyword.NONE
    amounts: List[ExtractedAmount] = Field(default_factory=list)
    primary_amount: Optional[Decimal] = None
    name_candidates: List[str] = Field(default_factory=list)
    tokens: List[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}
