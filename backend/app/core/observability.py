"""Observability & Metrics — MODULE 24.

In-process metrics store that tracks all key operational signals described
in prompt.md §28.  No external dependency required for basic operation —
counters and histograms are held in memory and exposed via GET /metrics.

Integrations:
  • GET /metrics  — JSON snapshot of all counters / histograms
  • MetricsMiddleware — records per-request HTTP latency
  • record_llm_call() — called by AgentService after every LLM round-trip
  • record_tool_call() — called by BaseTool after every tool execution
  • record_rag_call() — called by RAGRetriever after every retrieval

Thread-safety: all mutations go through a threading.Lock so the in-process
store is safe for multi-threaded WSGI.  For async Uvicorn workers the GIL is
sufficient, but the lock is kept for correctness.

Prometheus export (optional):
  If PROMETHEUS_ENABLED=true in env, metrics are also exposed in Prometheus
  text format at GET /metrics?format=prometheus.  Requires the `prometheus-client`
  package to be installed; falls back gracefully if not available.
"""

import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Histogram helper (simple bucketed distribution)
# ---------------------------------------------------------------------------

class Histogram:
    """Lightweight latency histogram with configurable buckets (ms)."""

    DEFAULT_BUCKETS = [10, 50, 100, 250, 500, 1000, 2500, 5000, 10000]

    def __init__(self, buckets: Optional[List[float]] = None) -> None:
        self.buckets = sorted(buckets or self.DEFAULT_BUCKETS)
        self._lock = threading.Lock()
        self._counts = [0] * (len(self.buckets) + 1)   # last bucket = +Inf
        self._sum = 0.0
        self._total = 0

    def observe(self, value_ms: float) -> None:
        with self._lock:
            self._sum += value_ms
            self._total += 1
            for i, b in enumerate(self.buckets):
                if value_ms <= b:
                    self._counts[i] += 1
                    return
            self._counts[-1] += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "count": self._total,
                "sum_ms": round(self._sum, 2),
                "avg_ms": round(self._sum / self._total, 2) if self._total else 0.0,
                "buckets": {
                    f"le_{b}ms": self._counts[i]
                    for i, b in enumerate(self.buckets)
                },
                "le_inf": self._counts[-1],
            }


# ---------------------------------------------------------------------------
# Central metrics store
# ---------------------------------------------------------------------------

