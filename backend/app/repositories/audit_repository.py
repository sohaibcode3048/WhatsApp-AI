"""Repository for managing immutable audit trail records."""

import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.audit_log import AuditLog
from backend.app.repositories.base_repository import TenantRepository


class AuditRepository(TenantRepository[AuditLog]):
    """Data access layer for immutable audit trail records."""

    def __init__(self, session: AsyncSession):
        super().__init__(AuditLog, session)

    async def log_action(
        self,
        shop_id: uuid.UUID,
        action: str,
        entity_type: str,
        entity_id: Optional[str] = None,
        user_id: Optional[uuid.UUID] = None,
        message_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        request_data: Optional[Dict[str, Any]] = None,
        result_data: Optional[Dict[str, Any]] = None,
        approval_status: str = "AUTO_APPROVED",
    ) -> AuditLog:
        """Create and flush an audit log record within the current transaction."""
        audit_entry = AuditLog(
            shop_id=shop_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            user_id=user_id,
            message_id=message_id,
            tool_name=tool_name,
            request_data=request_data or {},
            result_data=result_data or {},
            approval_status=approval_status,
        )
        self.session.add(audit_entry)
        await self.session.flush()
        return audit_entry

    async def list_by_tenant(
        self,
        shop_id: uuid.UUID,
        entity_type: Optional[str] = None,
        action: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[AuditLog]:
        """Fetch audit log records scoped strictly to the tenant."""
        query = select(AuditLog).where(AuditLog.shop_id == shop_id)
        if entity_type:
            query = query.where(AuditLog.entity_type == entity_type)
        if action:
            query = query.where(AuditLog.action == action)

        query = query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())
