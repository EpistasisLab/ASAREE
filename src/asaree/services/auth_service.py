"""Browser-session JWTs — access + refresh token pair, Redis deny list.

Separate from ``services.api_tokens`` (the SDK/agent auth path): a browser
session is short-lived and refreshable, an API token is long-lived and
explicit-lifetime. Both ultimately resolve to the same ``User`` row (see
``deps.get_current_user``, which accepts either).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from asaree.config import get_settings
from asaree.redis_client import get_redis

_ALGORITHM = "HS256"


def create_user_tokens(user_id: uuid.UUID, email: str) -> tuple[str, str]:
    """Issue an (access_token, refresh_token) JWT pair for a user."""
    settings = get_settings()
    now = datetime.now(tz=UTC)

    access_payload = {
        "sub": str(user_id),
        "email": email,
        "type": "access",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_expiry_seconds),
    }
    access_token = jwt.encode(access_payload, settings.auth_secret_key, algorithm=_ALGORITHM)

    refresh_payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(seconds=settings.refresh_token_expiry_seconds),
    }
    refresh_token = jwt.encode(refresh_payload, settings.auth_secret_key, algorithm=_ALGORITHM)

    return access_token, refresh_token


async def validate_user_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    """Validate a user JWT — signature, expiry, type, and deny list.

    Raises ValueError (never a raw jwt exception) on anything invalid, so
    callers have one exception type to catch regardless of *why* it failed.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.auth_secret_key, algorithms=[_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise ValueError(str(exc)) from exc

    if payload.get("type") != expected_type:
        raise ValueError(f"Expected token type '{expected_type}', got '{payload.get('type')}'")

    jti = payload.get("jti")
    if jti and await _is_token_denied(jti):
        raise ValueError("Token has been revoked")

    return payload


async def deny_token(jti: str, expires_at: datetime) -> None:
    """Add a token's ``jti`` to the Redis deny list, TTL'd to its own expiry —
    a denied token needs remembering only until it would have expired anyway."""
    ttl = max(0, int((expires_at - datetime.now(tz=UTC)).total_seconds()))
    if ttl > 0:
        await get_redis().setex(f"token:deny:{jti}", ttl, "1")


async def _is_token_denied(jti: str) -> bool:
    try:
        return await get_redis().get(f"token:deny:{jti}") is not None
    except Exception:
        # Redis unavailable: fail open on deny-list checks specifically (a
        # denied token staying valid for the rest of its short access-token
        # life is a far smaller risk than locking every session out because
        # Redis hiccuped).
        return False
