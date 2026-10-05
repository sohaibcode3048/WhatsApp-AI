"""Service layer for recording sanitized audit events."""

import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.logging import sanitize_payload
from backend.app.models.audit_log import AuditLog
from backend.app.repositories.audit_repository import AuditRepository


class AuditService:
    """Provides high-level audit logging with sensitive data redaction."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = AuditRepository(session)

    async def record_event(
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
        """Sanitizes sensitive information and logs the audit event within the active transaction."""
        clean_request = sanitize_payload(request_data) if request_data else {}
        clean_result = sanitize_payload(result_data) if result_data else {}

        return await self.repository.log_action(
            shop_id=shop_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user_id,
            message_id=message_id,
            tool_name=tool_name,
            request_data=clean_request,
            result_data=clean_result,
            approval_status=approval_status,
        )

    async def get_shop_audit_trail(
        self,
        shop_id: uuid.UUID,
        entity_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[AuditLog]:
        """Fetch audit entries for a tenant."""
        return await self.repository.list_by_tenant(
            shop_id=shop_id,
            entity_type=entity_type,
            skip=skip,
            limit=limit,
        )
