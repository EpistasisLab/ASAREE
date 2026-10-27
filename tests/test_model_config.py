"""Picker metadata must govern the actual request, including factorial model changes."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import litellm
import pytest
from fastapi import FastAPI
from motoro.services.llm_service import UnsupportedEffortError, _sampling_kwargs
from motoro.services.model_capabilities import TEMPERATURE_ONLY_CAPABILITIES, ModelCapabilities

from asaree.api.llm_settings import router
from asaree.deps import get_current_user
from asaree.models.database import get_db
from asaree.services import model_config as service
from asaree.services.llm_model_discovery import ModelInfo, _capabilities_from_openrouter

OWNER = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def discovery(monkeypatch):
    @asynccontextmanager
    async def session():
        yield object()

    monkeypatch.setattr(service, "get_session", session)
    monkeypatch.setattr(service, "get_setting", AsyncMock(return_value=None))
    lookup = AsyncMock(return_value=([], "static", None))
    monkeypatch.setattr(service, "discover_models_cached", lookup)
    return lookup


async def test_live_effort_metadata_overrides_temperature_registry(discovery):
    capabilities = ModelCapabilities(
        supports_temperature=False,
        supports_effort=True,
        effort_levels=["low", "high"],
        default_effort="high",
    )
    discovery.return_value = ([ModelInfo(id="gpt-4o", label=None, capabilities=capabilities)], "api", None)
    data = {"provider": "openai", "model": "gpt-4o", "temperature": 0.7}

    config = await service.build_model_config(data, owner_id=OWNER)

    assert _sampling_kwargs(config, "openai/gpt-4o") == {"reasoning_effort": "high"}
    assert "resolved_capabilities" not in data
    assert discovery.call_args.kwargs["user_id"] == OWNER


async def test_live_temperature_metadata_overrides_effort_registry(discovery):
    discovery.return_value = (
        [ModelInfo(id="gpt-5", label=None, capabilities=TEMPERATURE_ONLY_CAPABILITIES)],
        "api",
        None,
    )
    config = await service.build_model_config(
        {"provider": "openai", "model": "gpt-5", "temperature": 0.3},
        owner_id=OWNER,
    )
    assert _sampling_kwargs(config, "openai/gpt-5") == {"temperature": 0.3}


async def test_hidden_effort_does_not_break_a_temperature_only_model(discovery):
    data = {"provider": "openai", "model": "gpt-4o", "temperature": 0.3, "effort": "high"}
    config = await service.build_model_config(data, owner_id=OWNER)
    assert _sampling_kwargs(config, "openai/gpt-4o") == {"temperature": 0.3}
    assert data["effort"] == "high"


async def test_unknown_model_defaults_to_medium_effort(discovery):
    config = await service.build_model_config(
        {"provider": "openai", "model": "new-model", "effort": None},
        owner_id=OWNER,
    )
    assert _sampling_kwargs(config, "openai/new-model") == {"reasoning_effort": "medium"}


@pytest.mark.parametrize("provider", ["openrouter", "local"])
async def test_unknown_router_or_local_model_uses_temperature(discovery, provider):
    config = await service.build_model_config(
        {"provider": provider, "model": "qwen/qwen3.8-27b", "effort": "medium", "temperature": 0.4},
        owner_id=OWNER,
    )
    assert _sampling_kwargs(config, "openrouter/qwen/qwen3.8-27b") == {"temperature": 0.4}


async def test_openrouter_claude_keeps_effort_without_discovery(discovery):
    config = await service.build_model_config(
        {"provider": "openrouter", "model": "anthropic/claude-opus-5", "effort": "high"},
        owner_id=OWNER,
    )
    assert _sampling_kwargs(config, "openrouter/anthropic/claude-opus-5")["reasoning_effort"] == "high"


async def test_openrouter_live_effort_survives_stale_litellm_model_map(discovery, monkeypatch):
    # Qwen's live catalog exposes effort even when the installed LiteLLM map doesn't.
    model = "qwen/qwen3.8-27b"
    capabilities = _capabilities_from_openrouter(
        {
            "id": model,
            "supported_parameters": ["temperature", "reasoning", "reasoning_effort"],
            "reasoning": {
                "supported_efforts": ["xhigh", "medium", "low", "none"],
                "default_effort": "xhigh",
            },
        }
    )
    discovery.return_value = ([ModelInfo(id=model, label=None, capabilities=capabilities)], "api", None)
    monkeypatch.setattr(litellm, "supports_reasoning", lambda **kwargs: False)

    with pytest.raises(litellm.UnsupportedParamsError, match="reasoning_effort"):
        litellm.utils.get_optional_params(model=model, custom_llm_provider="openrouter", reasoning_effort="medium")

    config = await service.build_model_config(
        {"provider": "openrouter", "model": model, "effort": "medium"},
        owner_id=OWNER,
    )
    kwargs = _sampling_kwargs(config, f"openrouter/{model}")
    assert kwargs == {"reasoning_effort": "medium", "allowed_openai_params": ["reasoning_effort"]}
    mapped = litellm.utils.get_optional_params(model=model, custom_llm_provider="openrouter", **kwargs)
    assert mapped["reasoning_effort"] == "medium"


async def test_explicit_custom_temperature_override_is_preserved(discovery):
    config = await service.build_model_config(
        {
            "provider": "local",
            "model": "custom",
            "temperature": 0.2,
            "resolved_capabilities": TEMPERATURE_ONLY_CAPABILITIES.model_dump(),
        },
        owner_id=OWNER,
    )
    assert _sampling_kwargs(config, "openai/custom") == {"temperature": 0.2}
    discovery.assert_not_called()


async def test_fallback_models_resolve_their_own_capabilities(discovery):
    config = await service.build_model_config(
        {
            "provider": "openai",
            "model": "gpt-4o",
            "fallback_models": [{"provider": "anthropic", "model": "new-model", "effort": "high"}],
        },
        owner_id=OWNER,
    )
    assert _sampling_kwargs(config, "openai/gpt-4o") == {"temperature": 0.7}
    assert _sampling_kwargs(config.fallback_models[0], "anthropic/new-model") == {"reasoning_effort": "high"}
    assert [call.kwargs["provider"] for call in discovery.call_args_list] == ["openai", "anthropic"]


async def test_unsupported_effort_rejected_against_discovered_levels(discovery):
    capabilities = ModelCapabilities(
        supports_temperature=False,
        supports_effort=True,
        effort_levels=["low", "high"],
        default_effort="high",
    )
    discovery.return_value = ([ModelInfo(id="new-model", label=None, capabilities=capabilities)], "api", None)
    config = await service.build_model_config(
        {"provider": "openai", "model": "new-model", "effort": "max"},
        owner_id=OWNER,
    )
    with pytest.raises(UnsupportedEffortError):
        _sampling_kwargs(config, "openai/new-model")


@pytest.fixture
async def api(discovery):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=OWNER)
    app.dependency_overrides[get_db] = lambda: object()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.parametrize("model", ["gpt-4o-2024-08-06", "openai/gpt-4o-2024-08-06", "truly-unknown"])
async def test_custom_capability_endpoint_matches_execution(api, model):
    response = await api.get("/llm-settings/openai/model-capabilities", params={"model": model})
    assert response.status_code == 200
    config = await service.build_model_config({"provider": "openai", "model": model}, owner_id=OWNER)
    assert response.json() == config.resolved_capabilities.model_dump()
    if model == "truly-unknown":
        assert _sampling_kwargs(config, model) == {"reasoning_effort": "medium"}
    else:
        assert _sampling_kwargs(config, model) == {"temperature": 0.7}


async def test_custom_endpoint_prefers_discovered_capabilities(api, discovery):
    capabilities = ModelCapabilities(
        supports_temperature=False,
        supports_effort=True,
        effort_levels=["high"],
        default_effort="high",
    )
    discovery.return_value = ([ModelInfo(id="gpt-4o", label=None, capabilities=capabilities)], "api", None)
    response = await api.get("/llm-settings/openai/model-capabilities", params={"model": "gpt-4o"})
    assert response.status_code == 200
    assert response.json() == capabilities.model_dump()
    assert discovery.call_args.kwargs["user_id"] == OWNER


@pytest.mark.parametrize("provider,model", [("invalid", "model"), ("openai", ""), ("openai", "   ")])
async def test_custom_endpoint_rejects_invalid_provider_or_model(api, provider, model):
    response = await api.get(f"/llm-settings/{provider}/model-capabilities", params={"model": model})
    assert response.status_code == 422
