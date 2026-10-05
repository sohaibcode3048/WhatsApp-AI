"""Celery application factory for background workers.

Usage:
    celery -A backend.app.workers.celery_app worker --loglevel=info
    celery -A backend.app.workers.celery_app beat  --loglevel=info
"""

from celery import Celery
from celery.schedules import crontab

from backend.app.core.config import settings


def make_celery() -> Celery:
    """Create and configure the Celery application."""
    app = Celery(
        "munshi_workers",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
        include=[
            "backend.app.workers.tasks",
            "backend.app.workers.reminders",
            "backend.app.workers.summaries",
            "backend.app.workers.stock_alerts",
        ],
    )

    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Asia/Karachi",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        # Beat schedule — periodic tasks
        beat_schedule={
            "daily-summary-midnight": {
                "task": "backend.app.workers.summaries.send_daily_summary",
                "schedule": crontab(hour=23, minute=55),
                "args": [],
            },
            "payment-reminders-morning": {
                "task": "backend.app.workers.reminders.send_payment_reminders",
                "schedule": crontab(hour=10, minute=0),
                "args": [],
            },
            "stock-alerts-noon": {
                "task": "backend.app.workers.stock_alerts.send_low_stock_alerts",
                "schedule": crontab(hour=12, minute=0),
                "args": [],
            },
        },
    )

    return app


celery_app = make_celery()
