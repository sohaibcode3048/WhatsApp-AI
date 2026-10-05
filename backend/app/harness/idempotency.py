"""Idempotency Engine — MODULE 17.

Prevents duplicate execution of the same tool call (e.g. when WhatsApp
delivers the same message twice).

Strategy
--------
  1. Compute a deterministic SHA-256 key from: tool_name + shop_id + sender_phone + params
  2. Check Redis/in-process store for a prior result under that key
  3. If found → return the cached ToolResult immediately (no DB work)
  4. If not found → execute the tool → store result with TTL → return

The idempotency_key is attached to ToolInput by the agent before calling
BaseTool.execute() so that the key is computed once and logged for auditing.
"""

import json
from typing import Any, Dict, Optional

from backend.app.core.config import settings
from backend.app.core.exceptions import IdempotencyConflictError
from backend.app.core.logging import logger
from backend.app.core.security import compute_idempotency_hash

_IDEMPOTENCY_TTL = 3600   # 1 hour
_IN_PROCESS_STORE: Dict[str, Dict] = {}


def compute_tool_idempotency_key(
    tool_name: str,
    shop_id: str,
    sender_phone: str,
    params: Dict[str, Any],
) -> str:
    """Deterministic key for this exact tool invocation."""
    params_str = json.dumps(params, sort_keys=True, default=str)
    return compute_idempotency_hash(tool_name, shop_id, sender_phone, params_str)


class IdempotencyEngine:
    """Check and record idempotency for tool executions."""

    def __init__(self) -> None:
        self._redis = None

    async def _get_redis(self):
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

    async def check(self, key: str) -> Optional[Dict[str, Any]]:
        """Return a previously cached result dict, or None if first execution."""
        r = await self._get_redis()
        redis_key = f"idem:{key}"
        if r:
            raw = await r.get(redis_key)
            return json.loads(raw) if raw else None
        raw = _IN_PROCESS_STORE.get(redis_key)
        return raw

    async def record(self, key: str, result: Dict[str, Any]) -> None:
        """Cache the result of a successful tool execution."""
        r = await self._get_redis()
        redis_key = f"idem:{key}"
        payload = json.dumps(result, default=str)
        if r:
            await r.setex(redis_key, _IDEMPOTENCY_TTL, payload)
        else:
            _IN_PROCESS_STORE[redis_key] = result

    async def check_and_raise_if_duplicate(
        self, key: str, tool_name: str
    ) -> None:
        """Raise IdempotencyConflictError if this key was already executed."""
        existing = await self.check(key)
        if existing:
            logger.info(
                "Idempotent duplicate detected — skipping execution.",
                extra={"key": key[:16], "tool": tool_name},
            )
            raise IdempotencyConflictError(
                message=f"Tool '{tool_name}' was already executed with these parameters.",
                details={"idempotency_key": key[:16], "cached_result": existing},
            )


# Singleton
idempotency_engine = IdempotencyEngine()
