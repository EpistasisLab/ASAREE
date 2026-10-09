"""Use the model picker's capability metadata when constructing execution configs."""

from __future__ import annotations

import uuid
from typing import Any

from motoro.schemas.agent import ModelConfig
from motoro.services.model_capabilities import ModelCapabilities, get_capabilities
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.database import get_session
from asaree.services.credential_resolver import SUPPORTED_PROVIDERS
from asaree.services.llm_model_cache import discover_models_cached
from asaree.services.user_llm_settings import get_setting


async def resolve_model_capabilities(
    *, provider: str, model: str, db: AsyncSession, owner_id: uuid.UUID
) -> ModelCapabilities:
    """Shared by custom-model lookup and execution: discovery, registry, then effort fallback."""
    capabilities = get_capabilities(model)
    if provider in SUPPORTED_PROVIDERS:
        setting = await get_setting(db, user_id=owner_id, provider=provider)
        models, _source, _note = await discover_models_cached(user_id=owner_id, provider=provider, setting=setting)
        capabilities = next((entry.capabilities for entry in models if entry.id == model), capabilities)
    return capabilities


async def _resolve_capabilities(config: ModelConfig, *, db: AsyncSession, owner_id: uuid.UUID) -> None:
    # Explicit metadata remains the escape hatch for custom temperature-only models.
    if config.resolved_capabilities is None:
        config.resolved_capabilities = await resolve_model_capabilities(
            provider=config.provider.value, model=config.model, db=db, owner_id=owner_id
        )
    if not config.resolved_capabilities.supports_effort:
        # The editor preserves a hidden effort value when switching models.
        # It belongs to the draft, not to this temperature-only request.
        config.effort = None
    for fallback in config.fallback_models:
        await _resolve_capabilities(fallback, db=db, owner_id=owner_id)


async def build_model_config(data: dict[str, Any], *, owner_id: uuid.UUID) -> ModelConfig:
    """Resolve each effective model after factorial substitutions, without editing the graph."""
    config = ModelConfig(**{key: value for key, value in data.items() if value is not None})
    async with get_session() as db:
        await _resolve_capabilities(config, db=db, owner_id=owner_id)
    return config
