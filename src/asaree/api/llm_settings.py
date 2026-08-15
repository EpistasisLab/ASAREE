"""Per-user LLM provider credentials — scoped to the caller, no admin view.

Always operates on the authenticated user (``CurrentUser``); there's no
``user_id`` in any of these URLs, on purpose — a credential belongs to
whoever authenticated, never to an id someone else supplies.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from asaree.deps import CurrentUser, DbSession
from asaree.services.credential_resolver import SUPPORTED_PROVIDERS
from asaree.services.llm_model_discovery import discover_models
from asaree.services.rate_limit import check_rate_limit, record_attempt
from asaree.services.user_llm_settings import delete_setting, get_setting, list_settings, upsert_setting

router = APIRouter(prefix="/llm-settings", tags=["llm-settings"])

# Only the Azure Foundry path makes a real outbound call using the user's own
# key (Anthropic/OpenAI resolve from a static, in-process catalog) -- capped
# defensively so a retry loop can't hammer Azure with it, same posture ARES
# already takes for the same call.
_DISCOVERY_MAX_ATTEMPTS = 10
_DISCOVERY_WINDOW_SECONDS = 60


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


class LLMSettingModelsResponse(BaseModel):
    models: list[LLMModelInfoResponse]
    source: str
    note: str | None


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
    models, source, note = await discover_models(provider=provider, setting=setting)
    return LLMSettingModelsResponse(
        models=[
            LLMModelInfoResponse(
                id=m.id,
                label=m.label,
                supports_temperature=m.capabilities.supports_temperature,
                supports_effort=m.capabilities.supports_effort,
                effort_levels=m.capabilities.effort_levels,
            )
            for m in models
        ],
        source=source,
        note=note,
    )
