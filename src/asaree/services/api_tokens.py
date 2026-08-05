"""Issuing and verifying API tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.user import User
from asaree.models.user_api_token import UserApiToken
from asaree.security.api_tokens import generate_api_token, hash_api_token


async def issue_api_token(db: AsyncSession, *, user_id: uuid.UUID, name: str) -> tuple[UserApiToken, str]:
    """Create a token and return ``(row, raw_token)``.

    *raw_token* is returned exactly once — the row never carries anything
    that reconstructs it.
    """
    raw, token_hash = generate_api_token()
    token = UserApiToken(user_id=user_id, name=name, token_hash=token_hash)
    db.add(token)
    await db.flush()
    await db.refresh(token)
    return token, raw


async def authenticate_api_key(db: AsyncSession, raw_token: str) -> User | None:
    """Resolve a raw token to its owning, active user — or ``None``."""
    token_hash = hash_api_token(raw_token)
    token = (await db.execute(select(UserApiToken).where(UserApiToken.token_hash == token_hash))).scalar_one_or_none()
    if token is None:
        return None
    token.last_used_at = datetime.now(tz=UTC)
    user = (await db.execute(select(User).where(User.id == token.user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user
