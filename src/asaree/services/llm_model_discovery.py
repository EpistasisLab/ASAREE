"""Adapt application-owned credentials to Motoro's public model discovery."""

from motoro.services import providers
from motoro.services.provider_types import ModelInfo as ModelInfo

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services.credential_resolver import SUPPORTED_PROVIDERS
from asaree.services.provider_config import provider_config


async def discover_models(*, provider: str, setting: UserLLMSetting | None) -> tuple[list[ModelInfo], str, str | None]:
    """Keep the application's cache interface; Motoro owns discovery policy."""
    if provider not in SUPPORTED_PROVIDERS:
        return [], "error", f"Model discovery isn't supported for provider {provider!r}."
    result = await providers.discover_models(provider_config(provider=provider, setting=setting))
    return result.models, result.source, result.note
