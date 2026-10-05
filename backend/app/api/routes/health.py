"""Health check routes for liveness, readiness, and observability.

Three endpoints:
  GET /health  — Full readiness check (probes DB, reports all components)
  GET /ping    — Lightweight liveness probe (no I/O, container heartbeat)
"""

from datetime import datetime, timezone
from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app import __version__
from backend.app.core.config import settings
from backend.app.core.database import get_db_session
from backend.app.core.logging import logger

router = APIRouter(tags=["Health"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ComponentStatus(BaseModel):
    status: Literal["up", "degraded", "down", "configured"]
    detail: str = ""


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    service: str
    version: str
    environment: str
    timestamp: str
    components: Dict[str, Any]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="System Health & Readiness Check",
    description=(
        "Returns live component status. "
        "Probes the database with a SELECT 1 to confirm connectivity. "
        "Other components (Redis, LLM) report 'configured' until their "
        "health-probe modules are implemented."
    ),
)
async def get_health(db: AsyncSession = Depends(get_db_session)) -> HealthResponse:
    """Full readiness check — suitable for Kubernetes readinessProbe."""
    components: Dict[str, Any] = {}
    overall = "healthy"

    # ---- Database probe ---------------------------------------------------
    try:
        await db.execute(text("SELECT 1"))
        components["database"] = {"status": "up", "detail": "SELECT 1 succeeded"}
    except Exception as exc:  # pragma: no cover
        logger.error("Health check: database probe failed.", extra={"error": str(exc)})
        components["database"] = {"status": "down", "detail": str(exc)}
        overall = "unhealthy"

    # ---- Static component stubs (filled in by later modules) --------------
    components["redis"] = {"status": "configured", "detail": settings.REDIS_URL}
    components["llm_provider"] = {
        "status": "configured",
        "detail": f"{settings.LLM_PROVIDER}/{settings.GROQ_MODEL}",
    }
    components["api"] = {"status": "up", "detail": "FastAPI running"}

    return HealthResponse(
        status=overall,
        service=settings.APP_NAME,
        version=__version__,
        environment=settings.APP_ENV,
        timestamp=datetime.now(timezone.utc).isoformat(),
        components=components,
    )


@router.get(
    "/ping",
    status_code=status.HTTP_200_OK,
    summary="Simple Ping Liveness Probe",
    description="Zero-dependency liveness check for container heartbeat probes.",
)
async def get_ping() -> Dict[str, str]:
    """Respond immediately without touching any backing service."""
    return {"ping": "pong"}
