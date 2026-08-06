"""The resolver installed with agentic_core.services.credentials.set_credential_resolver.

Per the module's own docstring, this is called with the run's ``owner_id`` as
``principal_id`` (``execute_run`` defaults to it when no override is given) —
so setting ``owner_id=user.id`` at ``create_run`` time is what makes this
resolve to the *right* user's key with no further plumbing.

Scoped to anthropic/openai/azure_foundry — the providers this deployment
actually needs. bedrock still returns ``None`` (defer to core's env-based
default) rather than half-implementing its region-derivation logic for a
provider nothing here uses yet.
"""

from __future__ import annotations

import uuid
from typing import Any

from agentic_core.services.credentials import foundry_api_base
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from asaree.models.database import get_engine
from asaree.services.user_llm_settings import decrypt_api_key, get_setting

SUPPORTED_PROVIDERS = frozenset({"anthropic", "openai", "azure_foundry"})


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

        if provider == "azure_foundry":
            # api_base here is the Foundry *resource name or URL* the user
            # pasted into their setting — foundry_api_base derives/normalizes
            # the actual base URL from it, same as core's own env resolver.
            resource = setting.api_base or getattr(config, "api_base", None)
            if not resource:
                raise ValueError(
                    "azure_foundry credential has no api_base — the base URL is derived "
                    "from the Foundry resource name, so a key alone is not enough. Set "
                    "api_base via PUT /llm-settings."
                )
            model = getattr(config, "model", "") or ""
            return {
                "model": f"azure_ai/{model}",
                "api_key": decrypt_api_key(setting),
                "api_base": foundry_api_base(resource),
                "api_version": None,
                "aws_region_name": None,
            }

        return {
            "model": None,
            "api_key": decrypt_api_key(setting),
            "api_base": setting.api_base or getattr(config, "api_base", None),
            "api_version": None,
            "aws_region_name": None,
        }
