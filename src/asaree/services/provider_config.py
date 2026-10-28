"""Translate saved application credentials without provider routing or I/O."""

from typing import Literal, cast
from urllib.parse import urlsplit

from motoro.services.provider_types import ProviderConfig

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.security.encryption import decrypt


class ProviderConfigurationError(ValueError):
    """Safe application error; never includes a credential or invalid input."""


def normalize_optional_endpoint(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def make_provider_config(
    *, provider: str, api_key: str | None, api_base: str | None = None, azure_project_endpoint: str | None = None
) -> ProviderConfig:
    try:
        config = ProviderConfig(
            provider=cast(Literal["anthropic", "openai", "azure_foundry", "openrouter", "local"], provider),
            api_key=api_key,
            api_base=normalize_optional_endpoint(api_base),
            azure_project_endpoint=normalize_optional_endpoint(azure_project_endpoint),
        )
        # urllib's port validation is lazy. Reject malformed ports before a
        # provider HTTP client attempts to construct a request from the URL.
        for endpoint in (config.api_base, config.azure_project_endpoint):
            if endpoint:
                _ = urlsplit(endpoint).port
        return config
    except ValueError:
        raise ProviderConfigurationError(
            "Invalid provider configuration. Check the saved endpoints; credentials must be supplied separately."
        ) from None


def provider_config(
    *, provider: str, setting: UserLLMSetting | None, fallback_api_base: str | None = None
) -> ProviderConfig:
    return make_provider_config(
        provider=provider,
        api_key=decrypt(setting.api_key_encrypted) if setting is not None else None,
        api_base=normalize_optional_endpoint(setting.api_base if setting is not None else None)
        or normalize_optional_endpoint(fallback_api_base),
        azure_project_endpoint=setting.azure_project_endpoint if setting is not None else None,
    )
