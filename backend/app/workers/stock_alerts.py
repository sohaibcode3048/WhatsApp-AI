"""Low Stock Alerts Worker — MODULE 21.

Checks stock levels across all shops and alerts owners via WhatsApp
when any product drops below its configured low_stock_threshold.
Scheduled by Celery Beat at 12:00 PM every day.
"""

import asyncio
import logging
from typing import Any, Dict

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
    name="backend.app.workers.stock_alerts.send_low_stock_alerts",
)
def send_low_stock_alerts(self) -> Dict[str, Any]:
    """Scan products for low stock and notify the shop owner.

    For each shop:
      1. Find products where current_stock <= low_stock_threshold and is_active is True.
      2. If any found, format an alert message in Roman Urdu.
      3. Send to shop.phone_number via WhatsAppService.
      4. Log results.
    """
    async def _run():
        from sqlalchemy import select
        from backend.app.core.database import async_session_factory
        from backend.app.models.product import Product
        from backend.app.models.shop import Shop
        from backend.app.services.whatsapp_service import WhatsAppService

        results = {"shops_processed": 0, "alerts_sent": 0, "shops_with_low_stock": 0}

        async with async_session_factory() as session:
            shops_result = await session.execute(select(Shop))
            shops = shops_result.scalars().all()

            for shop in shops:
                results["shops_processed"] += 1
                prod_result = await session.execute(
                    select(Product).where(
                        Product.shop_id == shop.id,
                        Product.is_active == True,
                        Product.current_stock <= Product.low_stock_threshold,
                    )
                )
                low_products = prod_result.scalars().all()

                if not low_products:
                    continue

                results["shops_with_low_stock"] += 1
                items_str = "\n".join(
                    f"- {p.name}: {p.current_stock} left (Threshold: {p.low_stock_threshold})"
                    for p in low_products
                )
                alert_text = (
                    f"⚠️ *Kam Stock Ki Ittila (Low Stock Alert)*\n"
                    f"Dukaan: {shop.name}\n\n"
                    f"Mandarja zail products ka stock kam ho chuka hai:\n"
                    f"{items_str}\n\n"
                    f"Baraye meherbani naya stock mangwa lein."
                )

                whatsapp = WhatsAppService(session)
                try:
                    await whatsapp.send_text_message(
                        to_phone=shop.phone_number,
                        text_body=alert_text,
                    )
                    results["alerts_sent"] += 1
                except Exception as exc:
                    logger.error(
                        "Failed to send stock alert to shop owner.",
                        extra={"shop_id": str(shop.id), "error": str(exc)},
                    )

            await session.commit()
            return results

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error("Stock alerts task failed.", extra={"error": str(exc)})
        raise self.retry(exc=exc)
