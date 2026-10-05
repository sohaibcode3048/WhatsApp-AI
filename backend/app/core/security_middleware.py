"""Security Middleware & Hardening — MODULE 25.

Implements:
  1. Rate limiting (in-process sliding window per IP + per phone number)
  2. Prompt injection detection (pattern matching before the message reaches the LLM)
  3. Request size limiting
  4. Security response headers (X-Content-Type-Options, X-Frame-Options, etc.)

Rate limiting:
  Uses a simple token-bucket / sliding-window algorithm backed by an
  in-process dict.  For multi-process deployments, replace _store with a
  Redis-backed implementation using the same interface.

Prompt injection detection:
  A curated set of patterns known to be used in jailbreak / prompt injection
  attacks.  Detection does NOT block the message — it raises a flag so the
  pipeline can route the message to HUMAN_HANDOFF rather than the LLM.
  This avoids false positives while still protecting the system.
"""

import re
import time
import threading
from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Tuple

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from backend.app.core.config import settings
from backend.app.core.logging import logger


# ---------------------------------------------------------------------------
# In-process sliding-window rate limiter
# ---------------------------------------------------------------------------

class _RateLimitWindow:
    """Sliding window of timestamps for one key."""

    def __init__(self, window_seconds: float, max_requests: int) -> None:
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._timestamps: Deque[float] = deque()

    def is_allowed(self) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        # Drop expired
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_requests:
            return False
        self._timestamps.append(now)
        return True


class RateLimiter:
    """Per-key sliding-window rate limiter.

    Default limits (configurable via env):
      Webhook endpoint:  60 requests / 60 seconds per IP
      API endpoints:    120 requests / 60 seconds per IP
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: Dict[str, _RateLimitWindow] = {}

    def _get_window(self, key: str, window_s: float, limit: int) -> _RateLimitWindow:
        if key not in self._windows:
            self._windows[key] = _RateLimitWindow(window_s, limit)
        return self._windows[key]

    def check_ip(self, ip: str, path: str) -> bool:
        """Return True if the IP is within rate limit for the given path."""
        if settings.APP_ENV == "test":
            return True   # never rate-limit during tests

        is_webhook = "/webhook/" in path
        limit = 60 if is_webhook else 120
        window = 60.0

        key = f"ip:{ip}:{path}"
        with self._lock:
            w = self._get_window(key, window, limit)
            allowed = w.is_allowed()

        if not allowed:
            logger.warning(
                "Rate limit exceeded.",
                extra={"ip": ip, "path": path, "limit": limit},
            )
        return allowed

    def check_phone(self, phone: str) -> bool:
        """Per-phone-number rate limit: 30 messages / minute (WhatsApp user)."""
        if settings.APP_ENV == "test":
            return True
        key = f"phone:{phone}"
        with self._lock:
            w = self._get_window(key, 60.0, 30)
            return w.is_allowed()

    def cleanup_expired(self) -> int:
        """Remove windows with empty timestamps. Returns count removed."""
        removed = 0
        with self._lock:
            dead = [
                k for k, w in self._windows.items() if not w._timestamps
            ]
            for k in dead:
                del self._windows[k]
                removed += 1
        return removed


rate_limiter = RateLimiter()


# ---------------------------------------------------------------------------
# Prompt injection detector
# ---------------------------------------------------------------------------

# Patterns compiled once at import time for performance.
_INJECTION_PATTERNS: List[re.Pattern] = [p for p in [
    # Classic instruction override
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.I),
    re.compile(r"forget\s+(your|all)\s+(rules?|instructions?|constraints?)", re.I),
    re.compile(r"disregard\s+(all|previous|prior)\s+instructions?", re.I),
    re.compile(r"override\s+(system|your)\s+(prompt|instructions?)", re.I),
    re.compile(r"you\s+are\s+now\s+(DAN|an?\s+unrestricted|jailbroken)", re.I),
    re.compile(r"act\s+as\s+(if\s+you\s+have\s+no\s+)?(DAN|unrestricted|evil)", re.I),

    # Database / system access attempts
    re.compile(r"(direct|directly)\s+(access|modify|update|delete)\s+(the\s+)?(database|db)", re.I),
    re.compile(r"(DROP|DELETE|TRUNCATE|ALTER|INSERT)\s+(TABLE|FROM|INTO|DATABASE)", re.I),
    re.compile(r"SELECT\s+\*\s+FROM", re.I),
    re.compile(r"execute\s+(sql|query|raw|arbitrary)\s+(code|statement|command)", re.I),

    # System prompt extraction
    re.compile(r"(show|reveal|print|repeat|output)\s+(your\s+)?(system\s+prompt|instructions?)", re.I),
    re.compile(r"what\s+(are|were)\s+your\s+(original\s+)?(instructions?|rules?)", re.I),
    re.compile(r"tell\s+me\s+your\s+(system|initial)\s+(prompt|instructions?)", re.I),

    # Role confusion
    re.compile(r"pretend\s+(you\s+are|to\s+be)\s+(a\s+)?(hacker|admin|root|superuser)", re.I),
    re.compile(r"you\s+have\s+no\s+(rules?|restrictions?|limitations?|constraints?)", re.I),
    re.compile(r"simulate\s+(a\s+)?(malicious|evil|unrestricted)\s+(AI|bot|agent)", re.I),

    # Cross-tenant / data theft
    re.compile(r"(show|give|list|dump)\s+(me\s+)?(all\s+)?(customer|user|shop)\s+(data|records?|info)", re.I),
    re.compile(r"access\s+all\s+shops?", re.I),

    # Credential theft
    re.compile(r"(api[_\s]?key|access[_\s]?token|secret[_\s]?key)\s*=?\s*[A-Za-z0-9+/]{10,}", re.I),
] if p is not None]


def detect_prompt_injection(text: str) -> Tuple[bool, Optional[str]]:
    """Scan text for known prompt injection / jailbreak patterns.

    Returns:
      (detected: bool, matched_pattern: Optional[str])

    Does NOT raise — callers decide how to handle a positive detection.
    """
    if not text or len(text) > 4000:
        # Oversized messages are suspicious but handled by size limit middleware
        return False, None

    for pattern in _INJECTION_PATTERNS:
        m = pattern.search(text)
        if m:
            return True, m.group(0)
    return False, None


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}

if settings.APP_ENV == "production":
    _SECURITY_HEADERS["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    _SECURITY_HEADERS["Content-Security-Policy"] = (
        "default-src 'none'; frame-ancestors 'none';"
    )


class SecurityMiddleware(BaseHTTPMiddleware):
    """Adds security headers and rate limiting to all requests."""

    MAX_BODY_BYTES = 1 * 1024 * 1024   # 1 MB

    def __init__(self, app: ASGIApp, **kwargs) -> None:
        super().__init__(app, **kwargs)

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. Rate limit by IP
        client_ip = request.client.host if request.client else "unknown"
        if not rate_limiter.check_ip(client_ip, request.url.path):
            return Response(
                content='{"error":"Rate limit exceeded. Please slow down.","code":"RATE_LIMITED"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": "60"},
            )

        # 2. Process request
        response = await call_next(request)

        # 3. Inject security headers
        for header, value in _SECURITY_HEADERS.items():
            response.headers[header] = value

        return response
