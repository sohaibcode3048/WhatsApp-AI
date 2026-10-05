"""Tool Framework — MODULE 10.

Every agent-callable operation is a BaseTool subclass.  The harness calls
``execute()`` which runs all 10 safety stages before touching the database.

Stage summary (matches context.md Section 3):
  1.  Schema validation    — input validated by Pydantic before anything runs
  2.  Authentication       — sender identity confirmed (role from RouterService)
  3.  Authorization        — RBAC: role must be in tool.allowed_roles
  4.  Tenant isolation     — all entity IDs verified to belong to shop_id
  5.  Business rule check  — domain-specific constraints (positive amounts, etc.)
  6.  Confirmation check   — financial mutations require Haan/Nahi (MODULE 15)
  7.  Approval check       — high-value ops require Owner approval (MODULE 16)
  8.  Idempotency check    — deduplication by idempotency_key (MODULE 17)
  9.  DB transaction       — atomic commit of data + audit log
  10. Audit logging        — immutable record written inside the transaction

Stages 6, 7, 8 are stubs in this module (raise NotImplemented prompts) — they
are fully implemented in MODULEs 15–17. Every tool already calls the stubs so
the pipeline wiring is correct now and the stubs can be replaced without
changing any tool code.
"""

import uuid
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, ClassVar, Dict, Optional, Set

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import AuthorizationError, ToolExecutionError, ValidationError
from backend.app.core.logging import logger
from backend.app.schemas.router import SenderRole
from backend.app.services.audit_service import AuditService


# ---------------------------------------------------------------------------
# Tool input / output schemas
# ---------------------------------------------------------------------------


class ToolInput(BaseModel):
    """Every tool request must carry shop_id and sender context.

    These fields are set by the harness before the tool sees the input —
    the LLM is NOT trusted to supply them.
    """

    shop_id: uuid.UUID
    sender_phone: str
    sender_role: SenderRole
    idempotency_key: Optional[str] = None   # filled by MODULE 17

    model_config = {"arbitrary_types_allowed": True}


class ToolResult(BaseModel):
    """Standardised wrapper returned by every tool execution."""

    success: bool
    tool_name: str
    data: Dict[str, Any] = {}
    message: str = ""
    idempotency_key: Optional[str] = None


# ---------------------------------------------------------------------------
# RBAC permission sets (reused across tools)
# ---------------------------------------------------------------------------

OWNER_ONLY: Set[SenderRole] = {SenderRole.OWNER}
OWNER_AND_STAFF: Set[SenderRole] = {SenderRole.OWNER, SenderRole.STAFF}
ALL_AUTHENTICATED: Set[SenderRole] = {SenderRole.OWNER, SenderRole.STAFF, SenderRole.CUSTOMER}


# ---------------------------------------------------------------------------
# Base tool
# ---------------------------------------------------------------------------


