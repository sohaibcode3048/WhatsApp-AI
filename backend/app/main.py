"""Main FastAPI application entry point for WhatsApp AI Munshi."""

import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app import __version__
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.whatsapp import router as whatsapp_router
from backend.app.api.routes.auth import router as auth_router
from backend.app.api.routes.shops import router as shops_router
from backend.app.api.routes.customers import router as customers_router
from backend.app.api.routes.products import router as products_router
from backend.app.api.routes.orders import router as orders_router
from backend.app.api.routes.ledger import router as ledger_router
from backend.app.api.routes.approvals import router as approvals_router
from backend.app.api.routes.audit_logs import router as audit_logs_router
from backend.app.api.routes.reports import router as reports_router
from backend.app.api.routes.metrics import router as metrics_router
from backend.app.core.config import settings
from backend.app.core.observability import metrics as metrics_store
from backend.app.core.exceptions import MunshiException
from backend.app.core.logging import logger, setup_logging
from backend.app.core.security_middleware import SecurityMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown events."""
    setup_logging(debug=settings.DEBUG)
    logger.info(
        f"Starting {settings.APP_NAME} v{__version__} [env={settings.APP_ENV}]"
    )
    yield
    logger.info(f"Shutting down {settings.APP_NAME}")


def create_application() -> FastAPI:
    """Factory creating and configuring the FastAPI instance."""
    app = FastAPI(
        title="WhatsApp AI Munshi",
        description="Production Multi-Tenant AI Digital Accountant for WhatsApp",
        version=__version__,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    # Cross-Origin Resource Sharing
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.DEBUG else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Security headers + IP rate limiting
    app.add_middleware(SecurityMiddleware)

    # Request Tracing & Correlation Middleware
    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        start_time = time.perf_counter()
        logger.info(
            f"--> {request.method} {request.url.path}",
            extra={"request_id": request_id},
        )

        try:
            response = await call_next(request)
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time-Ms"] = str(latency_ms)

            logger.info(
                f"<-- {request.method} {request.url.path} {response.status_code} ({latency_ms}ms)",
                extra={
                    "request_id": request_id,
                    "status": response.status_code,
                    "latency_ms": latency_ms,
                },
            )
            metrics_store.record_http_request(latency_ms, is_error=response.status_code >= 500)
            return response
        except Exception as exc:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            metrics_store.record_http_request(latency_ms, is_error=True)
            logger.error(
                f"Unhandled exception on {request.method} {request.url.path}: {str(exc)}",
                exc_info=True,
                extra={"request_id": request_id, "latency_ms": latency_ms},
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error": "An internal system error occurred.",
                    "code": "INTERNAL_SERVER_ERROR",
                    "request_id": request_id,
                },
                headers={"X-Request-ID": request_id},
            )

    # Domain Exception Handler
    @app.exception_handler(MunshiException)
    async def munshi_exception_handler(request: Request, exc: MunshiException) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        logger.warning(
            f"Domain exception [{exc.code}]: {exc.message}",
            extra={"request_id": request_id, "error": exc.code},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.message,
                "code": exc.code,
                "details": exc.details,
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )

    # Mount Routes
    # Health checks available at root and versioned prefix
    app.include_router(health_router, prefix="", tags=["Health"])
    app.include_router(health_router, prefix=settings.API_V1_PREFIX, tags=["Health"])
    app.include_router(whatsapp_router, prefix="", tags=["WhatsApp Webhook"])
    app.include_router(whatsapp_router, prefix=settings.API_V1_PREFIX, tags=["WhatsApp Webhook"])

    # Admin / Dashboard API — all under /api/v1
    _v1 = settings.API_V1_PREFIX
    app.include_router(auth_router, prefix=_v1)
    app.include_router(shops_router, prefix=_v1)
    app.include_router(customers_router, prefix=_v1)
    app.include_router(products_router, prefix=_v1)
    app.include_router(orders_router, prefix=_v1)
    app.include_router(ledger_router, prefix=_v1)
    app.include_router(approvals_router, prefix=_v1)
    app.include_router(audit_logs_router, prefix=_v1)
    app.include_router(reports_router, prefix=_v1)
    app.include_router(metrics_router, prefix="")   # /metrics (no version prefix)

    return app


app = create_application()
