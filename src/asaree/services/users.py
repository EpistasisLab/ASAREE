"""User creation, lookup, profile update, and password change."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.user import User
from asaree.security.passwords import hash_password


async def create_user(db: AsyncSession, *, email: str, password: str, display_name: str | None = None) -> User:
    """*display_name* defaults to the email — every user gets a non-blank
    one (see the migration's backfill for pre-existing rows), so the
    login/profile UI never has to special-case a blank name."""
    email = email.lower()
    user = User(
        email=email,
        hashed_password=hash_password(password),
        display_name=display_name or email,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()


async def record_login(db: AsyncSession, user: User) -> None:
    user.last_login_at = datetime.now(tz=UTC)
    await db.flush()


async def update_profile(
    db: AsyncSession, user: User, *, display_name: str | None = None, email: str | None = None
) -> tuple[User, bool]:
    """Apply a partial profile update. Returns ``(user, email_taken)`` —
    *email_taken* is True (and nothing is changed) if the new email belongs
    to a different account already."""
    if email is not None and email.lower() != user.email:
        existing = await get_user_by_email(db, email)
        if existing is not None and existing.id != user.id:
            return user, True
        user.email = email.lower()
    if display_name is not None:
        user.display_name = display_name
    await db.flush()
    await db.refresh(user)
    return user, False


async def set_password(db: AsyncSession, user: User, *, new_password: str) -> None:
    user.hashed_password = hash_password(new_password)
    await db.flush()
