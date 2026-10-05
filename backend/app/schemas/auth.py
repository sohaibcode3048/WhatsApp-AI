"""Authentication and session schemas for the admin/dashboard API."""

import uuid
from typing import Optional

from pydantic import BaseModel, field_validator


class LoginRequest(BaseModel):
    """Dashboard login — phone + password (or API key for dev)."""

    phone_number: str
    password: str
    shop_id: Optional[uuid.UUID] = None  # required for multi-tenant lookup


class TokenResponse(BaseModel):
    """JWT access token returned on successful login."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int           # seconds
    user_id: uuid.UUID
    shop_id: uuid.UUID
    role: str


class CurrentUser(BaseModel):
    """Decoded claims injected by the auth dependency."""

    user_id: uuid.UUID
    shop_id: uuid.UUID
    phone_number: str
    role: str
    is_active: bool = True
