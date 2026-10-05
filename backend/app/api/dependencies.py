"""FastAPI dependency injection helpers for the admin/dashboard API.

Provides:
  get_current_user  — decode JWT and return CurrentUser
  require_owner     — assert role == OWNER (raises 403 otherwise)
  require_staff     — assert role in (OWNER, STAFF)
"""

import uuid
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import get_db_session
from backend.app.core.security import decode_access_token, get_token_from_header
from backend.app.schemas.auth import CurrentUser


async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
) -> CurrentUser:
    """Extract and validate the JWT access token from the Authorization header.

    Raises HTTP 401 if the token is missing, expired, or invalid.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token = get_token_from_header(authorization)
        payload = decode_access_token(token)
    except (JWTError, ValueError):
        raise credentials_exception

    user_id = payload.get("sub")
    shop_id = payload.get("shop_id")
    role = payload.get("role", "UNKNOWN")
    phone_number = payload.get("phone_number", "")

    if not user_id or not shop_id:
        raise credentials_exception

    return CurrentUser(
        user_id=uuid.UUID(user_id),
        shop_id=uuid.UUID(shop_id),
        phone_number=phone_number,
        role=role,
        is_active=True,
    )


def require_owner(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Raise 403 if the caller is not an OWNER."""
    if current_user.role != "OWNER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required for this operation.",
        )
    return current_user


def require_staff(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Raise 403 if the caller is not OWNER or STAFF."""
    if current_user.role not in ("OWNER", "STAFF"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff or Owner access required for this operation.",
        )
    return current_user
