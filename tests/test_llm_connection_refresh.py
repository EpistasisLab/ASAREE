"""Connection button must invalidate the Azure deployment cache."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from asaree.api import llm_settings as api


@pytest.mark.parametrize("status", ["ok", "failed", "unknown"])
async def test_azure_connection_refreshes_cache_only_when_successful(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(api, "check_rate_limit", AsyncMock(return_value=(True, 0)))
    monkeypatch.setattr(api, "record_attempt", AsyncMock())
    monkeypatch.setattr(api, "get_setting", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        api,
        "check_connection",
        AsyncMock(return_value=SimpleNamespace(status=status, detail="checked", endpoint="project")),
    )
    invalidate = AsyncMock()
    monkeypatch.setattr(api, "invalidate_models_cache", invalidate)

    result = await api.check_connection_endpoint("azure_foundry", user, None)

    assert result.status == status
    if status == "ok":
        invalidate.assert_awaited_once_with(user_id=user.id, provider="azure_foundry")
    else:
        invalidate.assert_not_awaited()
