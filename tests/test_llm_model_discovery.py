"""Unit tests for llm_model_discovery.discover_models (pure/mocked -- never a
real network call in an automated test)."""

from __future__ import annotations

import httpx
import pytest

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services import llm_model_discovery as discovery
from asaree.services.user_llm_settings import encrypt


def _foundry_setting(api_base: str = "my-resource") -> UserLLMSetting:
    return UserLLMSetting(provider="azure_foundry", api_key_encrypted=encrypt("secret-key"), api_base=api_base)


async def test_discover_models_anthropic_returns_static_catalog() -> None:
    models, source, note = await discovery.discover_models(provider="anthropic", setting=None)
    assert source == "static"
    assert note is None
    assert any(m.id == "claude-sonnet-5" for m in models)
    assert all(m.id for m in models)


async def test_discover_models_openai_returns_static_catalog() -> None:
    models, source, note = await discovery.discover_models(provider="openai", setting=None)
    assert source == "static"
    assert models  # non-empty
    assert all(m.id for m in models)


async def test_discover_models_azure_without_setting_returns_error() -> None:
    models, source, note = await discovery.discover_models(provider="azure_foundry", setting=None)
    assert models == []
    assert source == "error"
    assert note


async def test_discover_models_unsupported_provider_returns_error() -> None:
    models, source, note = await discovery.discover_models(provider="bedrock", setting=None)
    assert models == []
    assert source == "error"
    assert "bedrock" in note


class _FakeResponse:
    def __init__(self, json_data: dict, status_code: int = 200) -> None:
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    def json(self) -> dict:
        return self._json_data


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse, *, captured_headers: list[dict]) -> None:
        self._response = response
        self._captured_headers = captured_headers

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def get(self, url: str, *, params: dict, headers: dict) -> _FakeResponse:
        self._captured_headers.append(headers)
        return self._response


async def test_discover_models_azure_success_maps_deployments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_headers: list[dict] = []
    fake_response = _FakeResponse({"data": [{"id": "my-gpt4-deployment"}, {"id": "claude-sonnet-5"}]})
    monkeypatch.setattr(
        discovery.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(fake_response, captured_headers=captured_headers)
    )

    models, source, note = await discovery.discover_models(provider="azure_foundry", setting=_foundry_setting())

    assert source == "api"
    assert note is None
    assert [m.id for m in models] == ["claude-sonnet-5", "my-gpt4-deployment"]  # sorted
    # The deployment named after a known Claude model resolves real capabilities.
    claude_entry = next(m for m in models if m.id == "claude-sonnet-5")
    assert claude_entry.capabilities.supports_effort is True
    # api-key header auth, not Authorization/Bearer.
    assert captured_headers[0] == {"api-key": "secret-key"}


async def test_discover_models_azure_failure_scrubs_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_response = _FakeResponse({}, status_code=401)
    monkeypatch.setattr(discovery.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(fake_response, captured_headers=[]))

    models, source, note = await discovery.discover_models(provider="azure_foundry", setting=_foundry_setting())

    assert models == []
    assert source == "error"
    assert "secret-key" not in (note or "")


async def test_discover_models_azure_404_gets_a_specific_friendly_note(monkeypatch: pytest.MonkeyPatch) -> None:
    # A services.ai.azure.com host has no api-key-authenticated deployment-
    # listing endpoint at all (confirmed against ARES's own discovery code) --
    # this is the expected, common case for a Foundry resource hosting Claude
    # models, not a raw httpx exception dump.
    fake_response = _FakeResponse({}, status_code=404)
    monkeypatch.setattr(
        discovery.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(fake_response, captured_headers=[])
    )

    models, source, note = await discovery.discover_models(provider="azure_foundry", setting=_foundry_setting())

    assert models == []
    assert source == "error"
    assert note == (
        "This Azure resource has no deployment-listing API available -- enter your deployment's name directly "
        "in the Model field below (expected for a Foundry resource hosting Claude models)."
    )
