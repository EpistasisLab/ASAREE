"""Password hashing. Bcrypt directly — no passlib layer for one algorithm."""

from __future__ import annotations

import bcrypt


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        # Malformed hash (e.g. empty string) — never a match, never a crash.
        return False
