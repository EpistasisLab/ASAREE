"""Which models a user can actually pick for a given provider/credential.

There is NO cross-provider standard for capability discovery. All three were
probed live, and all three answer differently:

* **Anthropic** -- GET /v1/models returns a full capability tree per model
  (``effort.supported`` plus a per-level ``supported`` flag, ``thinking``,
  ``image_input``, ``structured_outputs``, ...) alongside ``max_input_tokens``
  / ``max_tokens``. Authoritative and self-updating, so it's used directly and
  Motoro's _REGISTRY is bypassed for this provider.
* **Azure Foundry** -- the deployments listing has a ``capabilities`` field,
  but it only ever contains ``{"chat_completion": "true"}``: a modality, not a
  sampling contract. Capability lookup there still has to go through
  get_capabilities() on the underlying model name.
* **OpenAI** -- GET /v1/models returns ``id``/``object``/``created``/
  ``owned_by``/``shutdown_date`` and nothing else; there is no capability
  endpoint at all (it's an open feature request). Hand-maintained data is the
  only option, so this provider stays on the curated catalog.

Nor is "temperature is being replaced by effort" quite true, which is why the
two are separate booleans rather than one enum. On OpenAI's 5.1+ families the
same model accepts *either* dial -- temperature is legal precisely when
``reasoning_effort`` is ``none`` -- so support is a property of the request
mode, not only of the model. ModelCapabilities flattens that to "which dial
do we show", which is right for Anthropic (adaptive thinking is always on, so
temperature is simply rejected) and an approximation for OpenAI.

Azure Foundry deployments are the opposite of Anthropic's: per-resource, so nothing short
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
from typing import Any

import httpx
from motoro.services.model_capabilities import (
    CATALOG,
    DEFAULT_EFFORT,
    EFFORT_LEVELS_FULL,
    ModelCapabilities,
    get_capabilities,
)

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services.user_llm_settings import decrypt_api_key

logger = logging.getLogger(__name__)

_PROJECT_DEPLOYMENTS_API_VERSION = "v1"
_REQUEST_TIMEOUT_SECONDS = 10.0

_OPENROUTER_DEFAULT_API_BASE = "https://openrouter.ai/api/v1"

_ANTHROPIC_DEFAULT_API_BASE = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_PAGE_LIMIT = 100
# A page loop that can't run away if `has_more` is ever wrong. 10 pages of 100
# is far past any plausible model count.
_ANTHROPIC_MAX_PAGES = 10

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
    """Returns ``(models, source, note)``.

    ``source`` is ``"api"`` for a live listing that succeeded (Anthropic or
    Azure Foundry), ``"static"`` for the curated catalog -- whether that's the
    only option (OpenAI), the no-credential case, or a fallback after a failed
    Anthropic call -- or ``"error"`` when discovery couldn't run at all and
    there's nothing to show.

    Callers treat only ``"api"`` as authoritative enough to call a model id
    wrong: a catalog can't vouch for ids it never knew about.
    """
    if provider == "anthropic":
        # No credential means no listing call to make -- fall back to the
        # curated catalog rather than showing nothing.
        if setting is None:
            return _static_catalog(provider), "static", None
        return await _discover_anthropic(setting)

    if provider == "openai":
        # OpenAI's GET /v1/models is confirmed (probed live) to return only
        # id/object/created/owned_by/shutdown_date -- no capability data at
        # all, and no other endpoint exposes it. Listing it live would mean
        # 124 entries (mostly embeddings/TTS/image/realtime models that can't
        # serve a chat turn) with every one of them falling back to
        # DEFAULT_CAPABILITIES, i.e. a Temperature slider offered for the
        # whole reasoning lineup. Until that's addressed, the curated catalog
        # is the more honest answer. See the module docstring.
        return _static_catalog(provider), "static", None

    if provider == "azure_foundry":
        if setting is None or not setting.api_base:
            return [], "error", "Set up a credential with a resource name first."
        if not setting.azure_project_endpoint:
            return [], "error", NO_PROJECT_ENDPOINT_NOTE
        return await _discover_azure_via_project(setting)

    if provider == "openrouter":
        # Unlike every other provider here, OpenRouter's listing call needs
        # no credential at all -- it's a public catalog -- so this runs even
        # before a key is saved, same spirit as the static catalog being
        # shown with no credential for Anthropic/OpenAI.
        return await _discover_openrouter(setting)

    if provider == "local":
        if setting is None or not setting.api_base:
            return [], "error", "Set up a credential with your server's base URL first."
        return await _discover_local(setting)

    return [], "error", f"Model discovery isn't supported for provider {provider!r}."


def _static_catalog(provider: str) -> list[ModelInfo]:
    return [
        ModelInfo(id=entry.model, label=entry.label, capabilities=entry.capabilities)
        for entry in CATALOG
        if entry.provider == provider
    ]


def _capabilities_from_anthropic(caps: dict[str, Any]) -> ModelCapabilities:
    """Map Anthropic's own capability tree onto ModelCapabilities.

    Shape confirmed live against GET /v1/models::

        "effort": {"supported": true,
                   "low": {"supported": true}, ..., "max": {"supported": true}}

    This is strictly better than Motoro's hand-maintained _REGISTRY for
    this provider: the registry gives every entry one hardcoded effort ladder,
    whereas the real ladders differ per model (opus-4-5 stops at high,
    sonnet-4-6/opus-4-6 have no xhigh, the 5-series has the full set) and
    older models like haiku-4-5/sonnet-4-5 report effort unsupported outright.

    ``supports_temperature`` is the one value NOT stated by the API -- there
    is no temperature leaf in the tree. Inferring it as "the inverse of
    effort" reproduces exactly what _REGISTRY already encodes by hand, and
    matches the underlying reason both exist: these models run adaptive
    thinking (note "thinking.types.enabled.supported": false alongside
    "adaptive": true) and 400 on an explicit temperature.
    """
    effort = caps.get("effort") or {}
    supports_effort = bool(effort.get("supported"))
    levels = [level for level in EFFORT_LEVELS_FULL if (effort.get(level) or {}).get("supported")]
    return ModelCapabilities(
        supports_temperature=not supports_effort,
        supports_effort=supports_effort,
        effort_levels=levels,
        default_effort=(DEFAULT_EFFORT if DEFAULT_EFFORT in levels else levels[0]) if levels else None,
    )


async def _discover_anthropic(setting: UserLLMSetting) -> tuple[list[ModelInfo], str, str | None]:
    """Live model list from Anthropic's own Models API.

    Unlike Azure's listing this isn't per-resource -- Anthropic's catalog is
    provider-wide -- but it's still fetched per credential, because a key's
    org can be gated to a subset (Project Glasswing models, previews) and
    because the response is the only authoritative source for the
    capabilities above. A newly released model appears here the day it ships,
    with no Motoro release needed.

    Failure falls back to the curated catalog rather than surfacing an error:
    the catalog is stale, not wrong, and an empty dropdown is a worse answer
    than an incomplete one. The returned source is "static" in that case, so
    callers gating on "api" (LlmNode's unrecognized-model warning) correctly
    stay quiet about ids this list can't vouch for.
    """
    api_key = decrypt_api_key(setting)
    base = (setting.api_base or _ANTHROPIC_DEFAULT_API_BASE).rstrip("/")
    entries: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            after_id: str | None = None
            for _ in range(_ANTHROPIC_MAX_PAGES):
                params: dict[str, str | int] = {"limit": _ANTHROPIC_PAGE_LIMIT}
                if after_id:
                    params["after_id"] = after_id
                response = await client.get(
                    f"{base}/v1/models",
                    params=params,
                    headers={"x-api-key": api_key, "anthropic-version": _ANTHROPIC_VERSION},
                )
                response.raise_for_status()
                page = response.json()
                entries.extend(page.get("data") or [])
                if not page.get("has_more"):
                    break
                after_id = page.get("last_id")
                if not after_id:
                    break
    except httpx.HTTPError as e:
        message = str(e).replace(api_key, "***")
        logger.warning("anthropic_model_discovery_failed", extra={"error": message})
        return (
            _static_catalog("anthropic"),
            "static",
            f"Couldn't reach Anthropic to list models ({message}), so this is the built-in catalog. "
            "You can still type any model name directly in the Model field.",
        )

    models = [
        ModelInfo(
            id=entry["id"],
            label=entry.get("display_name"),
            capabilities=_capabilities_from_anthropic(entry.get("capabilities") or {}),
        )
        for entry in entries
        if entry.get("id")
    ]
    if not models:
        return _static_catalog("anthropic"), "static", None
    return models, "api", None


async def _discover_openrouter(setting: UserLLMSetting | None) -> tuple[list[ModelInfo], str, str | None]:
    """Live model list from OpenRouter's own public catalog.

    Unlike every other live listing here, this needs no credential -- OpenRouter
    documents GET /models as public, so a saved key is only used (as a bearer
    token) when present, never required. The response has price/context data
    but no explicit temperature-vs-effort field the way Anthropic's does, so
    per-model capabilities fall back to Motoro's own get_capabilities(), which
    strips the leading `vendor/` segment before matching (see
    model_capabilities._normalize) -- an id like "anthropic/claude-opus-5"
    resolves against the exact same registry entry the native Anthropic
    provider does.
    """
    base = (setting.api_base if setting and setting.api_base else _OPENROUTER_DEFAULT_API_BASE).rstrip("/")
    headers = {}
    api_key = decrypt_api_key(setting) if setting is not None else None
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{base}/models", headers=headers)
            response.raise_for_status()
        data = response.json().get("data") or []
    except httpx.HTTPError as e:
        message = str(e).replace(api_key, "***") if api_key else str(e)
        logger.warning("openrouter_model_discovery_failed", extra={"error": message})
        return (
            [],
            "error",
            f"Couldn't reach OpenRouter to list models ({message}). You can still type any model id "
            "directly in the Model field.",
        )

    models = sorted(
        (
            ModelInfo(id=m["id"], label=m.get("name"), capabilities=get_capabilities(m["id"]))
            for m in data
            if m.get("id")
        ),
        key=lambda m: m.id,
    )
    if not models:
        return [], "error", "OpenRouter returned no models. You can still type any model id directly."
    return models, "api", None


async def _discover_local(setting: UserLLMSetting) -> tuple[list[ModelInfo], str, str | None]:
    """Best-effort live listing via the OpenAI-compatible GET /models route
    most self-hosted servers (LM Studio, vLLM, llama.cpp server, ...)
    implement -- but that route isn't a hard requirement of being "an
    OpenAI-compatible chat endpoint", so a server that doesn't expose it is a
    normal, expected outcome (surfaced via ``source="error"`` purely so the
    inspector's note actually renders -- see LlmNodeInspector.tsx, which only
    shows ``note`` for that source -- not because it's a real error).
    """
    base = setting.api_base.rstrip("/") if setting.api_base else ""
    api_key = decrypt_api_key(setting) or "not-needed"
    fallback_note = "This server didn't return a model list -- type the model's name directly in the Model field below."
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{base}/models", headers={"Authorization": f"Bearer {api_key}"})
            response.raise_for_status()
        data = response.json().get("data") or []
    except httpx.HTTPError as e:
        message = str(e).replace(api_key, "***")
        logger.info("local_model_discovery_unavailable", extra={"error": message})
        return [], "error", fallback_note

    models = sorted(
        (ModelInfo(id=m["id"], label=None, capabilities=get_capabilities(m["id"])) for m in data if m.get("id")),
        key=lambda m: m.id,
    )
    if not models:
        return [], "error", fallback_note
    return models, "api", None


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
