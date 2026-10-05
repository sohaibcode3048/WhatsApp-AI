"""Agent Core Service — MODULE 19.

Architecture
------------
  LLMProvider (ABC)    — vendor-agnostic interface
  GroqProvider         — Groq API implementation (llama-3.3-70b-versatile)
  MockLLMProvider      — deterministic stub for tests
  AgentService         — orchestrates: RAG → prompt → LLM → tool dispatch → response

The LLM is UNTRUSTED. Every tool call it emits must pass through the
10-stage BaseTool harness before touching the database.

Tool-calling flow
-----------------
  1.  AgentService receives a message + context
  2.  Retrieves relevant RAG chunks (knowledge queries only)
  3.  Builds system prompt with tools + RAG + sender role
  4.  Calls LLMProvider.chat() — gets back either:
        a. A plain text response (no tool needed)
        b. A tool_call with name + arguments
  5.  If tool_call: dispatches to tool_registry.get(name).execute(args)
  6.  Feeds tool result back to LLM for final response
  7.  Returns the final response string
"""

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from backend.app.core.config import settings
from backend.app.core.exceptions import LLMError
from backend.app.core.logging import logger


# ---------------------------------------------------------------------------
# LLM message / response schemas
# ---------------------------------------------------------------------------

@dataclass
class LLMMessage:
    role: str          # "system" | "user" | "assistant" | "tool"
    content: str
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMResponse:
    content: Optional[str]
    tool_calls: List[ToolCall] = field(default_factory=list)
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


# ---------------------------------------------------------------------------
# LLMProvider abstraction
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Abstract LLM provider — swap Groq for OpenAI / Anthropic without changing agent."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def chat(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> LLMResponse: ...


