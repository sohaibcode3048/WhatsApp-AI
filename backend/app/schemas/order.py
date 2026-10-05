"""Pydantic schemas for order placement and validation."""

from decimal import Decimal
import uuid
from typing import List, Optional
from pydantic import BaseModel, Field


class OrderItemCreate(BaseModel):
    """Line item request for placing an order."""
    product_id: uuid.UUID
    quantity: int = Field(gt=0, description="Quantity to purchase must be greater than zero")
    unit_price: Optional[Decimal] = Field(default=None, description="Optional override price; if omitted, product catalog price is used")


class OrderCreate(BaseModel):
    """Schema for creating an order."""
    customer_id: Optional[uuid.UUID] = None
    items: List[OrderItemCreate] = Field(min_length=1, description="Order must contain at least one item")
    notes: Optional[str] = None


class OrderItemResponse(BaseModel):
    product_id: uuid.UUID
    product_name: Optional[str] = None
    quantity: int
    unit_price: Decimal
    subtotal: Decimal

    model_config = {"from_attributes": True}


class OrderResponse(BaseModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    customer_id: Optional[uuid.UUID] = None
    status: str
    payment_status: str
    total_amount: Decimal
    notes: Optional[str] = None
    created_at: Optional[str] = None
    items: List[OrderItemResponse] = []

    model_config = {"from_attributes": True}

