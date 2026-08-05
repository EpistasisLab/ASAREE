"""The resolver installed with agentic_core.services.credentials.set_credential_resolver.

Per the module's own docstring, this is called with the run's ``owner_id`` as
``principal_id`` (``execute_run`` defaults to it when no override is given) —
so setting ``owner_id=user.id`` at ``create_run`` time is what makes this
resolve to the *right* user's key with no further plumbing.

Scoped to anthropic/openai for now — the two providers this deployment
actually needs. bedrock/azure_foundry return ``None`` (defer to core's
env-based default) rather than half-implementing their extra
region/resource-derivation logic for providers nothing here uses yet.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from asaree.models.database import get_engine
from asaree.services.user_llm_settings import decrypt_api_key, get_setting

SUPPORTED_PROVIDERS = frozenset({"anthropic", "openai"})


async def resolve(config: Any, principal_id: uuid.UUID | None) -> dict[str, str | None] | None:
    if principal_id is None:
        return None
    provider = getattr(getattr(config, "provider", None), "value", None)
    if provider not in SUPPORTED_PROVIDERS:
        return None

    factory = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        setting = await get_setting(db, user_id=principal_id, provider=provider)
        if setting is None:
            return None
        return {
            "model": None,
            "api_key": decrypt_api_key(setting),
            "api_base": setting.api_base or getattr(config, "api_base", None),
            "api_version": None,
            "aws_region_name": None,
        }
