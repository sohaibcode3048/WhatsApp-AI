"""Ledger and payment routes — admin/dashboard API."""

from typing import List
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import get_current_user, require_staff
from backend.app.core.database import get_db_session
from backend.app.repositories.ledger_repository import LedgerRepository
from backend.app.schemas.auth import CurrentUser
from backend.app.schemas.ledger import (
    CustomerBalanceResponse,
    LedgerEntryCreate,
    LedgerEntryResponse,
    PaymentRecordCreate,
)

router = APIRouter(prefix="/ledger", tags=["Ledger"])


@router.get("", summary="List Ledger Entries")
async def list_ledger(
    customer_id: uuid.UUID = Query(..., description="Customer UUID to fetch entries for"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, le=200),
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
):
    """Return paginated ledger entries for a customer."""
    repo = LedgerRepository(db)
    entries = await repo.get_customer_statement(
        customer_id=customer_id,
        shop_id=current_user.shop_id,
        skip=skip,
        limit=limit,
    )
    return [_entry_to_dict(e) for e in entries]


@router.get("/balance", response_model=CustomerBalanceResponse, summary="Get Customer Balance")
async def get_balance(
    customer_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
) -> CustomerBalanceResponse:
    """Derive and return the current balance for a customer."""
    repo = LedgerRepository(db)
    try:
        return await repo.get_customer_balance(customer_id, current_user.shop_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/entry", summary="Add Ledger Entry")
async def add_entry(
    body: LedgerEntryCreate,
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
):
    """Manually add a ledger entry (DEBIT or CREDIT)."""
    repo = LedgerRepository(db)
    try:
        entry = await repo.add_entry(shop_id=current_user.shop_id, entry_in=body)
        await db.commit()
        return _entry_to_dict(entry)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/payment", summary="Record Payment")
async def record_payment(
    body: PaymentRecordCreate,
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
):
    """Record a payment received from a customer (CREDIT entry)."""
    repo = LedgerRepository(db)
    try:
        entry = await repo.record_payment(shop_id=current_user.shop_id, payment_in=body)
        await db.commit()
        return _entry_to_dict(entry)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _entry_to_dict(entry) -> dict:
    return {
        "id": str(entry.id),
        "shop_id": str(entry.shop_id),
        "customer_id": str(entry.customer_id),
        "type": entry.type.value,
        "amount": str(entry.amount),
        "note": entry.note,
        "reference_type": entry.reference_type.value if entry.reference_type else None,
        "reference_id": str(entry.reference_id) if entry.reference_id else None,
        "created_at": entry.created_at.isoformat(),
    }