class MetricsStore:
    """Singleton in-process metrics store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # -- HTTP --
        self.http_requests_total: int = 0
        self.http_errors_total: int = 0
        self.http_latency = Histogram()

        # -- LLM --
        self.llm_calls_total: int = 0
        self.llm_errors_total: int = 0
        self.llm_prompt_tokens_total: int = 0
        self.llm_completion_tokens_total: int = 0
        self.llm_latency = Histogram()
        # Cost estimate (USD): configurable per-token rate
        self._llm_cost_per_1k_prompt: float = 0.00059      # Groq llama-3.3-70b pricing
        self._llm_cost_per_1k_completion: float = 0.00079

        # -- Tools --
        self.tool_calls_total: Dict[str, int] = defaultdict(int)
        self.tool_errors_total: Dict[str, int] = defaultdict(int)
        self.tool_latency: Dict[str, Histogram] = defaultdict(Histogram)

        # -- RAG --
        self.rag_calls_total: int = 0
        self.rag_errors_total: int = 0
        self.rag_latency = Histogram()
        self.rag_results_returned: int = 0

        # -- WhatsApp --
        self.whatsapp_messages_received: int = 0
        self.whatsapp_messages_sent: int = 0
        self.whatsapp_send_errors: int = 0
        self.whatsapp_duplicate_messages: int = 0

        # -- Business flow --
        self.confirmations_requested: int = 0
        self.confirmations_accepted: int = 0
        self.confirmations_rejected: int = 0
        self.approvals_requested: int = 0
        self.approvals_accepted: int = 0
        self.approvals_rejected: int = 0
        self.human_handoffs_total: int = 0
        self.idempotency_blocks_total: int = 0

        # -- DB --
        self.db_latency = Histogram()

        # Store start time
        self._started_at = time.time()

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def record_http_request(self, latency_ms: float, is_error: bool = False) -> None:
        with self._lock:
            self.http_requests_total += 1
            if is_error:
                self.http_errors_total += 1
        self.http_latency.observe(latency_ms)

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------

    def record_llm_call(
        self,
        latency_ms: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        is_error: bool = False,
    ) -> None:
        with self._lock:
            self.llm_calls_total += 1
            if is_error:
                self.llm_errors_total += 1
            self.llm_prompt_tokens_total += prompt_tokens
            self.llm_completion_tokens_total += completion_tokens
        self.llm_latency.observe(latency_ms)

    @property
    def llm_estimated_cost_usd(self) -> float:
        prompt_cost = (self.llm_prompt_tokens_total / 1000) * self._llm_cost_per_1k_prompt
        comp_cost = (self.llm_completion_tokens_total / 1000) * self._llm_cost_per_1k_completion
        return round(prompt_cost + comp_cost, 6)

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def record_tool_call(
        self, tool_name: str, latency_ms: float, is_error: bool = False
    ) -> None:
        with self._lock:
            self.tool_calls_total[tool_name] += 1
            if is_error:
                self.tool_errors_total[tool_name] += 1
        self.tool_latency[tool_name].observe(latency_ms)

    # ------------------------------------------------------------------
    # RAG
    # ------------------------------------------------------------------

    def record_rag_call(
        self,
        latency_ms: float,
        results_returned: int = 0,
        is_error: bool = False,
    ) -> None:
        with self._lock:
            self.rag_calls_total += 1
            if is_error:
                self.rag_errors_total += 1
            self.rag_results_returned += results_returned
        self.rag_latency.observe(latency_ms)

    # ------------------------------------------------------------------
    # WhatsApp
    # ------------------------------------------------------------------

    def record_whatsapp_inbound(self, is_duplicate: bool = False) -> None:
        with self._lock:
            self.whatsapp_messages_received += 1
            if is_duplicate:
                self.whatsapp_duplicate_messages += 1

    def record_whatsapp_outbound(self, is_error: bool = False) -> None:
        with self._lock:
            if is_error:
                self.whatsapp_send_errors += 1
            else:
                self.whatsapp_messages_sent += 1

    # ------------------------------------------------------------------
    # Business flow
    # ------------------------------------------------------------------

    def record_confirmation_requested(self) -> None:
        with self._lock:
            self.confirmations_requested += 1

    def record_confirmation_response(self, accepted: bool) -> None:
        with self._lock:
            if accepted:
                self.confirmations_accepted += 1
            else:
                self.confirmations_rejected += 1

    def record_approval_requested(self) -> None:
        with self._lock:
            self.approvals_requested += 1

    def record_approval_response(self, accepted: bool) -> None:
        with self._lock:
            if accepted:
                self.approvals_accepted += 1
            else:
                self.approvals_rejected += 1

    def record_human_handoff(self) -> None:
        with self._lock:
            self.human_handoffs_total += 1

    def record_idempotency_block(self) -> None:
        with self._lock:
            self.idempotency_blocks_total += 1

    def record_db_query(self, latency_ms: float) -> None:
        self.db_latency.observe(latency_ms)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        uptime_s = round(time.time() - self._started_at, 1)
        total_tool_calls = sum(self.tool_calls_total.values())
        total_tool_errors = sum(self.tool_errors_total.values())

        confirmation_rate = (
            round(self.confirmations_requested / max(self.llm_calls_total, 1), 4)
            if self.llm_calls_total else 0.0
        )
        handoff_rate = (
            round(self.human_handoffs_total / max(self.whatsapp_messages_received, 1), 4)
            if self.whatsapp_messages_received else 0.0
        )
        duplicate_rate = (
            round(
                self.whatsapp_duplicate_messages / max(self.whatsapp_messages_received, 1), 4
            )
            if self.whatsapp_messages_received else 0.0
        )

        return {
            "uptime_seconds": uptime_s,
            "http": {
                "requests_total": self.http_requests_total,
                "errors_total": self.http_errors_total,
                "latency": self.http_latency.snapshot(),
            },
            "llm": {
                "calls_total": self.llm_calls_total,
                "errors_total": self.llm_errors_total,
                "prompt_tokens_total": self.llm_prompt_tokens_total,
                "completion_tokens_total": self.llm_completion_tokens_total,
                "estimated_cost_usd": self.llm_estimated_cost_usd,
                "latency": self.llm_latency.snapshot(),
            },
            "tools": {
                "calls_total": total_tool_calls,
                "errors_total": total_tool_errors,
                "by_tool": {
                    name: {
                        "calls": self.tool_calls_total[name],
                        "errors": self.tool_errors_total.get(name, 0),
                        "latency": self.tool_latency[name].snapshot(),
                    }
                    for name in self.tool_calls_total
                },
            },
            "rag": {
                "calls_total": self.rag_calls_total,
                "errors_total": self.rag_errors_total,
                "results_returned_total": self.rag_results_returned,
                "latency": self.rag_latency.snapshot(),
            },
            "database": {
                "latency": self.db_latency.snapshot(),
            },
            "whatsapp": {
                "messages_received": self.whatsapp_messages_received,
                "messages_sent": self.whatsapp_messages_sent,
                "send_errors": self.whatsapp_send_errors,
                "duplicate_messages": self.whatsapp_duplicate_messages,
                "duplicate_rate": duplicate_rate,
            },
            "business_flow": {
                "confirmations_requested": self.confirmations_requested,
                "confirmations_accepted": self.confirmations_accepted,
                "confirmations_rejected": self.confirmations_rejected,
                "confirmation_rate": confirmation_rate,
                "approvals_requested": self.approvals_requested,
                "approvals_accepted": self.approvals_accepted,
                "approvals_rejected": self.approvals_rejected,
                "human_handoffs_total": self.human_handoffs_total,
                "human_handoff_rate": handoff_rate,
                "idempotency_blocks_total": self.idempotency_blocks_total,
            },
        }


# Singleton
metrics = MetricsStore()
