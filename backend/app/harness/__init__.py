"""AI Agent Safety Harness — MODULEs 15, 16, 17, 22.

Exposes all harness components used by BaseTool.execute() and the pipeline:
  confirmation_engine  — MODULE 15: Haan/Nahi state machine
  approval_engine      — MODULE 16: high-value owner approval
  idempotency_engine   — MODULE 17: duplicate-execution prevention
  validation           — MODULE 20: composable input validators
  permissions          — RBAC action permission map
  fallback_handler     — MODULE 22: human handoff events
"""

from backend.app.harness.confirmation import (
    ConfirmationEngine,
    ConfirmationStatus,
    confirmation_engine,
    is_confirmation_yes,
    is_confirmation_no,
)
from backend.app.harness.approval import ApprovalEngine, ApprovalStatus, approval_engine
from backend.app.harness.idempotency import (
    IdempotencyEngine,
    idempotency_engine,
    compute_tool_idempotency_key,
)
from backend.app.harness.validation import (
    require_positive_amount,
    require_non_empty_string,
    require_valid_uuid,
    require_max_amount,
    require_non_negative_integer,
)
from backend.app.harness.permissions import (
    OWNER_ONLY,
    OWNER_AND_STAFF,
    ALL_AUTHENTICATED,
    PUBLIC,
    ACTION_PERMISSIONS,
    is_allowed,
    get_allowed_roles,
)
from backend.app.harness.fallback import (
    FallbackHandler,
    HandoffReason,
    HumanHandoffEvent,
    fallback_handler,
)

__all__ = [
    # Confirmation
    "ConfirmationEngine", "ConfirmationStatus", "confirmation_engine",
    "is_confirmation_yes", "is_confirmation_no",
    # Approval
    "ApprovalEngine", "ApprovalStatus", "approval_engine",
    # Idempotency
    "IdempotencyEngine", "idempotency_engine", "compute_tool_idempotency_key",
    # Validation
    "require_positive_amount", "require_non_empty_string", "require_valid_uuid",
    "require_max_amount", "require_non_negative_integer",
    # Permissions
    "OWNER_ONLY", "OWNER_AND_STAFF", "ALL_AUTHENTICATED", "PUBLIC",
    "ACTION_PERMISSIONS", "is_allowed", "get_allowed_roles",
    # Fallback
    "FallbackHandler", "HandoffReason", "HumanHandoffEvent", "fallback_handler",
]
