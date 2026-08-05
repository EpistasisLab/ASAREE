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
from asaree.services.user_llm_settings import list_settings, upsert_setting

router = APIRouter(prefix="/llm-settings", tags=["llm-settings"])


class UpsertLLMSettingRequest(BaseModel):
    provider: str
    api_key: str
    api_base: str | None = None


class LLMSettingResponse(BaseModel):
    provider: str
    api_base: str | None


@router.put("", response_model=LLMSettingResponse, status_code=201)
async def upsert_llm_setting_endpoint(
    body: UpsertLLMSettingRequest, user: CurrentUser, db: DbSession
) -> LLMSettingResponse:
    if body.provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=422, detail=f"provider must be one of {sorted(SUPPORTED_PROVIDERS)}")
    setting = await upsert_setting(
        db, user_id=user.id, provider=body.provider, api_key=body.api_key, api_base=body.api_base
    )
    return LLMSettingResponse(provider=setting.provider, api_base=setting.api_base)


@router.get("", response_model=list[LLMSettingResponse])
async def list_llm_settings_endpoint(user: CurrentUser, db: DbSession) -> list[LLMSettingResponse]:
    settings = await list_settings(db, user_id=user.id)
    return [LLMSettingResponse(provider=s.provider, api_base=s.api_base) for s in settings]
