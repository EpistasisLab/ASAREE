"""Password-reset token generation. Same shape as security.api_tokens: a
high-entropy random token, only its SHA-256 hash persisted."""

from __future__ import annotations

import hashlib
import secrets

TOKEN_PREFIX = "asaree_reset_"


def generate_reset_token() -> tuple[str, str]:
    """Return ``(raw_token, token_hash)``. Only the hash is ever persisted."""
    raw = TOKEN_PREFIX + secrets.token_hex(16)
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()
