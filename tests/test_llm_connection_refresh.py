"""The connection response and cached menu must share the checked deployments."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from motoro.services.model_capabilities import ModelCapabilities

from asaree.api import llm_settings as api
from asaree.services import llm_model_cache as cache
from asaree.services.llm_connection_check import ConnectionCheck
from asaree.services.llm_model_discovery import ModelInfo


@pytest.mark.parametrize("status", ["ok", "failed", "unknown"])
async def test_azure_connection_refreshes_cache_only_when_successful(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    deployments = [ModelInfo(id=str(index), label=str(index), capabilities=ModelCapabilities()) for index in range(4)]
    stored: dict[str, str] = {}

    async def put(key: str, value: str, *, ex: int) -> None:
        stored[key] = value

    redis = SimpleNamespace(get=AsyncMock(side_effect=lambda key: stored.get(key)), set=AsyncMock(side_effect=put))
    monkeypatch.setattr(cache, "get_redis", lambda: redis)
    await cache.store_models_cache(user_id=user.id, provider="azure_foundry", models=deployments[:3])
    discovery = AsyncMock(side_effect=AssertionError("Menu must reuse the checked deployments"))
    monkeypatch.setattr(cache, "discover_models", discovery)
    monkeypatch.setattr(api, "check_rate_limit", AsyncMock(return_value=(True, 0)))
    monkeypatch.setattr(api, "record_attempt", AsyncMock())
    monkeypatch.setattr(api, "get_setting", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        api,
        "check_connection",
        AsyncMock(
            return_value=ConnectionCheck(
                status=status, detail="checked", endpoint="project", models=deployments if status == "ok" else None
            )
        ),
    )
    monkeypatch.setattr(api, "model_tool_calling_support", lambda provider, model: None)

    result = await api.check_connection_endpoint("azure_foundry", user, None)

    assert result.status == status
    menu = await api.list_models_endpoint("azure_foundry", user, None)
    if status == "ok":
        assert len(result.models) == 4
        assert [model.id for model in menu.models] == [model.id for model in result.models]
    else:
        assert result.models is None
        assert len(menu.models) == 3
    discovery.assert_not_awaited()
