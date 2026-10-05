"""Shop and user management schemas for the admin/dashboard API."""

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Shop schemas
# ---------------------------------------------------------------------------


class ShopCreate(BaseModel):
    name: str
    phone_number: str
    address: Optional[str] = None
    timezone: str = "Asia/Karachi"
    default_language: str = "roman_urdu"
    currency: str = "PKR"
    settings: Dict[str, Any] = {}


class ShopUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    timezone: Optional[str] = None
    default_language: Optional[str] = None
    currency: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class ShopResponse(BaseModel):
    id: uuid.UUID
    name: str
    phone_number: str
    address: Optional[str]
    timezone: str
    default_language: str
    currency: str
    settings: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# User schemas
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    name: str
    phone_number: str
    role: str = "STAFF"
    password: Optional[str] = None   # hashed before storing


class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    name: str
    phone_number: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Customer admin schemas
# ---------------------------------------------------------------------------


class CustomerCreate(BaseModel):
    name: str
    phone_number: str
    notes: Optional[str] = None


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    phone_number: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class CustomerResponse(BaseModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    name: str
    phone_number: str
    notes: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Product admin schemas
# ---------------------------------------------------------------------------


class ProductCreate(BaseModel):
    name: str
    price: float
    cost_price: Optional[float] = None
    current_stock: int = 0
    low_stock_threshold: int = 5
    sku: Optional[str] = None
    description: Optional[str] = None


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    cost_price: Optional[float] = None
    current_stock: Optional[int] = None
    low_stock_threshold: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class ProductResponse(BaseModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    name: str
    sku: Optional[str]
    description: Optional[str]
    price: float
    cost_price: Optional[float]
    current_stock: int
    low_stock_threshold: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
