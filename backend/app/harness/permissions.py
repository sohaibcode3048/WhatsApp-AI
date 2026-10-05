"""Harness Permission Definitions.

Central registry of which roles are allowed to perform which actions.
Used by BaseTool.allowed_roles and can be queried directly for pre-flight
checks in the router or API layer.

Role hierarchy (highest → lowest):
  OWNER > STAFF > CUSTOMER > UNKNOWN
"""

from typing import FrozenSet, Set

from backend.app.schemas.router import SenderRole


# ---------------------------------------------------------------------------
# Canonical permission sets (reuse these across tools and routes)
# ---------------------------------------------------------------------------

#: Only the shop owner may perform this action.
OWNER_ONLY: FrozenSet[SenderRole] = frozenset({SenderRole.OWNER})

#: Owner and trusted staff members may perform this action.
OWNER_AND_STAFF: FrozenSet[SenderRole] = frozenset({SenderRole.OWNER, SenderRole.STAFF})

#: Any authenticated user (owner / staff / customer) may perform this action.
ALL_AUTHENTICATED: FrozenSet[SenderRole] = frozenset(
    {SenderRole.OWNER, SenderRole.STAFF, SenderRole.CUSTOMER}
)

#: Absolutely anyone — including unknown callers — may access this.
PUBLIC: FrozenSet[SenderRole] = frozenset(
    {SenderRole.OWNER, SenderRole.STAFF, SenderRole.CUSTOMER, SenderRole.UNKNOWN}
)


# ---------------------------------------------------------------------------
# Action-level permission map
# ---------------------------------------------------------------------------

#: Maps logical action names → minimum required roles.
#: Tools should reference this rather than hard-coding their own sets.
ACTION_PERMISSIONS: dict[str, FrozenSet[SenderRole]] = {
    # Ledger
    "add_ledger_entry": OWNER_AND_STAFF,
    "get_balance": ALL_AUTHENTICATED,
    "record_payment": OWNER_AND_STAFF,
    # Orders
    "create_order": OWNER_AND_STAFF,
    "get_order": OWNER_AND_STAFF,
    "cancel_order": OWNER_AND_STAFF,
    # Stock
    "check_stock": ALL_AUTHENTICATED,
    "update_stock": OWNER_AND_STAFF,
    "get_low_stock": OWNER_AND_STAFF,
    # Customers
    "find_customer": OWNER_AND_STAFF,
    "get_customer": OWNER_AND_STAFF,
    "add_customer": OWNER_AND_STAFF,
    # Products
    "find_product": ALL_AUTHENTICATED,
    "add_product": OWNER_ONLY,
    "update_product": OWNER_ONLY,
    # Reporting
    "daily_summary": OWNER_AND_STAFF,
    "get_sales_summary": OWNER_AND_STAFF,
    # Approvals
    "approve_action": OWNER_ONLY,
    "reject_action": OWNER_ONLY,
    # Configuration
    "update_shop_settings": OWNER_ONLY,
}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def is_allowed(action: str, role: SenderRole) -> bool:
    """Return True if ``role`` is permitted to execute ``action``."""
    allowed: FrozenSet[SenderRole] = ACTION_PERMISSIONS.get(action, OWNER_ONLY)
    return role in allowed


def get_allowed_roles(action: str) -> FrozenSet[SenderRole]:
    """Return the set of roles that may perform ``action``."""
    return ACTION_PERMISSIONS.get(action, OWNER_ONLY)
