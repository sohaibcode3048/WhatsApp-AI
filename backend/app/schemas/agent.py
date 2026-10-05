"""Agent activity and observability schemas."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    action: str
    entity_type: Optional[str]
    entity_id: Optional[uuid.UUID]
    tool_name: Optional[str]
    approval_status: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class ApprovalResponse(BaseModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    requested_by: str
    approved_by: Optional[str]
    action: str
    payload: Dict[str, Any]
    status: str
    created_at: datetime
    approved_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ApprovalActionRequest(BaseModel):
    """Body for approve / reject endpoints."""
    approved_by: str
    note: Optional[str] = None


class MessageResponse(BaseModel):
    id: uuid.UUID
    shop_id: Optional[uuid.UUID]
    whatsapp_message_id: str
    sender_phone: str
    sender_type: str
    message_type: str
    raw_message: str
    intent: Optional[str]
    processing_status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedResponse(BaseModel):
    """Generic paginated list wrapper."""
    items: List[Any]
    total: int
    page: int
    page_size: int
    pages: int
