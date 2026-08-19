"""Which models a user can actually pick for a given provider/credential.

Anthropic/OpenAI's own catalogs are curated and provider-wide -- they don't
vary per user, so agentic_core.services.model_capabilities.CATALOG (already
hand-maintained there specifically because litellm's own dynamic capability
data is unreliable for newer models -- see that module's own docstring) is
the source of truth for those two. No live call, no credential needed.

Azure Foundry deployments are the opposite: per-resource, so nothing short
of asking that resource can know what's actually deployed there. The listing
call for a Foundry project's deployments (any publisher, including
Anthropic) is ``{project_endpoint}/deployments?api-version=v1`` -- confirmed
live to work with a plain ``api-key`` header, despite Microsoft's own REST
reference documenting only OAuth2 for it. It needs the *project*-scoped
endpoint (``.../api/projects/{project}``), which is a genuinely different
piece of connection info than the bare resource host used for inference --
UserLLMSetting.azure_project_endpoint, optional, since inference works fine
without it. No project endpoint means no listing; say so and let the user
type a deployment name instead.

Deliberately NOT tried here: the classic Azure OpenAI "list deployments"
endpoint (``{resource}/openai/deployments?api-version=2024-10-21``). It was
implemented and 404'd unconditionally against a real Foundry resource,
because Claude deployments were never "Azure OpenAI deployments" -- they're
served through the separate ``/anthropic`` passthrough. It has been removed
rather than kept as a fallback; don't re-add it without a resource it
actually works on.
"""

from __future__ import annotations

import logging

import httpx
from agentic_core.services.model_capabilities import CATALOG, ModelCapabilities, get_capabilities

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services.user_llm_settings import decrypt_api_key

logger = logging.getLogger(__name__)

_PROJECT_DEPLOYMENTS_API_VERSION = "v1"
_REQUEST_TIMEOUT_SECONDS = 10.0

# Public because llm_connection_check reuses it: no project endpoint is the
# one discovery failure that says nothing about whether the credential itself
# is good, so a connection check must report it as inconclusive rather than
# as a rejected key.
NO_PROJECT_ENDPOINT_NOTE = (
    "This credential has no Project endpoint, so its deployments can't be listed -- enter your deployment's "
    "name directly in the Model field below, or edit the credential to add a Project endpoint "
    "(https://{resource}.services.ai.azure.com/api/projects/{project})."
)


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
        if not setting.azure_project_endpoint:
            return [], "error", NO_PROJECT_ENDPOINT_NOTE
        return await _discover_azure_via_project(setting)

    return [], "error", f"Model discovery isn't supported for provider {provider!r}."


async def _discover_azure_via_project(setting: UserLLMSetting) -> tuple[list[ModelInfo], str, str | None]:
    endpoint = (setting.azure_project_endpoint or "").rstrip("/")
    api_key = decrypt_api_key(setting)
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{endpoint}/deployments",
                params={"api-version": _PROJECT_DEPLOYMENTS_API_VERSION},
                headers={"api-key": api_key},
            )
            response.raise_for_status()
        data = response.json().get("value") or []
    except httpx.HTTPError as e:
        message = str(e).replace(api_key, "***")
        logger.warning("azure_foundry_project_discovery_failed", extra={"error": message})
        return [], "error", f"Could not reach the Azure Foundry project to list deployments: {message}"

    # Response shape here is {"value": [{"name", "type", "modelName",
    # "modelPublisher", "modelVersion", "capabilities", "sku"}, ...]}.
    # `name` is the deployment's own name (what a real inference call must
    # pass as `model`); `modelName` is the underlying published model id, a
    # better key for capability lookup and a more useful label when the two
    # differ (e.g. a deployment aliased as "prod-claude").
    models = sorted(
        (
            ModelInfo(
                id=d["name"],
                label=d.get("modelName"),
                capabilities=get_capabilities(d.get("modelName") or d["name"]),
            )
            for d in data
            if d.get("name")
        ),
        key=lambda m: m.id,
    )
    return models, "api", None
