"""Core background task registry.

Import this module to register generic utility tasks with Celery.
Domain-specific scheduled tasks live in summaries.py, reminders.py, and
stock_alerts.py.
"""

import asyncio
import logging
from typing import Any, Dict

from backend.app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from a synchronous Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, name="tasks.ping")
def ping(self) -> str:
    """Health-check task — verifiable via Celery Inspect or Flower."""
    logger.info("Celery worker is alive.")
    return "pong"


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    name="tasks.ingest_rag_document",
)
def ingest_rag_document(self, shop_id: str, document_text: str, metadata: Dict[str, Any]) -> str:
    """Background task to ingest a document into the shop's RAG vector store.

    ingest_document() is synchronous (CPU-bound embedding + ChromaDB upsert),
    so we call it directly — no event loop needed.
    """
    try:
        from backend.app.rag.ingestion import rag_ingestion
        chunk_count = rag_ingestion.ingest_document(
            shop_id=shop_id,
            text=document_text,
            document_type=metadata.get("document_type", "general"),
            source=metadata.get("source", "manual"),
            extra_metadata={k: v for k, v in metadata.items()
                            if k not in ("document_type", "source")},
        )
        logger.info(
            "RAG document ingested.",
            extra={"shop_id": shop_id, "chunks": chunk_count},
        )
        return str(chunk_count)
    except Exception as exc:
        logger.error("RAG ingestion failed.", extra={"error": str(exc)})
        raise self.retry(exc=exc)


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="tasks.send_whatsapp_message",
)
def send_whatsapp_message(self, to_phone: str, text_body: str) -> Dict[str, Any]:
    """Fire-and-forget outbound WhatsApp message task.

    Decouples the webhook response from the outbound send so the webhook
    always returns 200 quickly to Meta.
    """
    async def _send():
        from backend.app.core.database import async_session_factory
        from backend.app.services.whatsapp_service import WhatsAppService
        async with async_session_factory() as session:
            svc = WhatsAppService(session)
            return await svc.send_text_message(to_phone=to_phone, text_body=text_body)

    try:
        result = _run_async(_send())
        return result or {}
    except Exception as exc:
        logger.error("WhatsApp send task failed.", extra={"to": to_phone, "error": str(exc)})
        raise self.retry(exc=exc)
