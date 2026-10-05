"""Product management routes — admin/dashboard API."""

from decimal import Decimal
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import get_current_user, require_owner, require_staff
from backend.app.core.database import get_db_session
from backend.app.repositories.product_repository import ProductRepository
from backend.app.schemas.auth import CurrentUser
from backend.app.schemas.shop import ProductCreate, ProductResponse, ProductUpdate

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("", response_model=List[ProductResponse], summary="List Products")
async def list_products(
    name: Optional[str] = Query(None, description="Filter by name substring"),
    low_stock_only: bool = Query(False, description="Return only low-stock products"),
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
) -> List[ProductResponse]:
    """List all active products for the authenticated shop."""
    repo = ProductRepository(db)
    if low_stock_only:
        products = await repo.get_low_stock_products(current_user.shop_id)
    elif name:
        products = await repo.find_by_name(name, current_user.shop_id)
    else:
        products = await repo.list_by_tenant(current_user.shop_id)
    return [ProductResponse.model_validate(p) for p in products]


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add Product",
)
async def create_product(
    body: ProductCreate,
    current_user: CurrentUser = Depends(require_owner),
    db: AsyncSession = Depends(get_db_session),
) -> ProductResponse:
    """Add a new product to the catalog. Owner only."""
    repo = ProductRepository(db)
    if body.sku:
        existing = await repo.get_by_sku(body.sku, current_user.shop_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Product with SKU '{body.sku}' already exists.",
            )
    product = await repo.create(
        shop_id=current_user.shop_id,
        name=body.name,
        price=Decimal(str(body.price)),
        cost_price=Decimal(str(body.cost_price)) if body.cost_price else None,
        current_stock=body.current_stock,
        low_stock_threshold=body.low_stock_threshold,
        sku=body.sku,
        description=body.description,
    )
    await db.commit()
    await db.refresh(product)
    return ProductResponse.model_validate(product)


@router.get("/{product_id}", response_model=ProductResponse, summary="Get Product")
async def get_product(
    product_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
) -> ProductResponse:
    """Fetch one product by UUID."""
    repo = ProductRepository(db)
    product = await repo.get_by_id_tenant(product_id, current_user.shop_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    return ProductResponse.model_validate(product)


@router.patch("/{product_id}", response_model=ProductResponse, summary="Update Product")
async def update_product(
    product_id: uuid.UUID,
    body: ProductUpdate,
    current_user: CurrentUser = Depends(require_owner),
    db: AsyncSession = Depends(get_db_session),
) -> ProductResponse:
    """Update product fields. Owner only."""
    repo = ProductRepository(db)
    product = await repo.get_by_id_tenant(product_id, current_user.shop_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    update_data = body.model_dump(exclude_none=True)
    for field, value in update_data.items():
        if field in ("price", "cost_price") and value is not None:
            setattr(product, field, Decimal(str(value)))
        else:
            setattr(product, field, value)

    await db.commit()
    await db.refresh(product)
    return ProductResponse.model_validate(product)
