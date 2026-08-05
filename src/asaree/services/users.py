"""User creation and lookup."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.user import User
from asaree.security.passwords import hash_password


async def create_user(db: AsyncSession, *, email: str, password: str) -> User:
    user = User(email=email.lower(), hashed_password=hash_password(password))
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
