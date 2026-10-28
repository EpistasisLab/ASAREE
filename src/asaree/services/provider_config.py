"""Translate saved application credentials without provider routing or I/O."""

from typing import Literal, cast

from motoro.services.provider_types import ProviderConfig

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services.user_llm_settings import decrypt_api_key


def provider_config(
    *, provider: str, setting: UserLLMSetting | None, fallback_api_base: str | None = None
) -> ProviderConfig:
    return ProviderConfig(
        provider=cast(Literal["anthropic", "openai", "azure_foundry", "openrouter", "local"], provider),
        api_key=decrypt_api_key(setting) if setting is not None else None,
        api_base=(setting.api_base if setting is not None else None) or fallback_api_base,
        azure_project_endpoint=setting.azure_project_endpoint if setting is not None else None,
    )
