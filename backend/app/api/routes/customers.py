"""Customer management routes — admin/dashboard API."""

from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import get_current_user, require_staff
from backend.app.core.database import get_db_session
from backend.app.repositories.customer_repository import CustomerRepository
from backend.app.repositories.ledger_repository import LedgerRepository
from backend.app.schemas.auth import CurrentUser
from backend.app.schemas.ledger import CustomerBalanceResponse
from backend.app.schemas.shop import CustomerCreate, CustomerResponse, CustomerUpdate

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.get("", response_model=List[CustomerResponse], summary="List Customers")
async def list_customers(
    name: Optional[str] = Query(None, description="Filter by name substring"),
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
) -> List[CustomerResponse]:
    """List all active customers for the authenticated shop."""
    repo = CustomerRepository(db)
    if name:
        customers = await repo.find_by_name(name, current_user.shop_id)
    else:
        customers = await repo.list_by_tenant(current_user.shop_id)
    return [CustomerResponse.model_validate(c) for c in customers]


@router.post(
    "",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Customer",
)
async def create_customer(
    body: CustomerCreate,
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
) -> CustomerResponse:
    """Register a new customer in the shop."""
    repo = CustomerRepository(db)
    existing = await repo.get_by_phone(body.phone_number, current_user.shop_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Customer with phone '{body.phone_number}' already exists.",
        )
    customer = await repo.create(
        shop_id=current_user.shop_id,
        name=body.name,
        phone_number=body.phone_number,
        notes=body.notes,
    )
    await db.commit()
    await db.refresh(customer)
    return CustomerResponse.model_validate(customer)


@router.get("/{customer_id}", response_model=CustomerResponse, summary="Get Customer")
async def get_customer(
    customer_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
) -> CustomerResponse:
    """Fetch one customer by UUID."""
    repo = CustomerRepository(db)
    customer = await repo.get_by_id_tenant(customer_id, current_user.shop_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return CustomerResponse.model_validate(customer)


@router.patch("/{customer_id}", response_model=CustomerResponse, summary="Update Customer")
async def update_customer(
    customer_id: uuid.UUID,
    body: CustomerUpdate,
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
) -> CustomerResponse:
    """Update customer details."""
    repo = CustomerRepository(db)
    customer = await repo.get_by_id_tenant(customer_id, current_user.shop_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(customer, field, value)
    await db.commit()
    await db.refresh(customer)
    return CustomerResponse.model_validate(customer)


@router.get(
    "/{customer_id}/balance",
    response_model=CustomerBalanceResponse,
    summary="Get Customer Balance",
)
async def get_balance(
    customer_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
) -> CustomerBalanceResponse:
    """Return the current outstanding balance for a customer."""
    ledger_repo = LedgerRepository(db)
    try:
        return await ledger_repo.get_customer_balance(customer_id, current_user.shop_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))
