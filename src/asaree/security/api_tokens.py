"""API token generation and hashing.

``secrets.token_urlsafe`` for the raw token (cryptographically random, no
dictionary-attack surface) and plain SHA-256 for the stored hash — a salt
would protect against precomputed-hash attacks on a *low-entropy* secret like
a password; it buys nothing for a 256-bit random token, so bcrypt's
deliberate slowness isn't needed here either.
"""

from __future__ import annotations

import hashlib
import secrets

TOKEN_PREFIX = "asaree_"


def generate_api_token() -> tuple[str, str]:
    """Return ``(raw_token, token_hash)``. Only the hash is ever persisted."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_api_token(raw)


def hash_api_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
