"""Per-user LLM provider credentials — scoped to the caller, no admin view.

Always operates on the authenticated user (``CurrentUser``); there's no
``user_id`` in any of these URLs, on purpose — a credential belongs to
whoever authenticated, never to an id someone else supplies.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from motoro.services.model_capabilities import ModelCapabilities
from pydantic import BaseModel

from asaree.deps import CurrentUser, DbSession
from asaree.services.credential_resolver import SUPPORTED_PROVIDERS
from asaree.services.llm_connection_check import check_connection
from asaree.services.llm_model_cache import discover_models_cached, invalidate_models_cache, store_models_cache
from asaree.services.llm_model_discovery import ModelInfo
from asaree.services.model_config import resolve_model_capabilities
from asaree.services.rate_limit import check_rate_limit, record_attempt
from asaree.services.tool_calling import model_tool_calling_support
from asaree.services.user_llm_settings import delete_setting, get_setting, list_settings, upsert_setting

router = APIRouter(prefix="/llm-settings", tags=["llm-settings"])

# Only the Azure Foundry path makes a real outbound call using the user's own
# key (Anthropic/OpenAI resolve from a static, in-process catalog) -- capped
# defensively so a retry loop can't hammer Azure with it, same posture ARES
# already takes for the same call.
_DISCOVERY_MAX_ATTEMPTS = 10
_DISCOVERY_WINDOW_SECONDS = 60

# The connection check ALWAYS makes an outbound call with the user's key --
# free in tokens, but still a request against a rate-limited provider
# endpoint, and a button a frustrated user will mash. Same cap, its own
# bucket so it can't exhaust discovery's.
_CONNECTION_CHECK_MAX_ATTEMPTS = 10
_CONNECTION_CHECK_WINDOW_SECONDS = 60


class UpsertLLMSettingRequest(BaseModel):
    provider: str
    api_key: str
    api_base: str | None = None
    # azure_foundry only -- see UserLLMSetting's own comment for why this is
    # a genuinely separate field from api_base, not derived from it.
    azure_project_endpoint: str | None = None


class LLMSettingResponse(BaseModel):
    provider: str
    api_base: str | None
    azure_project_endpoint: str | None


class LLMModelInfoResponse(BaseModel):
    id: str
    label: str | None
    supports_temperature: bool
    supports_effort: bool
    effort_levels: list[str]
    default_effort: str | None
    # Unlike the three above, this one doesn't come from Motoro's
    # ModelCapabilities registry -- it's litellm's own function-calling flag,
    # the same oracle ``model_supports_tool_calling`` uses to pick an agent's
    # default execution pattern. Surfaced so the canvas can warn that an agent
    # wired to peers is on a model that can never be sent function schemas, and
    # so would silently never consult them. Null when litellm has never heard
    # of the model (any Azure Foundry deployment name, for one) -- "can't tell"
    # rather than "no", so an unknown model raises no warning.
    supports_tool_calling: bool | None


class LLMSettingModelsResponse(BaseModel):
    models: list[LLMModelInfoResponse]
    source: str
    note: str | None


def _model_response(model: ModelInfo, provider: str) -> LLMModelInfoResponse:
    return LLMModelInfoResponse(
        id=model.id,
        label=model.label,
        supports_temperature=model.capabilities.supports_temperature,
        supports_effort=model.capabilities.supports_effort,
        effort_levels=model.capabilities.effort_levels,
        default_effort=model.capabilities.default_effort,
        supports_tool_calling=model_tool_calling_support(provider, model.id),
    )


class LLMConnectionCheckResponse(BaseModel):
    provider: str
    # "ok" | "failed" | "unknown" -- see llm_connection_check for why an
    # inconclusive state is a real outcome and not a cop-out.
    status: str
    detail: str
    endpoint: str | None
    models: list[LLMModelInfoResponse] | None = None


@router.put("", response_model=LLMSettingResponse, status_code=201)
async def upsert_llm_setting_endpoint(
    body: UpsertLLMSettingRequest, user: CurrentUser, db: DbSession
) -> LLMSettingResponse:
    if body.provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=422, detail=f"provider must be one of {sorted(SUPPORTED_PROVIDERS)}")
    setting = await upsert_setting(
        db,
        user_id=user.id,
        provider=body.provider,
        api_key=body.api_key,
        api_base=body.api_base,
        azure_project_endpoint=body.azure_project_endpoint,
    )
    # A new key can reach a different set of models than the old one, and a
    # changed Azure project endpoint points at an entirely different set of
    # deployments -- either way the cached list is now about a credential
    # that no longer exists.
    await invalidate_models_cache(user_id=user.id, provider=body.provider)
    return LLMSettingResponse(
        provider=setting.provider, api_base=setting.api_base, azure_project_endpoint=setting.azure_project_endpoint
    )


@router.get("", response_model=list[LLMSettingResponse])
async def list_llm_settings_endpoint(user: CurrentUser, db: DbSession) -> list[LLMSettingResponse]:
    settings = await list_settings(db, user_id=user.id)
    return [
        LLMSettingResponse(provider=s.provider, api_base=s.api_base, azure_project_endpoint=s.azure_project_endpoint)
        for s in settings
    ]


@router.delete("/{provider}", status_code=204)
async def delete_llm_setting_endpoint(provider: str, user: CurrentUser, db: DbSession) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=422, detail=f"provider must be one of {sorted(SUPPORTED_PROVIDERS)}")
    deleted = await delete_setting(db, user_id=user.id, provider=provider)
    if not deleted:
        raise HTTPException(status_code=404, detail="No credential saved for this provider.")
    await invalidate_models_cache(user_id=user.id, provider=provider)


@router.get("/{provider}/connection", response_model=LLMConnectionCheckResponse)
async def check_connection_endpoint(provider: str, user: CurrentUser, db: DbSession) -> LLMConnectionCheckResponse:
    """Zero-token liveness check for the caller's saved credential.

    Deliberately on demand rather than folded into GET /llm-settings: it's
    free in tokens but it's still one outbound request per provider, and a
    credentials page that silently calls three providers on every render is
    a worse citizen than a button.
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=422, detail=f"provider must be one of {sorted(SUPPORTED_PROVIDERS)}")

    key = f"connection-check:{user.id}:{provider}"
    allowed, retry_after = await check_rate_limit(
        key, limit=_CONNECTION_CHECK_MAX_ATTEMPTS, window_seconds=_CONNECTION_CHECK_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"Too many connection checks. Please wait {retry_after} seconds.",
                "retry_after_seconds": retry_after,
            },
        )
    await record_attempt(key, window_seconds=_CONNECTION_CHECK_WINDOW_SECONDS)

    setting = await get_setting(db, user_id=user.id, provider=provider)
    if setting is None:
        raise HTTPException(status_code=404, detail="No credential saved for this provider.")

    result = await check_connection(provider=provider, setting=setting)
    if provider == "azure_foundry" and result.status == "ok" and result.models is not None:
        await store_models_cache(user_id=user.id, provider=provider, models=result.models)
    return LLMConnectionCheckResponse(
        provider=provider,
        status=result.status,
        detail=result.detail,
        endpoint=result.endpoint,
        models=[_model_response(model, provider) for model in result.models] if result.models is not None else None,
    )


