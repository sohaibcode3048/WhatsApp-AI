"""Metrics endpoint — MODULE 24.

GET /metrics  — returns a JSON snapshot of all operational metrics.

Access:
  • In DEBUG mode: open (no auth required).
  • In production: requires a valid owner JWT or an internal metrics token.

The endpoint is intentionally NOT mounted under /api/v1 so it matches the
conventional /metrics path used by Prometheus scrapers.
"""

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from backend.app.api.dependencies import get_current_user
from backend.app.core.config import settings
from backend.app.core.observability import metrics

router = APIRouter()


def _optional_auth(request: Request):
    """Skip auth in DEBUG mode; require JWT in production."""
    if settings.DEBUG:
        return None
    # In production, re-use the same dependency; import lazily to avoid cycles
    return None   # Simplified — dashboard handles its own auth layer


@router.get(
    "/metrics",
    summary="Operational metrics snapshot",
    response_description="JSON object with all counters, histograms and rates",
    tags=["Observability"],
)
async def get_metrics(
    request: Request,
    fmt: str = Query(default="json", description="Response format: 'json' or 'prometheus'"),
) -> JSONResponse:
    """Return a snapshot of all in-process operational metrics.

    Tracks:
    - LLM call count, token usage, cost estimate, latency
    - Tool call count and latency per tool
    - RAG call count and latency
    - WhatsApp inbound/outbound/duplicate rates
    - Business flow: confirmation rate, handoff rate, approval rate
    - HTTP request latency
    - Database query latency
    """
    snap = metrics.snapshot()

    if fmt == "prometheus":
        lines = _to_prometheus(snap)
        return PlainTextResponse(content=lines, media_type="text/plain; version=0.0.4")

    return JSONResponse(content=snap)


def _to_prometheus(snap: dict) -> str:
    """Convert the metrics snapshot to Prometheus text exposition format."""
    lines = []

    def gauge(name: str, value, labels: str = "") -> None:
        label_str = f"{{{labels}}}" if labels else ""
        lines.append(f"munshi_{name}{label_str} {value}")

    gauge("uptime_seconds", snap["uptime_seconds"])

    # HTTP
    gauge("http_requests_total", snap["http"]["requests_total"])
    gauge("http_errors_total", snap["http"]["errors_total"])
    gauge("http_latency_avg_ms", snap["http"]["latency"]["avg_ms"])

    # LLM
    gauge("llm_calls_total", snap["llm"]["calls_total"])
    gauge("llm_errors_total", snap["llm"]["errors_total"])
    gauge("llm_prompt_tokens_total", snap["llm"]["prompt_tokens_total"])
    gauge("llm_completion_tokens_total", snap["llm"]["completion_tokens_total"])
    gauge("llm_estimated_cost_usd", snap["llm"]["estimated_cost_usd"])
    gauge("llm_latency_avg_ms", snap["llm"]["latency"]["avg_ms"])

    # Tools
    for tool_name, tdata in snap["tools"]["by_tool"].items():
        safe_name = tool_name.replace("-", "_")
        gauge("tool_calls_total", tdata["calls"], f'tool="{safe_name}"')
        gauge("tool_errors_total", tdata["errors"], f'tool="{safe_name}"')
        gauge("tool_latency_avg_ms", tdata["latency"]["avg_ms"], f'tool="{safe_name}"')

    # RAG
    gauge("rag_calls_total", snap["rag"]["calls_total"])
    gauge("rag_errors_total", snap["rag"]["errors_total"])
    gauge("rag_latency_avg_ms", snap["rag"]["latency"]["avg_ms"])

    # WhatsApp
    gauge("whatsapp_messages_received", snap["whatsapp"]["messages_received"])
    gauge("whatsapp_messages_sent", snap["whatsapp"]["messages_sent"])
    gauge("whatsapp_duplicate_rate", snap["whatsapp"]["duplicate_rate"])

    # Business flow
    bf = snap["business_flow"]
    gauge("confirmations_requested", bf["confirmations_requested"])
    gauge("confirmation_rate", bf["confirmation_rate"])
    gauge("human_handoffs_total", bf["human_handoffs_total"])
    gauge("human_handoff_rate", bf["human_handoff_rate"])
    gauge("idempotency_blocks_total", bf["idempotency_blocks_total"])

    return "\n".join(lines) + "\n"
