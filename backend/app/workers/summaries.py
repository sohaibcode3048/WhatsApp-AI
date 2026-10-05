"""Daily Summary Worker — MODULE 21.

Sends a nightly business summary to each shop owner via WhatsApp.
Scheduled by Celery Beat at 23:55 every day.
"""

import asyncio
import logging
from datetime import datetime, timezone

from backend.app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    name="backend.app.workers.summaries.send_daily_summary",
)
def send_daily_summary(self) -> dict:
    """Send nightly summary to all active shop owners.

    For each shop:
      1. Generate daily_summary via the reporting tool.
      2. Format a concise WhatsApp message.
      3. Send to the shop's owner phone number.
    """
    async def _run():
        from sqlalchemy import select
        from backend.app.core.database import async_session_factory
        from backend.app.models.shop import Shop
        from backend.app.schemas.router import SenderRole
        from backend.app.services.whatsapp_service import WhatsAppService
        from backend.app.tools.reporting_tools import DailySummaryTool

        results = {"sent": 0, "failed": 0, "shops": []}
        today = datetime.now(timezone.utc).date().isoformat()
        tool = DailySummaryTool()

        async with async_session_factory() as session:
            shops_result = await session.execute(select(Shop))
            shops = shops_result.scalars().all()

            for shop in shops:
                try:
                    raw_input = {
                        "shop_id": str(shop.id),
                        "sender_phone": shop.phone_number,
                        "sender_role": SenderRole.OWNER.value,
                        "report_date": today,
                    }
                    result = await tool.execute(raw_input, session)
                    d = result.data

                    lines = [
                        f"🌙 *Daily Summary — {today}*",
                        f"📦 Orders: {d.get('total_orders', 0)} (Paid: {d.get('paid_orders', 0)})",
                        f"💰 Sales: Rs. {d.get('total_sales', 0)}",
                        f"📒 New Udhaar: Rs. {d.get('new_debit', 0)}",
                        f"✅ Payments: Rs. {d.get('new_credit', 0)}",
                    ]
                    low_stock = d.get("low_stock_products", [])
                    if low_stock:
                        lines.append(f"⚠️ Low Stock ({len(low_stock)} items):")
                        for p in low_stock[:5]:
                            lines.append(f"  • {p['name']}: {p['current_stock']} remaining")
                    message = "\n".join(lines)

                    whatsapp = WhatsAppService(session)
                    await whatsapp.send_text_message(
                        to_phone=shop.phone_number,
                        text_body=message,
                    )
                    results["sent"] += 1
                    results["shops"].append({"shop_id": str(shop.id), "status": "sent"})
                    logger.info("Daily summary sent.", extra={"shop_id": str(shop.id)})

                except Exception as exc:
                    results["failed"] += 1
                    results["shops"].append(
                        {"shop_id": str(shop.id), "status": "failed", "error": str(exc)}
                    )
                    logger.error(
                        "Failed to send daily summary.",
                        extra={"shop_id": str(shop.id), "error": str(exc)},
                    )
        return results

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error("send_daily_summary task failed.", extra={"error": str(exc)})
        raise self.retry(exc=exc)
