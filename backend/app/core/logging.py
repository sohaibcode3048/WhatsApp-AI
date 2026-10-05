"""Structured logging configuration for WhatsApp AI Munshi."""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict


# Keys that must never have their values logged in plain text
SENSITIVE_KEYS = {
    "api_key",
    "access_token",
    "app_secret",
    "secret_key",
    "jwt_secret",
    "password",
    "groq_api_key",
    "whatsapp_access_token",
    "whatsapp_app_secret",
}


def sanitize_payload(obj: Any) -> Any:
    """Recursively redact sensitive keys from dictionary structures."""
    if isinstance(obj, dict):
        sanitized = {}
        for k, v in obj.items():
            if str(k).lower() in SENSITIVE_KEYS:
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = sanitize_payload(v)
        return sanitized
    elif isinstance(obj, list):
        return [sanitize_payload(i) for i in obj]
    return obj


class StructuredJsonFormatter(logging.Formatter):
    """Custom JSON formatter producing structured log entries."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include structured context if attached to record
        context_keys = [
            "request_id",
            "shop_id",
            "message_id",
            "user_id",
            "tool",
            "latency_ms",
            "status",
            "error",
        ]
        for key in context_keys:
            if hasattr(record, key):
                val = getattr(record, key)
                log_entry[key] = sanitize_payload(val)

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


def setup_logging(debug: bool = False) -> logging.Logger:
    """Configure root logger with structured JSON handler."""
    log_level = logging.DEBUG if debug else logging.INFO

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(StructuredJsonFormatter())
    root_logger.addHandler(console_handler)

    # Silence overly verbose third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    return root_logger


logger = logging.getLogger("munshi")
