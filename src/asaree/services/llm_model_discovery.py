"""Which models a user can actually pick for a given provider/credential.

Anthropic/OpenAI's own catalogs are curated and provider-wide -- they don't
vary per user, so agentic_core.services.model_capabilities.CATALOG (already
hand-maintained there specifically because litellm's own dynamic capability
data is unreliable for newer models -- see that module's own docstring) is
the source of truth for those two. No live call, no credential needed.

Azure Foundry deployments are the opposite: per-resource, so nothing short
of asking that resource can know what's actually deployed there -- and it
turns out there are two genuinely different listing calls, at two different
scopes, and a saved credential may only have one of them:

- The classic Azure OpenAI "list deployments" endpoint
  (``{resource}/openai/deployments?api-version=...``, plain api-key auth)
  mirrors ARES's own approach (user_llm_settings_service.py's
  ``_discover_azure``). It 404s unconditionally on a Foundry resource
  hosting Claude models -- confirmed live against a real resource -- because
  Claude deployments were never "Azure OpenAI deployments" to begin with;
  they're served through the separate ``/anthropic`` passthrough. This isn't
  a broken credential, just the wrong API for this kind of deployment.
- The real listing call for a Foundry project's deployments (any publisher,
  including Anthropic) is ``{project_endpoint}/deployments?api-version=v1``
  -- confirmed live to work with the same plain ``api-key`` header, despite
  Microsoft's own REST reference documenting only OAuth2 for it. This needs
  the *project*-scoped endpoint (``.../api/projects/{project}``), which is a
  genuinely different piece of connection info than the bare resource host
  used for inference -- UserLLMSetting.azure_project_endpoint, optional,
  since inference works fine without it.

So: use the project endpoint when the credential has one (the only path
that actually works for a Claude-on-Foundry resource); otherwise fall back
to the classic endpoint (works for a plain Azure OpenAI resource with no
project configured), and failing that, tell the user how to fix it rather
than dump a raw exception.
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
_PROJECT_DEPLOYMENTS_API_VERSION = "v1"
_REQUEST_TIMEOUT_SECONDS = 10.0

_NO_LISTING_API_NOTE = (
    "This Azure resource has no deployment-listing API available -- enter your deployment's name directly in "
    "the Model field below, or add a Project endpoint to this credential to enable listing (expected for a "
    "Foundry resource hosting Claude models)."
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
        if setting.azure_project_endpoint:
            return await _discover_azure_via_project(setting)
        return await _discover_azure_classic(setting)

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
    # "modelPublisher", "modelVersion", "capabilities", "sku"}, ...]} --
    # distinct from the classic endpoint's {"data": [{"id"}]}. `name` is the
    # deployment's own name (what a real inference call must pass as
    # `model`); `modelName` is the underlying published model id, a better
    # key for capability lookup and a more useful label when the two differ
    # (e.g. a deployment aliased as "prod-claude").
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


async def _discover_azure_classic(setting: UserLLMSetting) -> tuple[list[ModelInfo], str, str | None]:
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
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return [], "error", _NO_LISTING_API_NOTE
        message = str(e).replace(api_key, "***")
        logger.warning("azure_foundry_model_discovery_failed", extra={"error": message})
        return [], "error", f"Could not reach Azure Foundry to list models: {message}"
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
