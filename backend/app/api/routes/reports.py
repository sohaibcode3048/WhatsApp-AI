"""Reporting routes — admin/dashboard API."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import get_current_user, require_staff
from backend.app.core.database import get_db_session
from backend.app.schemas.auth import CurrentUser

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/daily", summary="Daily Business Summary")
async def daily_summary(
    report_date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD (default: today)"),
    current_user: CurrentUser = Depends(require_staff),
    db: AsyncSession = Depends(get_db_session),
):
    """Return a daily business summary: orders, sales, ledger, low-stock.

    This is the REST equivalent of the ``daily_summary`` agent tool.
    """
    from backend.app.tools.reporting_tools import DailySummaryTool
    from backend.app.schemas.router import SenderRole

    tool = DailySummaryTool()
    raw_input = {
        "shop_id": str(current_user.shop_id),
        "sender_phone": current_user.phone_number,
        "sender_role": SenderRole.OWNER.value,
        "report_date": report_date,
    }
    result = await tool.execute(raw_input, db)
    return result.data
