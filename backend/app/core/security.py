"""Security utilities: HMAC signature, idempotency hashes, and JWT tokens."""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from backend.app.core.config import settings


# ---------------------------------------------------------------------------
# Meta Webhook signature
# ---------------------------------------------------------------------------


def verify_webhook_signature(payload: bytes, signature_header: str, app_secret: str) -> bool:
    """Verify Meta WhatsApp Webhook SHA256 HMAC signature.

    Format expected in signature_header: 'sha256=<hex_digest>'
    """
    if not signature_header or not app_secret:
        return False

    prefix = "sha256="
    if not signature_header.startswith(prefix):
        return False

    expected_sig = signature_header[len(prefix):].strip()
    mac = hmac.new(
        key=app_secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    )
    computed_sig = mac.hexdigest()
    return hmac.compare_digest(expected_sig, computed_sig)


# ---------------------------------------------------------------------------
# Idempotency hash
# ---------------------------------------------------------------------------


def compute_idempotency_hash(*components: str) -> str:
    """Generate deterministic SHA-256 hash from request components."""
    combined = ":".join(str(c) for c in components)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of the given plain-text password."""
    from passlib.context import CryptContext
    _pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches the bcrypt hash."""
    from passlib.context import CryptContext
    _pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    return _pwd_ctx.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------


def create_access_token(
    subject: str,
    extra_claims: Optional[Dict[str, Any]] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a signed JWT access token.

    Args:
        subject:      The ``sub`` claim — typically a user UUID or phone number.
        extra_claims: Additional claims to embed (e.g. shop_id, role).
        expires_delta: Override the default expiry from settings.

    Returns:
        Encoded JWT string.
    """
    from jose import jwt

    expire = datetime.now(timezone.utc) + (
        expires_delta
        or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload: Dict[str, Any] = {
        "sub": subject,
        "iat": datetime.now(timezone.utc),
        "exp": expire,
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(
        payload,
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT access token.

    Raises:
        jose.JWTError on any decode/validation failure — callers should
        translate this to an AuthenticationError.
    """
    from jose import jwt

    return jwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=[settings.JWT_ALGORITHM],
    )


# ---------------------------------------------------------------------------
# FastAPI dependency — extract current user from Bearer token
# ---------------------------------------------------------------------------


def get_token_from_header(authorization: Optional[str]) -> str:
    """Extract the raw token from 'Bearer <token>' header.

    Raises ValueError if the header is missing or malformed.
    """
    if not authorization:
        raise ValueError("Authorization header missing")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ValueError("Invalid Authorization header format — expected 'Bearer <token>'")
    return token
