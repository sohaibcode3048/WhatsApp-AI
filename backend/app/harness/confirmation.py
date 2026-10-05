"""Confirmation Engine — MODULE 15.

Every financial or inventory mutation requires a Haan/Nahi confirmation from
the sender before the database is touched.

State machine
-------------
  PENDING_CONFIRMATION  →  CONFIRMED   (user replies "Haan" / "Yes" / "1")
                        →  REJECTED    (user replies "Nahi" / "No" / "2")
                        →  EXPIRED     (TTL elapsed, usually 10 minutes)
                        →  EXECUTED    (tool has run successfully)

Design
------
Pending actions are stored in Redis with a TTL key (lightweight, no DB row
until confirmed). On confirmation the harness re-calls the tool with the
same parameters. A deterministic action_id (SHA-256 of tool+params) makes
the whole flow idempotent.

When Redis is unavailable (dev/test), the engine falls back to an in-process
dict so tests never need a real Redis instance.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from backend.app.core.config import settings
from backend.app.core.exceptions import ConfirmationRequired
from backend.app.core.logging import logger
from backend.app.core.security import compute_idempotency_hash

# TTL for pending confirmations (seconds)
_CONFIRMATION_TTL = 600   # 10 minutes

# In-process fallback store (test / no-Redis environments)
_IN_PROCESS_STORE: Dict[str, Dict] = {}


class ConfirmationStatus(str, Enum):
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTED = "EXECUTED"


# Urdu / English / Roman Urdu "yes" tokens
_YES_TOKENS = {"haan", "han", "ha", "yes", "y", "1", "ok", "theek", "theek hai", "ji", "bilkul"}
_NO_TOKENS = {"nahi", "nai", "no", "n", "2", "cancel", "band karo", "rokو", "mat karo"}


def is_confirmation_yes(text: str) -> bool:
    return text.strip().lower() in _YES_TOKENS


def is_confirmation_no(text: str) -> bool:
    return text.strip().lower() in _NO_TOKENS


@dataclass
class PendingAction:
    action_id: str
    shop_id: str
    sender_phone: str
    tool_name: str
    tool_params: Dict[str, Any]
    prompt_text: str
    status: ConfirmationStatus = ConfirmationStatus.PENDING_CONFIRMATION
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "shop_id": self.shop_id,
            "sender_phone": self.sender_phone,
            "tool_name": self.tool_name,
            "tool_params": self.tool_params,
            "prompt_text": self.prompt_text,
            "status": self.status.value,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PendingAction":
        d["status"] = ConfirmationStatus(d["status"])
        return cls(**d)


def _make_action_id(tool_name: str, shop_id: str, sender_phone: str, params: Dict) -> str:
    """Deterministic action ID — same tool + params produces same ID."""
    params_str = json.dumps(params, sort_keys=True, default=str)
    return compute_idempotency_hash(tool_name, shop_id, sender_phone, params_str)[:32]


class ConfirmationEngine:
    """Manages the pending-confirmation lifecycle.

    Uses Redis when available, falls back to in-process dict for dev/tests.
    """

    def __init__(self) -> None:
        self._redis = None   # lazy-initialised

    async def _get_redis(self):
        """Return async Redis client or None if unavailable."""
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            await client.ping()
            self._redis = client
            return client
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Store / retrieve
    # ------------------------------------------------------------------

    async def _save(self, action: PendingAction) -> None:
        r = await self._get_redis()
        key = f"confirm:{action.action_id}"
        payload = json.dumps(action.to_dict())
        if r:
            await r.setex(key, _CONFIRMATION_TTL, payload)
        else:
            _IN_PROCESS_STORE[key] = action.to_dict()

    async def _load(self, action_id: str) -> Optional[PendingAction]:
        r = await self._get_redis()
        key = f"confirm:{action_id}"
        if r:
            raw = await r.get(key)
        else:
            raw = _IN_PROCESS_STORE.get(key)
            if isinstance(raw, dict):
                raw = json.dumps(raw)
        if not raw:
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        return PendingAction.from_dict(data)

    async def _delete(self, action_id: str) -> None:
        r = await self._get_redis()
        key = f"confirm:{action_id}"
        if r:
            await r.delete(key)
        else:
            _IN_PROCESS_STORE.pop(key, None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def request_confirmation(
        self,
        shop_id: str,
        sender_phone: str,
        tool_name: str,
        tool_params: Dict[str, Any],
        prompt_text: str,
    ) -> PendingAction:
        """Create a pending action and raise ConfirmationRequired.

        The caller (BaseTool._check_confirmation) catches this exception and
        sends the prompt_text to the user. The agent loop must pause here.
        """
        action_id = _make_action_id(tool_name, shop_id, sender_phone, tool_params)
        action = PendingAction(
            action_id=action_id,
            shop_id=shop_id,
            sender_phone=sender_phone,
            tool_name=tool_name,
            tool_params=tool_params,
            prompt_text=prompt_text,
        )
        await self._save(action)

        logger.info(
            "Confirmation requested.",
            extra={"action_id": action_id, "tool": tool_name, "sender": sender_phone},
        )
        raise ConfirmationRequired(
            message="Confirmation required before executing this action.",
            pending_action_id=action_id,
            prompt_text=prompt_text,
        )

    async def process_reply(
        self, action_id: str, reply_text: str
    ) -> tuple[ConfirmationStatus, Optional[PendingAction]]:
        """Process the user's Haan/Nahi reply.

        Returns (new_status, action) — action is None if not found/expired.
        """
        action = await self._load(action_id)
        if action is None:
            return ConfirmationStatus.EXPIRED, None

        if action.status != ConfirmationStatus.PENDING_CONFIRMATION:
            return action.status, action

        if is_confirmation_yes(reply_text):
            action.status = ConfirmationStatus.CONFIRMED
        elif is_confirmation_no(reply_text):
            action.status = ConfirmationStatus.REJECTED
            await self._delete(action_id)
        else:
            # Not a clear yes/no — keep pending
            return action.status, action

        await self._save(action)
        logger.info(
            "Confirmation reply processed.",
            extra={"action_id": action_id, "status": action.status.value},
        )
        return action.status, action

    async def mark_executed(self, action_id: str) -> None:
        """Mark a confirmed action as executed (prevents re-execution)."""
        action = await self._load(action_id)
        if action:
            action.status = ConfirmationStatus.EXECUTED
            await self._save(action)

    async def get_pending(self, action_id: str) -> Optional[PendingAction]:
        return await self._load(action_id)


# Singleton
confirmation_engine = ConfirmationEngine()
