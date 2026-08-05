"""Storing and reading per-user LLM provider credentials."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.security.encryption import decrypt, encrypt


async def get_setting(db: AsyncSession, *, user_id: uuid.UUID, provider: str) -> UserLLMSetting | None:
    return (
        await db.execute(
            select(UserLLMSetting).where(UserLLMSetting.user_id == user_id, UserLLMSetting.provider == provider)
        )
    ).scalar_one_or_none()


async def list_settings(db: AsyncSession, *, user_id: uuid.UUID) -> list[UserLLMSetting]:
    return list((await db.execute(select(UserLLMSetting).where(UserLLMSetting.user_id == user_id))).scalars().all())


async def upsert_setting(
    db: AsyncSession, *, user_id: uuid.UUID, provider: str, api_key: str, api_base: str | None = None
) -> UserLLMSetting:
    """Create or replace the credential for this (user, provider) pair."""
    existing = await get_setting(db, user_id=user_id, provider=provider)
    if existing is not None:
        existing.api_key_encrypted = encrypt(api_key)
        existing.api_base = api_base
        await db.flush()
        await db.refresh(existing)
        return existing

    setting = UserLLMSetting(user_id=user_id, provider=provider, api_key_encrypted=encrypt(api_key), api_base=api_base)
    db.add(setting)
    await db.flush()
    await db.refresh(setting)
    return setting


def decrypt_api_key(setting: UserLLMSetting) -> str:
    return decrypt(setting.api_key_encrypted)