# ---------------------------------------------------------------------------
# Groq provider
# ---------------------------------------------------------------------------

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(LLMProvider):
    """Groq API provider — OpenAI-compatible chat completions."""

    @property
    def provider_name(self) -> str:
        return "groq"

    async def chat(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        api_key = settings.GROQ_API_KEY
        if not api_key:
            logger.warning("GROQ_API_KEY not set — returning mock LLM response.")
            return LLMResponse(
                content="[LLM not configured — set GROQ_API_KEY]",
                model=settings.GROQ_MODEL,
            )

        payload: Dict[str, Any] = {
            "model": settings.GROQ_MODEL,
            "messages": [self._fmt(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(_GROQ_CHAT_URL, json=payload, headers=headers)
            except httpx.RequestError as exc:
                raise LLMError(f"Network error reaching Groq: {exc}") from exc

        latency_ms = (time.monotonic() - t0) * 1000

        if resp.status_code == 401:
            raise LLMError("Groq authentication failed — check GROQ_API_KEY.")
        if resp.status_code >= 400:
            raise LLMError(f"Groq API error {resp.status_code}: {resp.text}")

        body = resp.json()
        choice = body["choices"][0]
        msg = choice["message"]
        usage = body.get("usage", {})

        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"]["arguments"])
            except Exception:
                args = {}
            tool_calls.append(ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=args,
            ))

        return LLMResponse(
            content=msg.get("content"),
            tool_calls=tool_calls,
            model=body.get("model", settings.GROQ_MODEL),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
        )

    def _fmt(self, m: LLMMessage) -> Dict[str, Any]:
        d: Dict[str, Any] = {"role": m.role, "content": m.content or ""}
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        return d


# ---------------------------------------------------------------------------
# Mock provider for tests
# ---------------------------------------------------------------------------


class MockLLMProvider(LLMProvider):
    """Deterministic stub — returns pre-configured responses without API calls."""

    def __init__(self) -> None:
        self.canned_content: Optional[str] = "Ji, kya main aapki madad kar sakta hoon?"
        self.canned_tool_calls: List[ToolCall] = []
        self.call_count: int = 0
        self.last_messages: List[LLMMessage] = []

    @property
    def provider_name(self) -> str:
        return "mock"

    async def chat(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        self.call_count += 1
        self.last_messages = list(messages)
        return LLMResponse(
            content=self.canned_content,
            tool_calls=list(self.canned_tool_calls),
            model="mock",
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=0.5,
        )


# ---------------------------------------------------------------------------
# Tool schema builder (converts ToolRegistry metadata to Groq tool format)
# ---------------------------------------------------------------------------


def build_tool_schemas(tool_metadata: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert tool_registry.list_tools() output to OpenAI-compatible tool schemas."""
    schemas = []
    for t in tool_metadata:
        schemas.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        })
    return schemas


# ---------------------------------------------------------------------------
# AgentService
# ---------------------------------------------------------------------------


class AgentService:
    """Orchestrates the full message → LLM → tool → response pipeline.

    Usage::

        agent = AgentService(provider=GroqProvider())
        response = await agent.process(
            message="Ali ka 2500 udhar likh do",
            shop_id="...",
            sender_phone="923001234567",
            sender_role="OWNER",
            session=db_session,
        )
    """

    MAX_TOOL_ROUNDS = 3   # prevent infinite tool-call loops

    def __init__(
        self,
        provider: Optional[LLMProvider] = None,
        tool_registry=None,
        rag_retriever=None,
    ) -> None:
        if provider is None:
            provider = GroqProvider()
        self._llm = provider
        self._tools = tool_registry
        self._rag = rag_retriever

    async def process(
        self,
        message: str,
        shop_id: str,
        sender_phone: str,
        sender_role: str,
        session,
        shop_language: str = "roman_urdu",
        message_type: str = "text",
    ) -> str:
        """Full pipeline: RAG → conversation history → prompt → LLM → optional tool → response."""
        from backend.app.prompts.system_prompt import build_system_prompt
        from backend.app.tools import tool_registry as default_registry

        registry = self._tools or default_registry

        # 1 — RAG retrieval
        rag_chunks = []
        if self._rag:
            try:
                rag_chunks = self._rag.retrieve(shop_id, message, n_results=3)
            except Exception as exc:
                logger.warning("RAG retrieval failed.", extra={"error": str(exc)})

        # 2 — Build prompt
        tool_meta = registry.list_tools()
        system_prompt = build_system_prompt(
            tools=tool_meta,
            sender_role=sender_role,
            shop_language=shop_language,
            rag_chunks=rag_chunks,
        )
        tool_schemas = build_tool_schemas(tool_meta)

        # 3 — Load conversation history
        history = await self._load_history(sender_phone, shop_id)

        # 4 — Assemble messages: system + history + current user message
        messages: List[LLMMessage] = [
            LLMMessage(role="system", content=system_prompt),
            *history,
            LLMMessage(role="user", content=message),
        ]

        # 5 — Agentic tool-call loop
        final_reply: str = ""
        for _round in range(self.MAX_TOOL_ROUNDS):
            llm_resp = await self._llm.chat(
                messages=messages,
                tools=tool_schemas,
                temperature=settings.LLM_TEMPERATURE,
                max_tokens=settings.LLM_MAX_TOKENS,
            )

            logger.info(
                "LLM response.",
                extra={
                    "provider": self._llm.provider_name,
                    "tokens": llm_resp.total_tokens,
                    "latency_ms": llm_resp.latency_ms,
                    "has_tool_calls": llm_resp.has_tool_calls,
                },
            )

            if not llm_resp.has_tool_calls:
                final_reply = llm_resp.content or "Kuch samajh nahi aya. Dobara try karein."
                break

            # 6 — Execute tool calls
            messages.append(LLMMessage(
                role="assistant",
                content=llm_resp.content or "",
            ))

            for tc in llm_resp.tool_calls:
                tool_result_content = await self._execute_tool(
                    tc, shop_id, sender_phone, sender_role, session, registry
                )
                messages.append(LLMMessage(
                    role="tool",
                    content=tool_result_content,
                    tool_call_id=tc.id,
                    name=tc.name,
                ))
        else:
            final_reply = "Request process karne mein masla hua. Dobara bhejein."

        # 7 — Persist conversation turn to history
        await self._save_turn(
            sender_phone=sender_phone,
            shop_id=shop_id,
            user_message=message,
            assistant_reply=final_reply,
        )

        return final_reply

    async def _execute_tool(
        self,
        tc: ToolCall,
        shop_id: str,
        sender_phone: str,
        sender_role: str,
        session,
        registry,
    ) -> str:
        """Execute one tool call through the full harness. Returns result as string."""
        try:
            tool = registry.get(tc.name)
        except Exception as exc:
            return f"Tool '{tc.name}' not found: {exc}"

        raw_input = {
            "shop_id": shop_id,
            "sender_phone": sender_phone,
            "sender_role": sender_role,
            **tc.arguments,
        }

        try:
            result = await tool.execute(raw_input, session)
            logger.info(
                "Tool executed via agent.",
                extra={"tool": tc.name, "success": result.success},
            )
            return json.dumps(result.data, default=str) if result.success else result.message
        except Exception as exc:
            logger.error(
                "Tool execution error in agent loop.",
                extra={"tool": tc.name, "error": str(exc)},
            )
            return f"Tool error: {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------
    # Conversation history (Redis-backed, with in-process fallback)
    # ------------------------------------------------------------------

    _MAX_TURNS: int = 10      # keep last N user+assistant pairs
    _HISTORY_TTL: int = 3600  # 1 hour per conversation

    def _history_key(self, sender_phone: str, shop_id: str) -> str:
        return f"history:{shop_id}:{sender_phone}"

    async def _get_redis(self):
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            await client.ping()
            return client
        except Exception:
            return None

    async def _load_history(self, sender_phone: str, shop_id: str) -> List[LLMMessage]:
        """Return up to MAX_TURNS prior message pairs from Redis."""
        key = self._history_key(sender_phone, shop_id)
        try:
            r = await self._get_redis()
            if r:
                raw = await r.get(key)
                if raw:
                    turns = json.loads(raw)
                    messages = []
                    for turn in turns[-(self._MAX_TURNS):]:
                        messages.append(LLMMessage(role="user", content=turn["user"]))
                        messages.append(LLMMessage(role="assistant", content=turn["assistant"]))
                    return messages
        except Exception as exc:
            logger.debug("History load failed (non-fatal).", extra={"error": str(exc)})
        return []

    async def _save_turn(
        self,
        sender_phone: str,
        shop_id: str,
        user_message: str,
        assistant_reply: str,
    ) -> None:
        """Append this turn to Redis conversation history."""
        key = self._history_key(sender_phone, shop_id)
        try:
            r = await self._get_redis()
            if r:
                raw = await r.get(key)
                turns: list = json.loads(raw) if raw else []
                turns.append({"user": user_message, "assistant": assistant_reply})
                # Trim to MAX_TURNS
                turns = turns[-(self._MAX_TURNS):]
                await r.setex(key, self._HISTORY_TTL, json.dumps(turns))
        except Exception as exc:
            logger.debug("History save failed (non-fatal).", extra={"error": str(exc)})
