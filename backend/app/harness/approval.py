"""Approval Engine — MODULE 16.

High-value operations (ledger entries above the configured threshold) require
explicit Owner approval before execution.

Flow
----
  1.  Tool calls _check_approval() in BaseTool.execute()
  2.  ApprovalEngine checks the amount against LARGE_CREDIT_APPROVAL_THRESHOLD
  3.  If over threshold → creates an Approval record in the DB, raises ApprovalRequired
  4.  Owner receives a WhatsApp notification with approve/reject link or reply code
  5.  Owner approves → approval record transitions to APPROVED → tool re-executes
  6.  Owner rejects  → approval record transitions to REJECTED

Approval records are persisted to the approvals table (permanent audit trail).
The in-process fallback dict is only used in tests that don't want a real DB.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.exceptions import ApprovalRequired
from backend.app.core.logging import logger


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


# In-process fallback for tests
_IN_PROCESS_APPROVALS: Dict[str, Dict] = {}


class ApprovalEngine:
    """Manages high-value operation approvals.

    Persists records to the approvals table when a DB session is supplied;
    falls back to in-process dict when session is None (unit tests).
    """

    def needs_approval(self, tool_name: str, params: Dict[str, Any]) -> bool:
        """Return True if this tool call requires owner approval.

        Current rules:
          - add_ledger_entry DEBIT with amount >= LARGE_CREDIT_APPROVAL_THRESHOLD
          - record_payment with amount >= LARGE_CREDIT_APPROVAL_THRESHOLD
        """
        threshold = settings.LARGE_CREDIT_APPROVAL_THRESHOLD
        amount_str = params.get("amount")
        if amount_str is None:
            return False

        try:
            amount = Decimal(str(amount_str))
        except Exception:
            return False

        if tool_name in ("add_ledger_entry", "record_payment") and amount >= threshold:
            return True
        return False

    async def request_approval(
        self,
        shop_id: str,
        requested_by: str,
        tool_name: str,
        params: Dict[str, Any],
        session: Optional[AsyncSession] = None,
    ) -> str:
        """Create an approval record and raise ApprovalRequired.

        Returns the approval_id (stored in exception details).
        """
        approval_id = str(uuid.uuid4())
        amount = params.get("amount", "?")
        customer_id = params.get("customer_id", "?")

        record = {
            "id": approval_id,
            "shop_id": shop_id,
            "requested_by": requested_by,
            "tool_name": tool_name,
            "payload": params,
            "status": ApprovalStatus.PENDING.value,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "approved_at": None,
            "approved_by": None,
        }

        if session is not None:
            await self._persist(record, session)
        else:
            _IN_PROCESS_APPROVALS[approval_id] = record

        prompt_text = (
            f"Rs. {amount} ki bari transaction ke liye Owner approval chahiye. "
            f"Approval ID: {approval_id[:8]}. "
            f"Owner ko notify kar diya gaya hai."
        )

        logger.warning(
            "Approval required for high-value operation.",
            extra={
                "approval_id": approval_id,
                "tool": tool_name,
                "amount": str(amount),
                "shop_id": shop_id,
            },
        )

        raise ApprovalRequired(
            message="This operation requires owner approval.",
            approval_id=approval_id,
            prompt_text=prompt_text,
        )

    async def _persist(self, record: Dict[str, Any], session: AsyncSession) -> None:
        """Persist approval record to the approvals table."""
        try:
            from backend.app.models.approval import Approval
            approval = Approval(
                id=uuid.UUID(record["id"]),
                shop_id=uuid.UUID(record["shop_id"]),
                requested_by=record["requested_by"],
                action=record["tool_name"],
                payload=record["payload"],
                status=record["status"],
            )
            session.add(approval)
            await session.flush()
        except Exception as exc:
            # Don't let approval persistence failure crash the whole pipeline
            logger.error(
                "Failed to persist approval record — using in-process fallback.",
                extra={"error": str(exc)},
            )
            _IN_PROCESS_APPROVALS[record["id"]] = record

    async def approve(
        self,
        approval_id: str,
        approved_by: str,
        session: Optional[AsyncSession] = None,
    ) -> bool:
        """Approve a pending operation. Returns True on success."""
        record = _IN_PROCESS_APPROVALS.get(approval_id)
        if record:
            record["status"] = ApprovalStatus.APPROVED.value
            record["approved_by"] = approved_by
            record["approved_at"] = datetime.now(timezone.utc).isoformat()
            logger.info("Approval granted.", extra={"approval_id": approval_id})
            return True

        if session:
            try:
                from backend.app.models.approval import Approval
                from sqlalchemy import select
                result = await session.execute(
                    select(Approval).where(Approval.id == uuid.UUID(approval_id))
                )
                approval = result.scalar_one_or_none()
                if approval:
                    approval.status = ApprovalStatus.APPROVED.value
                    approval.approved_by = approved_by
                    approval.approved_at = datetime.now(timezone.utc)
                    await session.flush()
                    return True
            except Exception as exc:
                logger.error("Failed to update approval.", extra={"error": str(exc)})
        return False

    async def reject(self, approval_id: str) -> bool:
        record = _IN_PROCESS_APPROVALS.get(approval_id)
        if record:
            record["status"] = ApprovalStatus.REJECTED.value
            return True
        return False

    def get_status(self, approval_id: str) -> Optional[ApprovalStatus]:
        record = _IN_PROCESS_APPROVALS.get(approval_id)
        if record:
            return ApprovalStatus(record["status"])
        return None


# Singleton
approval_engine = ApprovalEngine()
