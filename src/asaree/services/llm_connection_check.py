"""Does this saved credential actually work? -- answered without spending a
single token.

Saving a credential validates nothing (user_llm_settings.upsert_setting only
encrypts and stores), and for anthropic/openai the Model dropdown is a
static in-process catalog, so it populates identically whether the key is
valid, expired, or nonsense. That left no way to distinguish "my key is
wrong" from "my agent is misconfigured" until a real run failed.

Every provider exposes an authenticated *list* endpoint that bills nothing,
so the check is a plain GET:

- openai:    GET {base}/v1/models, ``Authorization: Bearer``
- anthropic: GET {base}/v1/models, ``x-api-key`` + ``anthropic-version``
- azure:     no separate call -- deployment discovery IS the check, so this
  delegates to llm_model_discovery rather than growing a second copy of that
  module's two-endpoint logic.

What a 200 proves: the key is real, the network path works, and the org/
resource resolves. What it does NOT prove: quota, billing, or per-project
model permissions -- those are only enforced at inference time, so a key can
pass here and still return 429 insufficient_quota on the first real request.
The UI wording must stay honest about that ("Key valid", never "Ready").

Hence three states, not two: an Azure resource with no listing API at all
(the common Claude-on-Foundry case) is UNKNOWN, not failed -- refusing to
list deployments says nothing about whether the credential can infer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import httpx

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services.llm_model_discovery import NO_LISTING_API_NOTE, discover_models
from asaree.services.user_llm_settings import decrypt_api_key

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 10.0

_OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
_ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com"

# Pinned because Anthropic's API requires it on every request; any supported
# version works for a models list, so this never needs to chase the latest.
_ANTHROPIC_VERSION = "2023-06-01"

ConnectionStatus = Literal["ok", "failed", "unknown"]


@dataclass(frozen=True)
class ConnectionCheck:
    status: ConnectionStatus
    detail: str
    # What was actually contacted, so a failure is debuggable without
    # guessing which base URL the credential resolved to. Never contains the
    # key -- every provider here authenticates by header, not query string.
    endpoint: str | None


def _models_url(base: str) -> str:
    """Both ``/v1``-suffixed and bare bases are in the wild -- OpenAI's own
    convention includes it (``https://api.openai.com/v1``) while Anthropic's
    does not, and a user pasting a proxy base may do either. Normalize rather
    than making the user guess which one this field wants."""
    base = base.rstrip("/")
    if base.endswith("/models"):
        return base
    return f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"


async def check_connection(*, provider: str, setting: UserLLMSetting) -> ConnectionCheck:
    if provider == "azure_foundry":
        return await _check_azure(setting)

    api_key = decrypt_api_key(setting)
    if provider == "openai":
        url = _models_url(setting.api_base or _OPENAI_DEFAULT_BASE)
        headers = {"Authorization": f"Bearer {api_key}"}
    elif provider == "anthropic":
        url = _models_url(setting.api_base or _ANTHROPIC_DEFAULT_BASE)
        headers = {"x-api-key": api_key, "anthropic-version": _ANTHROPIC_VERSION}
    else:
        return ConnectionCheck(
            status="unknown", detail=f"Connection checks aren't supported for provider {provider!r}.", endpoint=None
        )

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        # Same scrubbing posture as llm_model_discovery: a raw httpx repr can
        # embed the request headers, and this string goes to the browser.
        message = str(e).replace(api_key, "***")
        logger.warning("llm_connection_check_failed", extra={"provider": provider, "error": message})
        return ConnectionCheck(status="failed", detail=f"Could not reach the provider: {message}", endpoint=url)

    if response.is_success:
        return ConnectionCheck(
            status="ok",
            detail="Key accepted and the provider is reachable. This doesn't verify quota or billing.",
            endpoint=url,
        )
    if response.status_code in (401, 403):
        return ConnectionCheck(
            status="failed",
            detail=f"The provider rejected this API key (HTTP {response.status_code}).",
            endpoint=url,
        )
    if response.status_code == 429:
        # Reaching a rate limit still proves the key authenticated -- the
        # provider had to identify the caller to rate-limit them.
        return ConnectionCheck(
            status="unknown",
            detail="The provider rate-limited this check (HTTP 429). The key authenticated; try again shortly.",
            endpoint=url,
        )
    return ConnectionCheck(status="failed", detail=f"The provider returned HTTP {response.status_code}.", endpoint=url)


async def _check_azure(setting: UserLLMSetting) -> ConnectionCheck:
    """Reuses deployment discovery verbatim -- for Foundry, "can I list what's
    deployed here" IS the zero-cost credential check, and duplicating that
    module's project-vs-classic endpoint choice would just give two answers
    that could drift apart."""
    endpoint = setting.azure_project_endpoint or setting.api_base
    models, source, note = await discover_models(provider="azure_foundry", setting=setting)
    if source == "api":
        return ConnectionCheck(
            status="ok",
            detail=f"Reached the Azure resource and listed {len(models)} deployment(s).",
            endpoint=endpoint,
        )
    if note == NO_LISTING_API_NOTE:
        # A 404 from the classic endpoint means this resource has no
        # api-key-authenticated listing API -- the expected case for a
        # Foundry resource hosting Claude models. That is emphatically not a
        # bad credential, and reporting it as one would send users chasing a
        # key that's fine. Inconclusive is the truthful answer.
        return ConnectionCheck(
            status="unknown",
            detail=(
                "This Azure resource exposes no deployment-listing API, so the credential can't be checked "
                "for free. Add a Project endpoint to enable listing, or test it with a real run."
            ),
            endpoint=endpoint,
        )
    return ConnectionCheck(status="failed", detail=note or "Could not reach the Azure resource.", endpoint=endpoint)
