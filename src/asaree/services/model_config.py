"""Use the model picker's capability metadata when constructing execution configs."""

from __future__ import annotations

import uuid
from typing import Any

from motoro.schemas.agent import ModelConfig
from motoro.services.model_capabilities import ModelCapabilities
from motoro.services.provider_types import ModelInfo, ModelListing
from motoro.services.providers import get_model_metadata
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.database import get_session
from asaree.services.credential_resolver import SUPPORTED_PROVIDERS
from asaree.services.llm_model_cache import discover_models_cached
from asaree.services.user_llm_settings import get_setting


async def resolve_model_capabilities(
    *, provider: str, model: str, db: AsyncSession, owner_id: uuid.UUID
) -> ModelCapabilities:
    """Shared by custom-model lookup and execution; unknown support stays unknown."""
    return (await resolve_model_metadata(provider=provider, model=model, db=db, owner_id=owner_id)).capabilities


async def resolve_model_metadata(*, provider: str, model: str, db: AsyncSession, owner_id: uuid.UUID) -> ModelInfo:
    """Provider facts, including defaults/provenance, separate from app preferences."""
    listing = None
    if provider in SUPPORTED_PROVIDERS:
        setting = await get_setting(db, user_id=owner_id, provider=provider)
        models, source, note = await discover_models_cached(user_id=owner_id, provider=provider, setting=setting)
        listing = ModelListing(models=models, source=source, note=note)
    return get_model_metadata(model=model, listing=listing)


async def _resolve_capabilities(config: ModelConfig, *, db: AsyncSession, owner_id: uuid.UUID) -> None:
    # Preserve explicit overrides and the metadata pinned in published snapshots.
    if config.resolved_capabilities is None:
        config.resolved_capabilities = await resolve_model_capabilities(
            provider=config.provider.value, model=config.model, db=db, owner_id=owner_id
        )
    for fallback in config.fallback_models:
        await _resolve_capabilities(fallback, db=db, owner_id=owner_id)


async def build_model_config(data: dict[str, Any], *, owner_id: uuid.UUID) -> ModelConfig:
    """Resolve each effective model after factorial substitutions, without editing the graph."""
    config = ModelConfig(**{key: value for key, value in data.items() if value is not None})
    return await resolve_execution_model_config(config, owner_id=owner_id)


async def resolve_execution_model_config(config: ModelConfig, *, owner_id: uuid.UUID) -> ModelConfig:
    """Resolve canvas and standalone API configurations without serializing credentials."""
    config = config.model_copy(deep=True)
    async with get_session() as db:
        await _resolve_capabilities(config, db=db, owner_id=owner_id)
    return config
