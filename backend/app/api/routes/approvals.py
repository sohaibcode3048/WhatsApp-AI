"""Approval management routes — admin/dashboard API."""

from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import get_current_user, require_owner
from backend.app.core.database import get_db_session
from backend.app.harness.approval import approval_engine
from backend.app.models.approval import Approval
from backend.app.schemas.agent import ApprovalActionRequest, ApprovalResponse
from backend.app.schemas.auth import CurrentUser

router = APIRouter(prefix="/approvals", tags=["Approvals"])


@router.get("", response_model=List[ApprovalResponse], summary="List Approvals")
async def list_approvals(
    approval_status: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, le=200),
    current_user: CurrentUser = Depends(require_owner),
    db: AsyncSession = Depends(get_db_session),
) -> List[ApprovalResponse]:
    """List approval requests for the authenticated shop."""
    query = (
        select(Approval)
        .where(Approval.shop_id == current_user.shop_id)
        .order_by(Approval.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    if approval_status:
        query = query.where(Approval.status == approval_status.upper())
    result = await db.execute(query)
    approvals = result.scalars().all()
    return [ApprovalResponse.model_validate(a) for a in approvals]


@router.get("/{approval_id}", response_model=ApprovalResponse, summary="Get Approval")
async def get_approval(
    approval_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_owner),
    db: AsyncSession = Depends(get_db_session),
) -> ApprovalResponse:
    """Fetch one approval record."""
    result = await db.execute(
        select(Approval).where(
            Approval.id == approval_id,
            Approval.shop_id == current_user.shop_id,
        )
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found.")
    return ApprovalResponse.model_validate(approval)


@router.post("/{approval_id}/approve", response_model=ApprovalResponse, summary="Approve")
async def approve_action(
    approval_id: uuid.UUID,
    body: ApprovalActionRequest,
    current_user: CurrentUser = Depends(require_owner),
    db: AsyncSession = Depends(get_db_session),
) -> ApprovalResponse:
    """Approve a pending high-value operation."""
    success = await approval_engine.approve(
        approval_id=str(approval_id),
        approved_by=body.approved_by or current_user.phone_number,
        session=db,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Approval not found or already processed.")
    await db.commit()

    result = await db.execute(
        select(Approval).where(Approval.id == approval_id)
    )
    approval = result.scalar_one_or_none()
    return ApprovalResponse.model_validate(approval)


@router.post("/{approval_id}/reject", response_model=ApprovalResponse, summary="Reject")
async def reject_action(
    approval_id: uuid.UUID,
    body: ApprovalActionRequest,
    current_user: CurrentUser = Depends(require_owner),
    db: AsyncSession = Depends(get_db_session),
) -> ApprovalResponse:
    """Reject a pending high-value operation."""
    success = await approval_engine.reject(approval_id=str(approval_id))
    if not success:
        # Try DB path
        result_db = await db.execute(
            select(Approval).where(
                Approval.id == approval_id,
                Approval.shop_id == current_user.shop_id,
            )
        )
        approval = result_db.scalar_one_or_none()
        if not approval:
            raise HTTPException(status_code=404, detail="Approval not found.")
        approval.status = "REJECTED"
        await db.commit()
        await db.refresh(approval)
        return ApprovalResponse.model_validate(approval)

    await db.commit()
    result = await db.execute(select(Approval).where(Approval.id == approval_id))
    approval = result.scalar_one_or_none()
    return ApprovalResponse.model_validate(approval)
