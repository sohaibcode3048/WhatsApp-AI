"""API routes package."""

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

__all__ = [
    "health_router",
    "whatsapp_router",
    "auth_router",
    "shops_router",
    "customers_router",
    "products_router",
    "orders_router",
    "ledger_router",
    "approvals_router",
    "audit_logs_router",
    "reports_router",
]
