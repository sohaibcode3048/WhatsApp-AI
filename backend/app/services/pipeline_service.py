"""Agent + Harness Pipeline Service — MODULE 20.

This is the single entry point from the webhook handler into the full
AI pipeline. It wires together:

  WhatsApp message
      → MessageRepository (persist raw)
      → LanguageService (normalise)
      → RouterService (role + intent)
      → AgentService (LLM + tool dispatch through 10-stage harness)
      → WhatsAppService.send_text_message (reply)

Typed exceptions from the harness are translated to user-friendly
WhatsApp messages before sending, so the user always gets a reply.
"""

import json
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import (
    ApprovalRequired,
    AuthorizationError,
    ConfirmationRequired,
    HumanHandoffRequired,
    IdempotencyConflictError,
    LLMError,
    MunshiException,
    NotFoundError,
    ValidationError,
)
from backend.app.core.logging import logger
from backend.app.repositories.message_repository import MessageRepository
from backend.app.schemas.language import NormalizedMessage
from backend.app.schemas.router import MessageIntent, RouterContext, SenderRole
from backend.app.services.agent_service import AgentService, MockLLMProvider
from backend.app.services.router_service import RouterService
from backend.app.services.whatsapp_service import WhatsAppService


class PipelineService:
    """Orchestrates the complete inbound-message processing pipeline."""

    def __init__(
        self,
        db: AsyncSession,
        agent: Optional[AgentService] = None,
    ) -> None:
        self._db = db
        self._whatsapp = WhatsAppService(db)
        self._router = RouterService(db)
        self._agent = agent or AgentService()

    async def handle_message(
        self,
        message_id: uuid.UUID,
        whatsapp_message_id: str,
        raw_text: str,
        normalized_message_json: Optional[str],
        sender_phone: str,
        shop_id: Optional[uuid.UUID],
        phone_number_id: str,
    ) -> str:
        """Full pipeline for one inbound message. Returns the reply string."""

        # --- Parse NormalizedMessage from JSON ---
        norm: Optional[NormalizedMessage] = None
        normalized_text = raw_text
        ledger_keyword = "NONE"
        name_candidates = []
        has_amount = False

        if normalized_message_json and not normalized_message_json.startswith("["):
            try:
                norm = NormalizedMessage.model_validate_json(normalized_message_json)
                normalized_text = norm.normalized_text
                ledger_keyword = norm.ledger_keyword.value
                name_candidates = norm.name_candidates
                has_amount = bool(norm.amounts)
            except Exception:
                pass

        # --- Route the message ---
        ctx = RouterContext(
            sender_phone=sender_phone,
            shop_id=str(shop_id) if shop_id else None,
            raw_text=raw_text,
            normalized_text=normalized_text,
            ledger_keyword=ledger_keyword,
            has_amount=has_amount,
            name_candidates=name_candidates,
        )

        routed = await self._router.route(ctx)

        logger.info(
            "Pipeline routing complete.",
            extra={
                "message_id": str(message_id),
                "role": routed.sender_role.value,
                "intent": routed.intent.value,
                "confidence": routed.confidence,
            },
        )

        # --- Human handoff shortcut ---
        if routed.intent == MessageIntent.HUMAN_HANDOFF:
            reply = (
                "Is request ko main safely process nahi kar sakta. "
                "Owner ko notify kar diya gaya hai."
            )
            await self._send_reply(sender_phone, reply, phone_number_id)
            return reply

        # --- Run agent ---
        reply = await self._run_agent(
            message=normalized_text,
            raw_message=raw_text,
            shop_id=str(shop_id) if shop_id else "",
            sender_phone=sender_phone,
            sender_role=routed.sender_role.value,
        )

        # --- Send reply ---
        await self._send_reply(sender_phone, reply, phone_number_id)

        # --- Update message processing status ---
        repo = MessageRepository(self._db)
        await repo.mark_completed(message_id, intent=routed.intent.value)

        return reply

    async def _run_agent(
        self,
        message: str,
        raw_message: str,
        shop_id: str,
        sender_phone: str,
        sender_role: str,
    ) -> str:
        """Run AgentService; translate harness exceptions to user-friendly replies."""
        try:
            return await self._agent.process(
                message=message,
                shop_id=shop_id,
                sender_phone=sender_phone,
                sender_role=sender_role,
                session=self._db,
            )

        except ConfirmationRequired as exc:
            return exc.details.get("prompt_text", "Kya aap confirm karna chahte hain? Haan / Nahi?")

        except ApprovalRequired as exc:
            return exc.details.get("prompt_text", "Is operation ke liye Owner approval chahiye.")

        except IdempotencyConflictError:
            return "Yeh transaction pehle se record ho chuka hai."

        except AuthorizationError as exc:
            return f"Maafi — aapke paas yeh karne ki ijazat nahi hai. ({exc.message})"

        except NotFoundError as exc:
            return f"Record nahi mila: {exc.message}"

        except ValidationError as exc:
            return f"Input mein masla hai: {exc.message}"

        except LLMError as exc:
            logger.error("LLM error in pipeline.", extra={"error": str(exc)})
            return "AI service temporarily unavailable. Dobara try karein."

        except MunshiException as exc:
            logger.error("Pipeline error.", extra={"code": exc.code, "error": exc.message})
            return "Kuch masla hua. Dobara bhejein ya owner se rabta karein."

        except Exception as exc:
            logger.error("Unexpected pipeline error.", extra={"error": str(exc)})
            return "System error. Owner ko notify kar diya gaya hai."

    async def _send_reply(
        self, to_phone: str, text: str, phone_number_id: str
    ) -> None:
        """Send WhatsApp reply — log failures but don't raise."""
        try:
            await self._whatsapp.send_text_message(
                to_phone=to_phone,
                text_body=text,
                phone_number_id=phone_number_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to send WhatsApp reply.",
                extra={"to": to_phone, "error": str(exc)},
            )
