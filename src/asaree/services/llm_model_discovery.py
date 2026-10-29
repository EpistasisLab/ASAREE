"""Adapt application-owned credentials to Motoro's public model discovery."""

from motoro.services import providers
from motoro.services.provider_types import ModelInfo as ModelInfo
from motoro.services.provider_types import ModelListing, ProviderError

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services.credential_resolver import SUPPORTED_PROVIDERS
from asaree.services.provider_config import provider_config


async def discover_models(*, provider: str, setting: UserLLMSetting | None) -> ModelListing:
    """Preserve provider facts and structured errors for application cache policy."""
    if provider not in SUPPORTED_PROVIDERS:
        return ModelListing(
            source="error",
            note=f"Model discovery isn't supported for provider {provider!r}.",
            error=ProviderError(kind="unsupported"),
        )
    return await providers.discover_models(provider_config(provider=provider, setting=setting))
