"""Payment Reminders Worker — MODULE 21.

Scans for customers with outstanding dues across shops and sends
friendly WhatsApp reminders.
Scheduled by Celery Beat at 10:00 AM every day.
"""

import asyncio
import logging
from decimal import Decimal
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
    name="backend.app.workers.reminders.send_payment_reminders",
)
def send_payment_reminders(self) -> Dict[str, Any]:
    """Send payment reminders to customers with overdue balances.

    For each shop:
      1. Fetch all active customers.
      2. Calculate current due balance for each customer.
      3. If balance > 0, send reminder via WhatsAppService.
      4. Log results and return summary stats.
    """
    async def _run():
        from sqlalchemy import select
        from backend.app.core.database import async_session_factory
        from backend.app.models.customer import Customer
        from backend.app.models.shop import Shop
        from backend.app.repositories.ledger_repository import LedgerRepository
        from backend.app.services.whatsapp_service import WhatsAppService

        results = {"shops_processed": 0, "reminders_sent": 0, "skipped": 0, "errors": 0}

        async with async_session_factory() as session:
            shops_result = await session.execute(select(Shop))
            shops = shops_result.scalars().all()

            for shop in shops:
                results["shops_processed"] += 1
                cust_result = await session.execute(
                    select(Customer).where(
                        Customer.shop_id == shop.id,
                        Customer.is_active == True,
                    )
                )
                customers = cust_result.scalars().all()
                ledger_repo = LedgerRepository(session)
                whatsapp = WhatsAppService(session)

                for cust in customers:
                    try:
                        balance = await ledger_repo.get_balance(cust.id, shop.id)
                        if balance > Decimal("0"):
                            msg = (
                                f"Assalam o alaikum {cust.name} sahib, {shop.name} ki taraf se "
                                f"yad-dahani: aap ka baqi hisab Rs. {balance:,.2f} hai. "
                                f"Baraye meherbani jald ada farmayein. Shukriya!"
                            )
                            await whatsapp.send_text_message(
                                to_phone=cust.phone_number,
                                text_body=msg,
                            )
                            results["reminders_sent"] += 1
                        else:
                            results["skipped"] += 1
                    except Exception as exc:
                        logger.error(
                            "Failed to send reminder to customer.",
                            extra={"customer_id": str(cust.id), "error": str(exc)},
                        )
                        results["errors"] += 1

            await session.commit()
            return results

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error("Payment reminders task failed.", extra={"error": str(exc)})
        raise self.retry(exc=exc)
