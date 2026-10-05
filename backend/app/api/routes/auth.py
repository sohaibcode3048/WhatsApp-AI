"""Authentication routes — dashboard login and token refresh."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import get_current_user
from backend.app.core.database import get_db_session
from backend.app.core.security import create_access_token, verify_password
from backend.app.repositories.user_repository import UserRepository
from backend.app.repositories.shop_repository import ShopRepository
from backend.app.schemas.auth import CurrentUser, LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Dashboard Login",
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Authenticate a user and return a JWT access token.

    Accepts phone_number + password.  The shop_id field narrows lookup when
    the same phone number is registered across multiple tenants.
    """
    user_repo = UserRepository(db)
    user = await user_repo.get_by_phone_and_shop(
        phone_number=body.phone_number,
        shop_id=body.shop_id,  # type: ignore[arg-type]
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        )

    # Verify password (password_hash stored on user model; fallback for users
    # without a hash set up — e.g. WhatsApp-only users — is rejected)
    password_hash = getattr(user, "password_hash", None)
    if not password_hash or not verify_password(body.password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive.",
        )

    from backend.app.core.config import settings

    token = create_access_token(
        subject=str(user.id),
        extra_claims={
            "shop_id": str(user.shop_id),
            "role": user.role.value if hasattr(user.role, "value") else str(user.role),
            "phone_number": user.phone_number,
        },
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user_id=user.id,
        shop_id=user.shop_id,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
    )


@router.get(
    "/me",
    response_model=CurrentUser,
    summary="Get Current Authenticated User",
)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Return the decoded claims of the current JWT token."""
    return current_user
