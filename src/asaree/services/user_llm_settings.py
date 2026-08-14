"""Storing and reading per-user LLM provider credentials."""

from __future__ import annotations

import uuid
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.security.encryption import decrypt, encrypt


def _resource_host_from_project_endpoint(project_endpoint: str) -> str:
    """The project endpoint always starts with the bare resource host
    (https://{resource}.services.ai.azure.com/api/projects/{project}) --
    this is a decomposition of an explicit URL the user already gave us, not
    a guess, unlike deriving a host from a bare resource *name* alone."""
    parsed = urlparse(project_endpoint)
    return f"{parsed.scheme}://{parsed.netloc}"


async def get_setting(db: AsyncSession, *, user_id: uuid.UUID, provider: str) -> UserLLMSetting | None:
    return (
        await db.execute(
            select(UserLLMSetting).where(UserLLMSetting.user_id == user_id, UserLLMSetting.provider == provider)
        )
    ).scalar_one_or_none()


async def list_settings(db: AsyncSession, *, user_id: uuid.UUID) -> list[UserLLMSetting]:
    return list((await db.execute(select(UserLLMSetting).where(UserLLMSetting.user_id == user_id))).scalars().all())


async def upsert_setting(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    provider: str,
    api_key: str,
    api_base: str | None = None,
    azure_project_endpoint: str | None = None,
) -> UserLLMSetting:
    """Create or replace the credential for this (user, provider) pair.

    azure_foundry only: when a project endpoint is given, api_base is always
    derived from it (never taken separately) -- the project endpoint is the
    single field the GUI now asks for, since it already contains the
    resource host as its own prefix and asking for both looked like two
    near-identical URLs with no visible reason to differ.
    """
    if provider == "azure_foundry" and azure_project_endpoint:
        api_base = _resource_host_from_project_endpoint(azure_project_endpoint)

    existing = await get_setting(db, user_id=user_id, provider=provider)
    if existing is not None:
        existing.api_key_encrypted = encrypt(api_key)
        existing.api_base = api_base
        existing.azure_project_endpoint = azure_project_endpoint
        await db.flush()
        await db.refresh(existing)
        return existing

    setting = UserLLMSetting(
        user_id=user_id,
        provider=provider,
        api_key_encrypted=encrypt(api_key),
        api_base=api_base,
        azure_project_endpoint=azure_project_endpoint,
    )
    db.add(setting)
    await db.flush()
    await db.refresh(setting)
    return setting


async def delete_setting(db: AsyncSession, *, user_id: uuid.UUID, provider: str) -> bool:
    """Delete the credential for this (user, provider) pair. Returns whether one existed."""
    existing = await get_setting(db, user_id=user_id, provider=provider)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True


def decrypt_api_key(setting: UserLLMSetting) -> str:
    return decrypt(setting.api_key_encrypted)
