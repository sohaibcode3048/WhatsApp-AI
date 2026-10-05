"""Order management routes — admin/dashboard API."""

from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import get_current_user, require_staff
from backend.app.core.database import get_db_session
from backend.app.models.order import OrderStatus
from backend.app.repositories.order_repository import OrderRepository
from backend.app.schemas.auth import CurrentUser
from backend.app.schemas.order import OrderCreate, OrderResponse

router = APIRouter(prefix="/orders", tags=["Orders"])


class OrderItemResponse:
    pass


@router.get("", summary="List Orders")
async def list_orders(
    customer_id: Optional[uuid.UUID] = Query(None),
    order_status: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, le=200),
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
):
    """List orders for the authenticated shop with optional filtering."""
    repo = OrderRepository(db)
    status_filter = OrderStatus(order_status) if order_status else None
    orders = await repo.list_by_tenant(
        shop_id=current_user.shop_id,
        customer_id=customer_id,
        status=status_filter,
        skip=skip,
        limit=limit,
    )
    return [_order_to_dict(o) for o in orders]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create Order",
)
async def create_order(
    body: OrderCreate,
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a new order with atomic stock decrement."""
    repo = OrderRepository(db)
    try:
        order = await repo.create_order(shop_id=current_user.shop_id, order_in=body)
        await db.commit()
        return _order_to_dict(order)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/{order_id}", summary="Get Order")
async def get_order(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
):
    """Fetch one order with all line items."""
    repo = OrderRepository(db)
    order = await repo.get_by_id_tenant(order_id, current_user.shop_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    return _order_to_dict(order)


@router.post("/{order_id}/cancel", summary="Cancel Order")
async def cancel_order(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
):
    """Cancel an order and restore stock."""
    repo = OrderRepository(db)
    try:
        order = await repo.cancel_order(order_id, current_user.shop_id)
        await db.commit()
        return _order_to_dict(order)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _order_to_dict(order) -> dict:
    return {
        "id": str(order.id),
        "shop_id": str(order.shop_id),
        "customer_id": str(order.customer_id) if order.customer_id else None,
        "status": order.status.value,
        "payment_status": order.payment_status.value,
        "total_amount": str(order.total_amount),
        "notes": order.notes,
        "created_at": order.created_at.isoformat(),
        "items": [
            {
                "product_id": str(item.product_id),
                "product_name": item.product.name if item.product else None,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price),
                "subtotal": str(item.subtotal),
            }
            for item in (order.items or [])
        ],
    }
