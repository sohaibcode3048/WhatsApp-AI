"""Pydantic schemas for ledger transactions and balance accounting."""

from datetime import datetime
from decimal import Decimal
import uuid
from typing import Optional
from pydantic import BaseModel, Field

from backend.app.models.ledger import LedgerType, ReferenceType


class LedgerEntryCreate(BaseModel):
    """Schema for creating a raw ledger entry."""
    customer_id: uuid.UUID
    type: LedgerType
    amount: Decimal = Field(gt=Decimal("0.00"), description="Transaction amount must be strictly greater than 0")
    note: Optional[str] = Field(default=None, max_length=500)
    reference_type: ReferenceType = Field(default=ReferenceType.MANUAL_ADJUSTMENT)
    reference_id: Optional[uuid.UUID] = None


class PaymentRecordCreate(BaseModel):
    """Schema for recording a customer payment (Credit)."""
    customer_id: uuid.UUID
    amount: Decimal = Field(gt=Decimal("0.00"), description="Payment amount must be strictly greater than 0")
    note: Optional[str] = Field(default="Payment received", max_length=500)
    reference_id: Optional[uuid.UUID] = None


class CustomerBalanceResponse(BaseModel):
    """Calculated financial balance summary for a customer."""
    customer_id: uuid.UUID
    customer_name: str
    total_debit: Decimal = Field(description="Total goods taken on credit (udhaar)")
    total_credit: Decimal = Field(description="Total payments received (jama)")
    current_balance: Decimal = Field(description="Current balance due: total_debit - total_credit")
    currency: str = "PKR"


class LedgerEntryResponse(BaseModel):
    """Serialized ledger record."""
    id: uuid.UUID
    shop_id: uuid.UUID
    customer_id: uuid.UUID
    type: LedgerType
    amount: Decimal
    note: Optional[str]
    reference_type: ReferenceType
    reference_id: Optional[uuid.UUID]
    created_at: datetime