@router.get("/{provider}/models", response_model=LLMSettingModelsResponse)
async def list_models_endpoint(provider: str, user: CurrentUser, db: DbSession) -> LLMSettingModelsResponse:
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=422, detail=f"provider must be one of {sorted(SUPPORTED_PROVIDERS)}")

    key = f"model-discovery:{user.id}:{provider}"
    allowed, retry_after = await check_rate_limit(
        key, limit=_DISCOVERY_MAX_ATTEMPTS, window_seconds=_DISCOVERY_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"Too many model list requests. Please wait {retry_after} seconds.",
                "retry_after_seconds": retry_after,
            },
        )
    await record_attempt(key, window_seconds=_DISCOVERY_WINDOW_SECONDS)

    setting = await get_setting(db, user_id=user.id, provider=provider)
    models, source, note = await discover_models_cached(user_id=user.id, provider=provider, setting=setting)
    return LLMSettingModelsResponse(
        models=[_model_response(model, provider) for model in models],
        source=source,
        note=note,
    )


@router.get("/{provider}/model-capabilities", response_model=ModelCapabilities)
async def model_capabilities_endpoint(
    provider: str, model: Annotated[str, Query(min_length=1, max_length=256)], user: CurrentUser, db: DbSession
) -> ModelCapabilities:
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=422, detail=f"provider must be one of {sorted(SUPPORTED_PROVIDERS)}")
    if not model.strip():
        raise HTTPException(status_code=422, detail="model must not be blank")
    return await resolve_model_capabilities(provider=provider, model=model, db=db, owner_id=user.id)
