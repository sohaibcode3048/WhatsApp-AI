"""Harness Validation Utilities.

Provides reusable, composable validators that BaseTool subclasses call in
their ``_check_business_rules()`` hooks.  All functions raise typed
MunshiException subclasses so the pipeline can return safe, user-friendly
messages.
"""

from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import UUID

from backend.app.core.exceptions import ValidationError


def require_positive_amount(amount: Any, field_name: str = "amount") -> Decimal:
    """Validate that ``amount`` is a positive Decimal.

    Accepts str, int, float, or Decimal.  Raises ValidationError if
    the value cannot be parsed or is not > 0.
    """
    try:
        dec = Decimal(str(amount))
    except (InvalidOperation, TypeError) as exc:
        raise ValidationError(
            f"{field_name} must be a valid number. Received: {amount!r}",
            details={field_name: str(amount)},
        ) from exc

    if dec <= Decimal("0"):
        raise ValidationError(
            f"{field_name} must be greater than zero. Received: {dec}",
            details={field_name: str(dec)},
        )
    return dec


def require_non_empty_string(value: Optional[str], field_name: str = "value") -> str:
    """Validate that a string field is present and non-empty."""
    if not value or not value.strip():
        raise ValidationError(
            f"{field_name} cannot be blank.",
            details={field_name: value},
        )
    return value.strip()


def require_valid_uuid(value: Any, field_name: str = "id") -> UUID:
    """Validate that ``value`` is a valid UUID (string or UUID object)."""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError) as exc:
        raise ValidationError(
            f"{field_name} is not a valid UUID. Received: {value!r}",
            details={field_name: str(value)},
        ) from exc


def require_max_amount(
    amount: Decimal,
    max_value: Decimal,
    field_name: str = "amount",
) -> None:
    """Validate that ``amount`` does not exceed ``max_value``."""
    if amount > max_value:
        raise ValidationError(
            f"{field_name} exceeds the maximum allowed value of {max_value}. "
            f"Received: {amount}",
            details={field_name: str(amount), "max": str(max_value)},
        )


def require_non_negative_integer(value: Any, field_name: str = "quantity") -> int:
    """Validate that ``value`` is a non-negative integer."""
    try:
        int_val = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"{field_name} must be an integer. Received: {value!r}",
            details={field_name: str(value)},
        ) from exc

    if int_val < 0:
        raise ValidationError(
            f"{field_name} cannot be negative. Received: {int_val}",
            details={field_name: int_val},
        )
    return int_val
