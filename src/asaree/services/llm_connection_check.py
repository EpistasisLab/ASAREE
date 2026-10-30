"""On-demand, zero-inference checks through Motoro's public provider API."""

from motoro.services import providers
from motoro.services.provider_types import ConnectionCheck as ConnectionCheck

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services.credential_resolver import SUPPORTED_PROVIDERS
from asaree.services.provider_config import provider_config


async def check_connection(*, provider: str, setting: UserLLMSetting) -> ConnectionCheck:
    if provider not in SUPPORTED_PROVIDERS:
        return ConnectionCheck(
            status="unknown", detail=f"Connection checks aren't supported for provider {provider!r}.", endpoint=None
        )
    return await providers.check_connection(provider_config(provider=provider, setting=setting))
