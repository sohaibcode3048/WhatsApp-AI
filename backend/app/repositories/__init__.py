"""Data repositories package exporting CRUD interfaces."""

from backend.app.repositories.audit_repository import AuditRepository
from backend.app.repositories.base_repository import BaseRepository, TenantRepository
from backend.app.repositories.customer_repository import CustomerRepository
from backend.app.repositories.ledger_repository import LedgerRepository
from backend.app.repositories.message_repository import MessageRepository
from backend.app.repositories.order_repository import OrderRepository
from backend.app.repositories.product_repository import ProductRepository
from backend.app.repositories.shop_repository import ShopRepository
from backend.app.repositories.user_repository import UserRepository

__all__ = [
    "BaseRepository",
    "TenantRepository",
    "ShopRepository",
    "UserRepository",
    "CustomerRepository",
    "ProductRepository",
    "OrderRepository",
    "LedgerRepository",
    "AuditRepository",
    "MessageRepository",
]
