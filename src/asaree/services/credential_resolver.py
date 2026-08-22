"""The resolver installed with motoro.services.credentials.set_credential_resolver.

Per the module's own docstring, this is called with the run's ``owner_id`` as
``principal_id`` (``execute_run`` defaults to it when no override is given) —
so setting ``owner_id=user.id`` at ``create_run`` time is what makes this
resolve to the *right* user's key with no further plumbing.

Scoped to anthropic/openai/azure_foundry/openrouter/local — the providers
this deployment actually needs. bedrock still returns ``None`` (defer to
core's env-based default) rather than half-implementing its
region-derivation logic for a provider nothing here uses yet.
"""

from __future__ import annotations

import uuid
from typing import Any

from motoro.services.credentials import foundry_api_base
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from asaree.models.database import get_engine
from asaree.services.user_llm_settings import decrypt_api_key, get_setting

SUPPORTED_PROVIDERS = frozenset({"anthropic", "openai", "azure_foundry", "openrouter", "local"})


class LLMCredentialNotConfiguredError(RuntimeError):
    """Raised when a run needs a provider this resolver owns, but the user has no saved credential for it.

    ASAREE deliberately has no server-environment fallback for these three
    providers (matching ARES's own M112 decision) -- returning ``None`` here
    would let ``motoro.services.credentials.resolve`` fall through to
    ``config.api_key`` (always unset for ASAREE's flow), which litellm itself
    would then try to satisfy by reading ANTHROPIC_API_KEY/OPENAI_API_KEY
    straight from the process environment. That's a silently-shared server
    key wearing a per-user credential's clothes. Raising here instead
    propagates up through execute_run() and is caught by protocol_execution.py's
    existing `except Exception` boundaries, which already turn it into a clean
    run.error message -- no new plumbing needed.
    """


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
            raise LLMCredentialNotConfiguredError(
                f"No {provider} credential configured for this user. Add one under LLM Settings before running "
                f"an agent that uses {provider}."
            )

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

        if provider == "local":
            # No fixed default host (there's no one address a self-hosted
            # server lives at) -- api_base is required, same as
            # azure_foundry's resource above. Routed through litellm's
            # generic `openai/` provider, same as Motoro's own env resolver
            # (services.credentials.env_credential_resolver) -- see that
            # module for why "local" needs this and azure_foundry needs
            # `azure_ai/` instead.
            base = setting.api_base or getattr(config, "api_base", None)
            if not base:
                raise ValueError(
                    "local credential has no api_base -- there's no default host for a self-hosted "
                    "server. Set api_base via PUT /llm-settings."
                )
            model = getattr(config, "model", "") or ""
            return {
                "model": f"openai/{model}",
                # Most self-hosted OpenAI-compatible servers don't check the
                # key, but litellm's OpenAI client still requires a non-empty
                # string -- the credential's own key is optional for exactly
                # this reason.
                "api_key": decrypt_api_key(setting) or "not-needed",
                "api_base": base,
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
