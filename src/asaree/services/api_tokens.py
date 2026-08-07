"""Issuing, verifying, listing, and revoking API tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.user import User
from asaree.models.user_api_token import UserApiToken
from asaree.security.api_tokens import generate_api_token, hash_api_token

MAX_ACTIVE_TOKENS_PER_USER = 20


async def issue_api_token(
    db: AsyncSession, *, user_id: uuid.UUID, name: str, expires_in_days: int | None = None
) -> tuple[UserApiToken, str]:
    """Create a token and return ``(row, raw_token)``.

    *raw_token* is returned exactly once — the row never carries anything
    that reconstructs it.
    """
    raw, token_hash, token_prefix = generate_api_token()
    expires_at = datetime.now(tz=UTC) + timedelta(days=expires_in_days) if expires_in_days else None
    token = UserApiToken(
        user_id=user_id, name=name, token_hash=token_hash, token_prefix=token_prefix, expires_at=expires_at
    )
    db.add(token)
    await db.flush()
    await db.refresh(token)
    return token, raw


async def count_active_tokens(db: AsyncSession, *, user_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(UserApiToken)
        .where(UserApiToken.user_id == user_id, UserApiToken.is_revoked.is_(False))
    )
    return result.scalar() or 0


async def list_api_tokens(
    db: AsyncSession, *, user_id: uuid.UUID, offset: int = 0, limit: int = 20
) -> tuple[list[UserApiToken], int]:
    total = (
        await db.execute(
            select(func.count()).select_from(UserApiToken).where(UserApiToken.user_id == user_id)
        )
    ).scalar_one()
    result = await db.execute(
        select(UserApiToken)
        .where(UserApiToken.user_id == user_id)
        .order_by(UserApiToken.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all()), total


async def revoke_api_token(db: AsyncSession, *, user_id: uuid.UUID, token_id: uuid.UUID) -> bool:
    """Soft-delete a token. Returns False if it doesn't exist or belongs to
    someone else — same response either way, so this can't be used to probe
    whether a given token id exists at all."""
    result = await db.execute(
        select(UserApiToken).where(UserApiToken.id == token_id, UserApiToken.user_id == user_id)
    )
    token = result.scalar_one_or_none()
    if token is None:
        return False
    token.is_revoked = True
    await db.flush()
    return True


async def authenticate_api_key(db: AsyncSession, raw_token: str) -> User | None:
    """Resolve a raw token to its owning, active user — or ``None``.

    A revoked or expired token fails the same way an unknown one does: no
    distinct error, nothing for a caller to learn about a token that isn't
    theirs.
    """
    token_hash = hash_api_token(raw_token)
    token = (await db.execute(select(UserApiToken).where(UserApiToken.token_hash == token_hash))).scalar_one_or_none()
    if token is None or token.is_revoked:
        return None
    if token.expires_at is not None and token.expires_at < datetime.now(tz=UTC):
        return None
    token.last_used_at = datetime.now(tz=UTC)
    user = (await db.execute(select(User).where(User.id == token.user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user
