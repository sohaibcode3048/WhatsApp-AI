"""Audit log routes — admin/dashboard API."""

from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import get_current_user, require_staff
from backend.app.core.database import get_db_session
from backend.app.repositories.audit_repository import AuditRepository
from backend.app.schemas.agent import AuditLogResponse
from backend.app.schemas.auth import CurrentUser

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])


@router.get("", response_model=List[AuditLogResponse], summary="List Audit Logs")
async def list_audit_logs(
    entity_type: Optional[str] = Query(None, description="Filter by entity_type"),
    action: Optional[str] = Query(None, description="Filter by action"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, le=200),
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
) -> List[AuditLogResponse]:
    """Return paginated audit logs for the authenticated shop."""
    repo = AuditRepository(db)
    logs = await repo.list_by_tenant(
        shop_id=current_user.shop_id,
        entity_type=entity_type,
        action=action,
        skip=skip,
        limit=limit,
    )
    return [AuditLogResponse.model_validate(log) for log in logs]
