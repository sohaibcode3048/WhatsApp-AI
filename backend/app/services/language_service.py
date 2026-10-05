"""Language detection and message normalization service — MODULE 08.

Architecture
------------
  LanguageDetector   — regex heuristics, no external dependency
  NumberNormalizer   — converts Urdu/Roman Urdu number words → Decimal
  KeywordNormalizer  — maps credit/debit/payment vocabulary → LedgerKeyword
  LanguageService    — orchestrates all three, returns NormalizedMessage

Design principles
-----------------
* No LLM calls.  Pure synchronous, no I/O — safe anywhere.
* Both raw_text and normalized_text are preserved.
* Normalization is conservative: ambiguous → leave as-is.
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Tuple

from backend.app.core.logging import logger
from backend.app.schemas.language import (
    ExtractedAmount,
    LanguageCode,
    LedgerKeyword,
    NormalizedMessage,
)

# ---------------------------------------------------------------------------
# Unicode helpers
# ---------------------------------------------------------------------------

_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_URDU_SCRIPT_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]"
)

# ---------------------------------------------------------------------------
# LanguageDetector
# ---------------------------------------------------------------------------

# Distinctive Roman Urdu words — NOT common English words
_ROMAN_URDU_KEYWORDS = {
    "udhar", "udhaar", "khata", "khate", "likh", "likhdo", "jama", "wasool",
    "ada", "mila", "milgaye", "hazar", "haazar", "lakh", "arab",
    "sau", "haan", "nahi", "nahin", "bhai", "yaar", "karo", "kiya",
    "kijiye", "chahiye", "rakho", "dena", "lena", "milna", "bhejo",
    "kal", "aaj", "abhi", "baad", "pehle", "paise", "rupay",
    "baqi", "bakaya",
}

_ENGLISH_ONLY_RE = re.compile(
    r"\b(the|is|are|was|were|have|has|had|will|would|could|should|"
    r"please|thanks|hello|hi|dear|sir|madam|regarding|payment|invoice|"
    r"receipt|amount|total|balance|outstanding)\b",
    re.IGNORECASE,
)


class LanguageDetector:
    """Classify message language via script analysis + keyword matching."""

    def detect(self, text: str) -> LanguageCode:
        if not text or not text.strip():
            return LanguageCode.UNKNOWN

        has_urdu_script = bool(_URDU_SCRIPT_RE.search(text))
        lower = text.lower()
        tokens = set(re.findall(r"\b\w+\b", lower))
        has_roman_urdu = bool(tokens & _ROMAN_URDU_KEYWORDS)
        has_english = bool(_ENGLISH_ONLY_RE.search(text))
        has_latin = bool(re.search(r"[a-zA-Z]", text))

        if has_urdu_script and has_latin:
            return LanguageCode.MIXED
        if has_urdu_script:
            return LanguageCode.URDU
        if has_roman_urdu:
            if has_english:
                return LanguageCode.MIXED
            return LanguageCode.ROMAN_URDU
        if has_english or has_latin:
            return LanguageCode.ENGLISH
        return LanguageCode.UNKNOWN


# ---------------------------------------------------------------------------
# NumberNormalizer
# ---------------------------------------------------------------------------

_MULTIPLIER_VALUES: dict = {
    "arab": Decimal("1000000000"), "ارب": Decimal("1000000000"),
    "crore": Decimal("10000000"), "kror": Decimal("10000000"),
    "کروڑ": Decimal("10000000"),
    "lakh": Decimal("100000"), "lac": Decimal("100000"),
    "لاکھ": Decimal("100000"),
    "hazar": Decimal("1000"), "haazar": Decimal("1000"),
    "ہزار": Decimal("1000"),
    "sau": Decimal("100"), "سو": Decimal("100"),
}

_URDU_WORD_DIGITS: dict = {
    "do": Decimal("2"), "دو": Decimal("2"),
    "teen": Decimal("3"), "تین": Decimal("3"),
    "char": Decimal("4"), "چار": Decimal("4"),
    "paanch": Decimal("5"), "پانچ": Decimal("5"),
    "chhe": Decimal("6"), "چھ": Decimal("6"),
    "saat": Decimal("7"), "سات": Decimal("7"),
    "aath": Decimal("8"), "آٹھ": Decimal("8"),
    "nau": Decimal("9"), "نو": Decimal("9"),
    "das": Decimal("10"), "دس": Decimal("10"),
    "ek": Decimal("1"), "ایک": Decimal("1"),
}


def _parse_digit_string(s: str) -> Optional[Decimal]:
    s = s.translate(_ARABIC_INDIC_DIGITS).replace(",", "").replace("،", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


class NumberNormalizer:
    """Extract and normalise monetary amounts from mixed-language text."""

    _AMOUNT_PATTERN = re.compile(
        r"""
        (?:
            # Pattern D: leading fraction + multiplier  (aadha hazar, dedh hazar)
            (?P<lead_frac>aadha|adha|آدھا|dedh|ڈیڑھ|sava|سوا|paav|پاؤ)\s+
            (?P<mult_d>arab|ارب|crore|kror|کروڑ|lakh|lac|لاکھ|hazar|haazar|ہزار|sau|سو)
        |
            # Pattern A: digit(s) + optional multiplier
            (?P<digits>[١-٩٠\d][١-٩٠\d,،\.]*)\s*
            (?P<mult_a>arab|ارب|crore|kror|کروڑ|lakh|lac|لاکھ|hazar|haazar|ہزار|sau|سو)?
        |
            # Pattern B: Urdu word digit + multiplier
            (?P<word_digit>do|teen|char|paanch|chhe|saat|aath|nau|das|ek|
                           دو|تین|چار|پانچ|چھ|سات|آٹھ|نو|دس|ایک)\s+
            (?P<mult_b>arab|ارب|crore|kror|کروڑ|lakh|lac|لاکھ|hazar|haazar|ہزار|sau|سو)
        |
            # Pattern C: standalone multiplier
            (?P<bare_mult>arab|ارب|crore|kror|کروڑ|lakh|lac|لاکھ|hazar|haazar|ہزار|sau|سو)
        )
        # Optional trailing fraction suffix
        (?:\s+(?:aur\s+)?(?P<fraction>aadha|adha|آدھا|paav|پاؤ|sava|سوا|dedh|ڈیڑھ))?
        """,
        re.VERBOSE | re.IGNORECASE,
    )

    _FRACTION_VALUES: dict = {
        "aadha": Decimal("0.5"), "adha": Decimal("0.5"), "آدھا": Decimal("0.5"),
        "paav": Decimal("0.25"), "پاؤ": Decimal("0.25"),
        "sava": Decimal("0.25"), "سوا": Decimal("0.25"),
        "dedh": Decimal("0.5"), "ڈیڑھ": Decimal("0.5"),
    }

    def normalize(self, text: str) -> Tuple[str, List[ExtractedAmount]]:
        amounts: List[ExtractedAmount] = []
        result_text = text

        for m in self._AMOUNT_PATTERN.finditer(text):
            raw_token = m.group(0).strip()
            if not raw_token:
                continue
            value = self._compute_value(m)
            if value is None or value <= 0:
                continue
            amounts.append(ExtractedAmount(raw_token=raw_token, value=value))
            digit_str = (
                str(int(value)) if value == value.to_integral_value() else str(value)
            )
            result_text = result_text.replace(raw_token, digit_str, 1)

        return result_text, amounts

    def _compute_value(self, m: re.Match) -> Optional[Decimal]:
        # Pattern D: leading fraction + multiplier
        if m.group("lead_frac"):
            frac_key = m.group("lead_frac").lower()
            mult_key = (m.group("mult_d") or "").lower()
            mult = _MULTIPLIER_VALUES.get(mult_key, Decimal("1"))
            if frac_key in ("dedh", "ڈیڑھ"):
                return Decimal("1.5") * mult
            frac = self._FRACTION_VALUES.get(frac_key, Decimal("0"))
            return frac * mult

        # Base value
        base: Optional[Decimal] = None
        if m.group("digits"):
            base = _parse_digit_string(m.group("digits"))
        elif m.group("word_digit"):
            base = _URDU_WORD_DIGITS.get(m.group("word_digit").lower())

        mult_key = (
            m.group("mult_a") or m.group("mult_b") or m.group("bare_mult") or ""
        ).lower()

        if mult_key:
            mult = _MULTIPLIER_VALUES.get(mult_key)
            if mult:
                base = (base if base is not None else Decimal("1")) * mult

        if base is None:
            return None

        # Trailing fraction suffix
        frac_key = (m.group("fraction") or "").lower()
        if frac_key:
            frac = self._FRACTION_VALUES.get(frac_key, Decimal("0"))
            mult_val = _MULTIPLIER_VALUES.get(mult_key, Decimal("1"))
            base = base + frac * mult_val

        return base


# ---------------------------------------------------------------------------
# KeywordNormalizer
# ---------------------------------------------------------------------------

_DEBIT_PATTERNS = re.compile(
    r"\b("
    r"udhar|udhaar|ادھار|"
    r"khata|khate|کھاتہ|"
    r"likh\s*do|لکھ\s*دو|likh\s*dena|"
    r"credit\s*karo|credit\s*kar|"
    r"baaki|باقی|bakaya|بقایا|"
    r"arz|قرض|"
    r"dena\s*hai|دینا\s*ہے"
    r")\b",
    re.IGNORECASE,
)

_CREDIT_PATTERNS = re.compile(
    r"\b("
    r"jama|جمع|"
    r"wasool|وصول|"
    r"ada\s*kiya|ادا\s*کیا|ada\s*kar|ادا\s*کر|"
    r"pay\s*kiya|paid|credit|"
    r"mila|mil\s*gaye|مل\s*گئے|"
    r"payment\s*aya|payment\s*received|"
    r"wapas|واپس|"
    r"return"
    r")\b",
    re.IGNORECASE,
)


class KeywordNormalizer:
    """Scan text for ledger-direction vocabulary."""

    def classify(self, text: str) -> LedgerKeyword:
        has_debit = bool(_DEBIT_PATTERNS.search(text))
        has_credit = bool(_CREDIT_PATTERNS.search(text))
        if has_debit:
            return LedgerKeyword.DEBIT
        if has_credit:
            return LedgerKeyword.CREDIT
        return LedgerKeyword.NONE


# ---------------------------------------------------------------------------
# LanguageService
# ---------------------------------------------------------------------------

_NAME_CANDIDATE_RE = re.compile(
    r"(?<!\.\s)(?<!\?\s)(?<!\!\s)\b([A-Z][a-z]{2,})\b"
    r"|"
    r"\b([A-Za-z\u0600-\u06FF]{2,})\s+(?:ka|ki|ke|ne|ko|se)\b",
    re.UNICODE,
)

_NON_NAME_WORDS = {
    "add", "ok", "yes", "no", "please", "thanks", "hi",
    "hello", "sir", "dear", "ji", "haan", "nahi",
}


class LanguageService:
    """Orchestrate language detection + number + keyword normalisation."""

    def __init__(self) -> None:
        self._detector = LanguageDetector()
        self._numbers = NumberNormalizer()
        self._keywords = KeywordNormalizer()

    def normalize(self, raw_text: str) -> NormalizedMessage:
        if not raw_text or not raw_text.strip():
            return NormalizedMessage(
                raw_text=raw_text,
                normalized_text=raw_text,
                language=LanguageCode.UNKNOWN,
            )

        text = unicodedata.normalize("NFC", raw_text)
        text = text.translate(_ARABIC_INDIC_DIGITS)

        language = self._detector.detect(raw_text)
        normalized_text, amounts = self._numbers.normalize(text)
        ledger_keyword = self._keywords.classify(raw_text)
        primary_amount = amounts[0].value if amounts else None
        name_candidates = self._extract_name_candidates(normalized_text)
        tokens = normalized_text.split()

        logger.debug(
            "Message normalised.",
            extra={
                "language": language.value,
                "ledger_keyword": ledger_keyword.value,
                "amounts": len(amounts),
                "primary_amount": str(primary_amount) if primary_amount else None,
            },
        )

        return NormalizedMessage(
            raw_text=raw_text,
            normalized_text=normalized_text,
            language=language,
            ledger_keyword=ledger_keyword,
            amounts=amounts,
            primary_amount=primary_amount,
            name_candidates=name_candidates,
            tokens=tokens,
        )

    def _extract_name_candidates(self, text: str) -> List[str]:
        seen: set = set()
        results: List[str] = []
        for m in _NAME_CANDIDATE_RE.finditer(text):
            candidate = (m.group(1) or m.group(2) or "").strip()
            if candidate and candidate.lower() not in seen:
                if candidate.lower() not in _NON_NAME_WORDS:
                    seen.add(candidate.lower())
                    results.append(candidate)
        return results
