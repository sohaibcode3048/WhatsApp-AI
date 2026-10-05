"""Reporting Tools — MODULE 07 extension.

Tools:
  daily_summary     — aggregate sales/orders/ledger stats for a given date
  get_sales_summary — period-based sales breakdown
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.ledger import LedgerEntry, LedgerType
from backend.app.models.order import Order, OrderStatus, PaymentStatus
from backend.app.tools.base import (
    OWNER_AND_STAFF,
    BaseTool,
    ToolInput,
    ToolResult,
    tool_registry,
)


class DailySummaryInput(ToolInput):
    report_date: Optional[str] = None   # ISO date string "YYYY-MM-DD"; None = today


class GetSalesSummaryInput(ToolInput):
    start_date: str   # ISO date string "YYYY-MM-DD"
    end_date: str     # ISO date string "YYYY-MM-DD"


# ---------------------------------------------------------------------------
# daily_summary
# ---------------------------------------------------------------------------


class DailySummaryTool(BaseTool):
    name = "daily_summary"
    description = (
        "Generate a business summary for a given date (default today). "
        "Returns total orders, total sales, new debit/credit entries, "
        "and a list of low-stock products."
    )
    allowed_roles = OWNER_AND_STAFF
    input_schema = DailySummaryInput

    def _check_business_rules(self, tool_input: DailySummaryInput) -> None:
        if tool_input.report_date:
            try:
                date.fromisoformat(tool_input.report_date)
            except ValueError as exc:
                from backend.app.core.exceptions import ValidationError
                raise ValidationError(
                    f"report_date must be ISO format YYYY-MM-DD. Got: {tool_input.report_date}",
                    details={"report_date": tool_input.report_date},
                ) from exc

    async def _check_tenant_isolation(
        self, tool_input: DailySummaryInput, session: AsyncSession
    ) -> None:
        pass  # all queries are already scoped to shop_id

    async def _run(
        self, tool_input: DailySummaryInput, session: AsyncSession
    ) -> ToolResult:
        from backend.app.repositories.product_repository import ProductRepository

        target_date = (
            date.fromisoformat(tool_input.report_date)
            if tool_input.report_date
            else datetime.now(timezone.utc).date()
        )

        day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0,
                             tzinfo=timezone.utc)
        day_end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59,
                           tzinfo=timezone.utc)

        shop_id = tool_input.shop_id

        # --- Orders ---
        orders_result = await session.execute(
            select(
                func.count(Order.id).label("total_orders"),
                func.coalesce(func.sum(Order.total_amount), 0).label("total_sales"),
            ).where(
                Order.shop_id == shop_id,
                Order.created_at >= day_start,
                Order.created_at <= day_end,
                Order.status != OrderStatus.CANCELLED,
            )
        )
        orders_row = orders_result.one()
        total_orders = int(orders_row.total_orders)
        total_sales = Decimal(str(orders_row.total_sales))

        # Paid vs unpaid
        paid_result = await session.execute(
            select(func.count(Order.id)).where(
                Order.shop_id == shop_id,
                Order.created_at >= day_start,
                Order.created_at <= day_end,
                Order.payment_status == PaymentStatus.PAID,
            )
        )
        paid_orders = int(paid_result.scalar_one_or_none() or 0)

        # --- Ledger ---
        ledger_result = await session.execute(
            select(
                LedgerEntry.type,
                func.coalesce(func.sum(LedgerEntry.amount), 0).label("total"),
            ).where(
                LedgerEntry.shop_id == shop_id,
                LedgerEntry.created_at >= day_start,
                LedgerEntry.created_at <= day_end,
            ).group_by(LedgerEntry.type)
        )
        debit_total = Decimal("0")
        credit_total = Decimal("0")
        for row in ledger_result:
            if row.type == LedgerType.DEBIT:
                debit_total = Decimal(str(row.total))
            else:
                credit_total = Decimal(str(row.total))

        # --- Low stock ---
        prod_repo = ProductRepository(session)
        low_stock = await prod_repo.get_low_stock_products(shop_id)
        low_stock_list = [
            {"name": p.name, "current_stock": p.current_stock,
             "threshold": p.low_stock_threshold}
            for p in low_stock
        ]

        summary = {
            "date": target_date.isoformat(),
            "total_orders": total_orders,
            "paid_orders": paid_orders,
            "unpaid_orders": total_orders - paid_orders,
            "total_sales": str(total_sales),
            "new_debit": str(debit_total),
            "new_credit": str(credit_total),
            "low_stock_count": len(low_stock_list),
            "low_stock_products": low_stock_list,
        }

        await self._write_audit(
            session,
            tool_input,
            action="DAILY_SUMMARY",
            entity_type="report",
            entity_id=shop_id,
            request_data={"date": target_date.isoformat()},
            result_data={"total_orders": total_orders, "total_sales": str(total_sales)},
        )

        msg_lines = [
            f"📊 Summary for {target_date.isoformat()}",
            f"Orders: {total_orders} (Paid: {paid_orders})",
            f"Sales: Rs. {total_sales}",
            f"New Udhaar: Rs. {debit_total}  |  Payments: Rs. {credit_total}",
        ]
        if low_stock_list:
            msg_lines.append(f"⚠️ {len(low_stock_list)} products low on stock")

        return ToolResult(
            success=True,
            tool_name=self.name,
            data=summary,
            message="\n".join(msg_lines),
        )


# ---------------------------------------------------------------------------
# get_sales_summary
# ---------------------------------------------------------------------------


class GetSalesSummaryTool(BaseTool):
    name = "get_sales_summary"
    description = (
        "Get aggregated sales and order stats for a date range (YYYY-MM-DD). "
        "Returns total orders, total revenue, and payment breakdown."
    )
    allowed_roles = OWNER_AND_STAFF
    input_schema = GetSalesSummaryInput

    def _check_business_rules(self, tool_input: GetSalesSummaryInput) -> None:
        from backend.app.core.exceptions import ValidationError
        try:
            start = date.fromisoformat(tool_input.start_date)
            end = date.fromisoformat(tool_input.end_date)
        except ValueError as exc:
            raise ValidationError(
                "start_date and end_date must be ISO format YYYY-MM-DD.",
                details={"start_date": tool_input.start_date, "end_date": tool_input.end_date},
            ) from exc
        if start > end:
            raise ValidationError(
                "start_date must be on or before end_date.",
                details={"start_date": tool_input.start_date, "end_date": tool_input.end_date},
            )

    async def _check_tenant_isolation(
        self, tool_input: GetSalesSummaryInput, session: AsyncSession
    ) -> None:
        pass

    async def _run(
        self, tool_input: GetSalesSummaryInput, session: AsyncSession
    ) -> ToolResult:
        start = date.fromisoformat(tool_input.start_date)
        end = date.fromisoformat(tool_input.end_date)
        start_dt = datetime(start.year, start.month, start.day, 0, 0, 0, tzinfo=timezone.utc)
        end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)

        result = await session.execute(
            select(
                func.count(Order.id).label("total_orders"),
                func.coalesce(func.sum(Order.total_amount), 0).label("total_revenue"),
            ).where(
                Order.shop_id == tool_input.shop_id,
                Order.created_at >= start_dt,
                Order.created_at <= end_dt,
                Order.status != OrderStatus.CANCELLED,
            )
        )
        row = result.one()
        total_orders = int(row.total_orders)
        total_revenue = Decimal(str(row.total_revenue))

        return ToolResult(
            success=True,
            tool_name=self.name,
            data={
                "start_date": tool_input.start_date,
                "end_date": tool_input.end_date,
                "total_orders": total_orders,
                "total_revenue": str(total_revenue),
            },
            message=(
                f"From {tool_input.start_date} to {tool_input.end_date}: "
                f"{total_orders} orders, Rs. {total_revenue} revenue."
            ),
        )


tool_registry.register(DailySummaryTool())
tool_registry.register(GetSalesSummaryTool())
