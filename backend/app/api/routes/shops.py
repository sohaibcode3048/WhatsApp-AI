"""Shop management routes — admin/dashboard API."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import get_current_user, require_owner
from backend.app.core.database import get_db_session
from backend.app.repositories.shop_repository import ShopRepository
from backend.app.schemas.auth import CurrentUser
from backend.app.schemas.shop import ShopCreate, ShopResponse, ShopUpdate

router = APIRouter(prefix="/shops", tags=["Shops"])


@router.get(
    "/{shop_id}",
    response_model=ShopResponse,
    summary="Get Shop Details",
)
async def get_shop(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ShopResponse:
    """Fetch shop details. Users can only access their own shop."""
    import uuid
    try:
        sid = uuid.UUID(shop_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid shop_id format.")

    if current_user.shop_id != sid:
        raise HTTPException(status_code=403, detail="Access denied.")

    repo = ShopRepository(db)
    shop = await repo.get_by_id(sid)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found.")
    return ShopResponse.model_validate(shop)


@router.patch(
    "/{shop_id}",
    response_model=ShopResponse,
    summary="Update Shop Settings",
)
async def update_shop(
    shop_id: str,
    body: ShopUpdate,
    current_user: CurrentUser = Depends(require_owner),
    db: AsyncSession = Depends(get_db_session),
) -> ShopResponse:
    """Update shop details. Owner only."""
    import uuid
    sid = uuid.UUID(shop_id)
    if current_user.shop_id != sid:
        raise HTTPException(status_code=403, detail="Access denied.")

    repo = ShopRepository(db)
    shop = await repo.get_by_id(sid)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found.")

    update_data = body.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(shop, field, value)
    await db.commit()
    await db.refresh(shop)
    return ShopResponse.model_validate(shop)
