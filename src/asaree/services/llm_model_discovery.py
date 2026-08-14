"""Which models a user can actually pick for a given provider/credential.

Anthropic/OpenAI's own catalogs are curated and provider-wide -- they don't
vary per user, so agentic_core.services.model_capabilities.CATALOG (already
hand-maintained there specifically because litellm's own dynamic capability
data is unreliable for newer models -- see that module's own docstring) is
the source of truth for those two. No live call, no credential needed.

Azure Foundry deployments are the opposite: per-resource, so nothing short
of asking that resource can know what's actually deployed there. Mirrors
ARES's own proven approach (user_llm_settings_service.py's _discover_azure):
the classic Azure OpenAI "list deployments" endpoint, plain api-key header
auth (no Azure AD/OAuth2/app-registration -- the newer AI Foundry
"deployments" API needs that, and ASAREE's credential shape has nowhere to
put it), raw httpx (no Azure SDK, no litellm involvement).
"""

from __future__ import annotations

import logging

import httpx
from agentic_core.services.credentials import foundry_api_base
from agentic_core.services.model_capabilities import CATALOG, ModelCapabilities, get_capabilities

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services.user_llm_settings import decrypt_api_key

logger = logging.getLogger(__name__)

_DEPLOYMENTS_API_VERSION = "2024-10-21"
_REQUEST_TIMEOUT_SECONDS = 10.0


class ModelInfo:
    def __init__(self, *, id: str, label: str | None, capabilities: ModelCapabilities) -> None:  # noqa: A002 -- matches the API response field name
        self.id = id
        self.label = label
        self.capabilities = capabilities


async def discover_models(*, provider: str, setting: UserLLMSetting | None) -> tuple[list[ModelInfo], str, str | None]:
    """Returns ``(models, source, note)`` -- ``source`` is ``"static"`` for
    the curated Anthropic/OpenAI catalog, ``"api"`` for a live Azure Foundry
    discovery that succeeded, or ``"error"`` (with ``note`` explaining why)
    when Azure discovery couldn't run at all."""
    if provider in ("anthropic", "openai"):
        models = [
            ModelInfo(id=entry.model, label=entry.label, capabilities=entry.capabilities)
            for entry in CATALOG
            if entry.provider == provider
        ]
        return models, "static", None

    if provider == "azure_foundry":
        if setting is None or not setting.api_base:
            return [], "error", "Set up a credential with a resource name first."
        return await _discover_azure(setting)

    return [], "error", f"Model discovery isn't supported for provider {provider!r}."


async def _discover_azure(setting: UserLLMSetting) -> tuple[list[ModelInfo], str, str | None]:
    base = foundry_api_base(setting.api_base)
    api_key = decrypt_api_key(setting)
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{base}/openai/deployments",
                params={"api-version": _DEPLOYMENTS_API_VERSION},
                headers={"api-key": api_key},
            )
            response.raise_for_status()
        data = response.json().get("data") or []
    except httpx.HTTPError as e:
        # Never leak the raw key if it ended up embedded in a request/response repr.
        message = str(e).replace(api_key, "***")
        logger.warning("azure_foundry_model_discovery_failed", extra={"error": message})
        return [], "error", f"Could not reach Azure Foundry to list models: {message}"

    models = sorted(
        (ModelInfo(id=d["id"], label=None, capabilities=get_capabilities(d["id"])) for d in data if d.get("id")),
        key=lambda m: m.id,
    )
    return models, "api", None
