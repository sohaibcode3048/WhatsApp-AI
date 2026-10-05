"""Centralized exception hierarchy for WhatsApp AI Munshi."""

from typing import Any, Dict, Optional


class MunshiException(Exception):
    """Base exception for all WhatsApp AI Munshi errors."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        status_code: int = 500,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}


class ValidationError(MunshiException):
    """Raised when data or tool schema validation fails."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            status_code=422,
            details=details,
        )


class AuthenticationError(MunshiException):
    """Raised when request or user fails authentication."""

    def __init__(self, message: str = "Authentication failed", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="AUTHENTICATION_FAILED",
            status_code=401,
            details=details,
        )


class AuthorizationError(MunshiException):
    """Raised when a user lacks required permissions for an action."""

    def __init__(self, message: str = "Permission denied", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="AUTHORIZATION_DENIED",
            status_code=403,
            details=details,
        )


class TenantIsolationError(AuthorizationError):
    """Raised when a cross-tenant data access violation is detected."""

    def __init__(self, message: str = "Cross-tenant access prohibited", details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, details=details)
        self.code = "TENANT_ISOLATION_VIOLATION"


class NotFoundError(MunshiException):
    """Raised when a requested resource does not exist."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="NOT_FOUND",
            status_code=404,
            details=details,
        )


class ToolExecutionError(MunshiException):
    """Raised when a tool execution fails inside the business harness."""

    def __init__(self, message: str, tool_name: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        data = details or {}
        if tool_name:
            data["tool_name"] = tool_name
        super().__init__(
            message=message,
            code="TOOL_EXECUTION_ERROR",
            status_code=400,
            details=data,
        )


class DatabaseError(MunshiException):
    """Raised when a database transaction or operation fails."""

    def __init__(self, message: str = "Database operation failed", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="DATABASE_ERROR",
            status_code=500,
            details=details,
        )


class LLMError(MunshiException):
    """Raised when an LLM provider request fails or produces invalid output."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="LLM_PROVIDER_ERROR",
            status_code=502,
            details=details,
        )


class RAGError(MunshiException):
    """Raised when vector retrieval or ingestion fails."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="RAG_RETRIEVAL_ERROR",
            status_code=502,
            details=details,
        )


class WhatsAppError(MunshiException):
    """Raised when communication with Meta WhatsApp Cloud API fails."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="WHATSAPP_API_ERROR",
            status_code=502,
            details=details,
        )


class ConfirmationRequired(MunshiException):
    """Raised when an operation is paused awaiting user confirmation (Haan/Nahi)."""

    def __init__(self, message: str, pending_action_id: str, prompt_text: str):
        super().__init__(
            message=message,
            code="CONFIRMATION_REQUIRED",
            status_code=200,
            details={
                "pending_action_id": pending_action_id,
                "prompt_text": prompt_text,
            },
        )


class ApprovalRequired(MunshiException):
    """Raised when an operation exceeds financial limits and awaits Owner approval."""

    def __init__(self, message: str, approval_id: str, prompt_text: str):
        super().__init__(
            message=message,
            code="APPROVAL_REQUIRED",
            status_code=200,
            details={
                "approval_id": approval_id,
                "prompt_text": prompt_text,
            },
        )


class HumanHandoffRequired(MunshiException):
    """Raised when the agent is uncertain and must escalate to human."""

    def __init__(self, message: str, reason: str):
        super().__init__(
            message=message,
            code="HUMAN_HANDOFF_REQUIRED",
            status_code=200,
            details={"reason": reason},
        )


class IdempotencyConflictError(MunshiException):
    """Raised when a duplicate request is received."""

    def __init__(self, message: str = "Duplicate request detected; operation skipped.", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="IDEMPOTENCY_CONFLICT",
            status_code=409,
            details=details,
        )


class SpeechTranscriptionError(MunshiException):
    """Raised when audio download or speech-to-text transcription fails."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="SPEECH_TRANSCRIPTION_ERROR",
            status_code=502,
            details=details,
        )


class LowConfidenceTranscriptionError(MunshiException):
    """Raised when transcription confidence is below the configured threshold.

    The pipeline must NOT automatically execute financial or inventory
    operations when this is raised — it must ask the user to resend as text.
    """

    def __init__(
        self,
        message: str = "Voice message unclear. Please resend as text.",
        confidence: float = 0.0,
        transcript: str = "",
    ):
        super().__init__(
            message=message,
            code="LOW_CONFIDENCE_TRANSCRIPTION",
            status_code=200,   # workflow-control, not an error response
            details={"confidence": confidence, "partial_transcript": transcript},
        )