class BaseTool(ABC):
    """Abstract base for all agent-callable tools.

    Subclasses must declare:
      name         — unique snake_case identifier used in audit logs
      description  — natural language description for the LLM
      allowed_roles — which SenderRoles may call this tool
      input_schema  — Pydantic model for the tool's domain-specific input

    Subclasses must implement:
      _run(input, session) — the actual database work
      _check_business_rules(input) — domain validations before any DB call
      _check_tenant_isolation(input, session) — verify IDs belong to shop_id
    """

    name: ClassVar[str]
    description: ClassVar[str]
    allowed_roles: ClassVar[Set[SenderRole]]
    input_schema: ClassVar[type[ToolInput]]

    # ------------------------------------------------------------------
    # Public entry point — the harness calls this
    # ------------------------------------------------------------------

    async def execute(
        self,
        raw_input: Dict[str, Any],
        session: AsyncSession,
    ) -> ToolResult:
        """Run all 10 safety stages then execute the tool.

        Returns ToolResult; never raises (stages raise typed exceptions that
        the harness translates to user-friendly WhatsApp messages).
        """
        tool_name = self.name

        # Stage 1 — Schema validation
        tool_input = self._validate_input(raw_input)

        # Stage 2+3 — Authentication & Authorization (role already resolved by router)
        self._check_authorization(tool_input)

        # Stage 4 — Tenant isolation
        await self._check_tenant_isolation(tool_input, session)

        # Stage 5 — Business rules
        self._check_business_rules(tool_input)

        # Stage 6 — Confirmation (stub — MODULE 15 replaces this)
        await self._check_confirmation(tool_input)

        # Stage 7 — Approval (stub — MODULE 16 replaces this)
        await self._check_approval(tool_input)

        # Stage 8 — Idempotency (stub — MODULE 17 replaces this)
        await self._check_idempotency(tool_input, session)

        # Stage 9+10 — DB transaction + audit log (inside _run)
        result = await self._run(tool_input, session)

        logger.info(
            "Tool executed successfully.",
            extra={
                "tool": tool_name,
                "shop_id": str(tool_input.shop_id),
                "sender": tool_input.sender_phone,
                "success": result.success,
            },
        )
        return result

    # ------------------------------------------------------------------
    # Stage implementations
    # ------------------------------------------------------------------

    def _validate_input(self, raw_input: Dict[str, Any]) -> ToolInput:
        """Stage 1 — Pydantic schema validation."""
        try:
            return self.input_schema.model_validate(raw_input)
        except Exception as exc:
            raise ValidationError(
                f"Tool '{self.name}' input validation failed: {exc}",
                details={"tool": self.name, "error": str(exc)},
            ) from exc

    def _check_authorization(self, tool_input: ToolInput) -> None:
        """Stage 2+3 — Verify role is in allowed_roles."""
        if tool_input.sender_role not in self.allowed_roles:
            raise AuthorizationError(
                f"Role '{tool_input.sender_role}' is not permitted to call '{self.name}'.",
                details={
                    "tool": self.name,
                    "role": tool_input.sender_role,
                    "allowed": [r.value for r in self.allowed_roles],
                },
            )

    # ------------------------------------------------------------------
    # Abstract hooks — subclasses implement these
    # ------------------------------------------------------------------

    @abstractmethod
    async def _run(
        self,
        tool_input: ToolInput,
        session: AsyncSession,
    ) -> ToolResult:
        """Stage 9 — Execute the actual database work inside a transaction."""

    @abstractmethod
    def _check_business_rules(self, tool_input: ToolInput) -> None:
        """Stage 5 — Validate domain-specific constraints."""

    @abstractmethod
    async def _check_tenant_isolation(
        self,
        tool_input: ToolInput,
        session: AsyncSession,
    ) -> None:
        """Stage 4 — Verify all entity IDs belong to shop_id."""

    # ------------------------------------------------------------------
    # Stub stages — replaced by later modules
    # ------------------------------------------------------------------

    # Tools that require Haan/Nahi confirmation before execution.
    # Subclasses can override this to add/remove from the set.
    _REQUIRES_CONFIRMATION: ClassVar[bool] = False

    async def _check_confirmation(self, tool_input: ToolInput) -> None:
        """Stage 6 — Confirmation gate (MODULE 15).

        If _REQUIRES_CONFIRMATION is True on the subclass, raises
        ConfirmationRequired so the agent can ask the user before proceeding.
        In test / stub mode the class variable is False and this is a no-op.
        """
        if not self._REQUIRES_CONFIRMATION:
            return
        from backend.app.harness.confirmation import confirmation_engine
        prompt = self._build_confirmation_prompt(tool_input)
        await confirmation_engine.request_confirmation(
            shop_id=str(tool_input.shop_id),
            sender_phone=tool_input.sender_phone,
            tool_name=self.name,
            tool_params=tool_input.model_dump(mode="json"),
            prompt_text=prompt,
        )

    def _build_confirmation_prompt(self, tool_input: ToolInput) -> str:
        """Override in subclasses to produce a human-readable confirmation prompt."""
        return f"Kya aap '{self.name}' execute karna chahte hain? Haan / Nahi"

    async def _check_approval(self, tool_input: ToolInput) -> None:
        """Stage 7 — Approval gate for high-value operations (MODULE 16)."""
        from backend.app.harness.approval import approval_engine
        params = tool_input.model_dump(mode="json")
        if approval_engine.needs_approval(self.name, params):
            await approval_engine.request_approval(
                shop_id=str(tool_input.shop_id),
                requested_by=tool_input.sender_phone,
                tool_name=self.name,
                params=params,
            )

    async def _check_idempotency(
        self, tool_input: ToolInput, session: AsyncSession
    ) -> None:
        """Stage 8 — Idempotency deduplication (MODULE 17)."""
        if tool_input.idempotency_key:
            from backend.app.harness.idempotency import idempotency_engine
            await idempotency_engine.check_and_raise_if_duplicate(
                tool_input.idempotency_key, self.name
            )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _require_positive_amount(self, amount: Decimal, field_name: str = "amount") -> None:
        """Raise ValidationError if amount is not strictly positive."""
        if amount <= 0:
            raise ValidationError(
                f"'{field_name}' must be a positive value, got {amount}.",
                details={"field": field_name, "value": str(amount)},
            )

    async def _write_audit(
        self,
        session: AsyncSession,
        tool_input: ToolInput,
        action: str,
        entity_type: str,
        entity_id: Optional[uuid.UUID],
        request_data: Dict[str, Any],
        result_data: Dict[str, Any],
    ) -> None:
        """Write an immutable audit log entry (called inside _run transactions)."""
        audit = AuditService(session)
        await audit.record_event(
            shop_id=tool_input.shop_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            tool_name=self.name,
            request_data=request_data,
            result_data=result_data,
        )


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Central registry mapping tool names to instances.

    Used by the agent to look up tools by name and pass calls to the harness.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        """Return tool by name or raise ToolExecutionError."""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolExecutionError(
                f"Unknown tool: '{name}'",
                tool_name=name,
                details={"available": list(self._tools.keys())},
            )
        return tool

    def list_tools(self) -> list[Dict[str, Any]]:
        """Return tool metadata for LLM prompt construction."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "allowed_roles": [r.value for r in t.allowed_roles],
            }
            for t in self._tools.values()
        ]

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# Singleton registry — populated by tool modules at import time
tool_registry = ToolRegistry()
