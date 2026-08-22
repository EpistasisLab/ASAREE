"""Unit tests for llm_connection_check.check_connection (pure/mocked -- never
a real network call in an automated test)."""

from __future__ import annotations

import httpx
import pytest

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services import llm_connection_check as check
from asaree.services.user_llm_settings import encrypt

_PROJECT_ENDPOINT = "https://my-resource.services.ai.azure.com/api/projects/my-project"


def _setting(
    provider: str, *, api_base: str | None = None, azure_project_endpoint: str | None = None
) -> UserLLMSetting:
    return UserLLMSetting(
        provider=provider,
        api_key_encrypted=encrypt("secret-key"),
        api_base=api_base,
        azure_project_endpoint=azure_project_endpoint,
    )


class _FakeClient:
    """Records (url, headers) and replays a canned status code."""

    def __init__(self, status_code: int, *, calls: list[tuple[str, dict]], raises: Exception | None = None) -> None:
        self._status_code = status_code
        self._calls = calls
        self._raises = raises

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def get(self, url: str, *, headers: dict) -> httpx.Response:
        self._calls.append((url, headers))
        if self._raises is not None:
            raise self._raises
        return httpx.Response(self._status_code, request=httpx.Request("GET", url))


def _patch_client(monkeypatch: pytest.MonkeyPatch, status_code: int = 200, raises: Exception | None = None) -> list:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        check.httpx, "AsyncClient", lambda **kwargs: _FakeClient(status_code, calls=calls, raises=raises)
    )
    return calls


async def test_openai_success_uses_bearer_auth_on_the_models_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_client(monkeypatch)

    result = await check.check_connection(provider="openai", setting=_setting("openai"))

    assert result.status == "ok"
    url, headers = calls[0]
    assert url == "https://api.openai.com/v1/models"
    assert headers == {"Authorization": "Bearer secret-key"}
    # The wording must not overpromise -- a 200 here says nothing about quota.
    assert "quota" in result.detail


async def test_anthropic_success_uses_api_key_header_and_a_version(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_client(monkeypatch)

    result = await check.check_connection(provider="anthropic", setting=_setting("anthropic"))

    assert result.status == "ok"
    url, headers = calls[0]
    assert url == "https://api.anthropic.com/v1/models"
    assert headers["x-api-key"] == "secret-key"
    assert headers["anthropic-version"]


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [
        # OpenAI's own convention includes /v1; Anthropic's doesn't; a user
        # pasting a proxy base may do either, or paste the full models URL.
        ("https://proxy.internal/v1", "https://proxy.internal/v1/models"),
        ("https://proxy.internal", "https://proxy.internal/v1/models"),
        ("https://proxy.internal/v1/", "https://proxy.internal/v1/models"),
        ("https://proxy.internal/v1/models", "https://proxy.internal/v1/models"),
    ],
)
async def test_custom_api_base_is_normalized(monkeypatch: pytest.MonkeyPatch, api_base: str, expected: str) -> None:
    calls = _patch_client(monkeypatch)

    await check.check_connection(provider="openai", setting=_setting("openai", api_base=api_base))

    assert calls[0][0] == expected


@pytest.mark.parametrize("status_code", [401, 403])
async def test_rejected_key_is_a_failure(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    _patch_client(monkeypatch, status_code=status_code)

    result = await check.check_connection(provider="openai", setting=_setting("openai"))

    assert result.status == "failed"
    assert str(status_code) in result.detail


async def test_rate_limited_provider_is_inconclusive_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 429 means the provider identified the caller in order to throttle
    # them -- reporting that as a bad key would send the user rotating a key
    # that's fine.
    _patch_client(monkeypatch, status_code=429)

    result = await check.check_connection(provider="openai", setting=_setting("openai"))

    assert result.status == "unknown"


async def test_transport_failure_scrubs_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, raises=httpx.ConnectError("no route to host (key=secret-key)"))

    result = await check.check_connection(provider="openai", setting=_setting("openai"))

    assert result.status == "failed"
    assert "secret-key" not in result.detail
    assert "***" in result.detail


async def test_azure_success_delegates_to_deployment_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_discover(*, provider: str, setting: UserLLMSetting):
        return [object(), object()], "api", None

    monkeypatch.setattr(check, "discover_models", fake_discover)

    setting = _setting("azure_foundry", api_base="my-resource", azure_project_endpoint=_PROJECT_ENDPOINT)
    result = await check.check_connection(provider="azure_foundry", setting=setting)

    assert result.status == "ok"
    assert "2 deployment" in result.detail
    assert result.endpoint == _PROJECT_ENDPOINT


async def test_azure_without_a_project_endpoint_is_unknown_not_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    # No project endpoint means no free listing call exists for this
    # credential -- which says nothing about whether it can infer (inference
    # only needs api_base). Must not even attempt discovery.
    async def fake_discover(*, provider: str, setting: UserLLMSetting):
        raise AssertionError("discovery should not run without a project endpoint")

    monkeypatch.setattr(check, "discover_models", fake_discover)

    result = await check.check_connection(provider="azure_foundry", setting=_setting("azure_foundry", api_base="r"))

    assert result.status == "unknown"
    assert "can't be checked" in result.detail


async def test_azure_real_discovery_error_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_discover(*, provider: str, setting: UserLLMSetting):
        return [], "error", "Could not reach Azure Foundry to list models: 401"

    monkeypatch.setattr(check, "discover_models", fake_discover)

    setting = _setting("azure_foundry", api_base="r", azure_project_endpoint=_PROJECT_ENDPOINT)
    result = await check.check_connection(provider="azure_foundry", setting=setting)

    assert result.status == "failed"


async def test_openrouter_success_uses_bearer_auth_on_the_models_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_client(monkeypatch)

    result = await check.check_connection(provider="openrouter", setting=_setting("openrouter"))

    assert result.status == "ok"
    url, headers = calls[0]
    assert url == "https://openrouter.ai/api/v1/models"
    assert headers == {"Authorization": "Bearer secret-key"}


async def test_local_success_delegates_to_model_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_discover(*, provider: str, setting: UserLLMSetting):
        return [object(), object(), object()], "api", None

    monkeypatch.setattr(check, "discover_models", fake_discover)

    setting = _setting("local", api_base="http://localhost:8000/v1")
    result = await check.check_connection(provider="local", setting=setting)

    assert result.status == "ok"
    assert "3 model" in result.detail
    assert result.endpoint == "http://localhost:8000/v1"


async def test_local_without_a_base_url_is_unknown_not_failed() -> None:
    result = await check.check_connection(provider="local", setting=_setting("local", api_base=None))

    assert result.status == "unknown"
    assert "can't be checked" in result.detail


async def test_local_no_listing_route_is_unknown_not_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server with no GET /models route is a normal, expected outcome, not
    proof the credential is bad -- same posture as Azure's no-project-endpoint
    case."""

    async def fake_discover(*, provider: str, setting: UserLLMSetting):
        return [], "error", "This server didn't return a model list."

    monkeypatch.setattr(check, "discover_models", fake_discover)

    setting = _setting("local", api_base="http://localhost:8000/v1")
    result = await check.check_connection(provider="local", setting=setting)

    assert result.status == "unknown"


async def test_unsupported_provider_is_unknown() -> None:
    result = await check.check_connection(provider="bedrock", setting=_setting("bedrock"))

    assert result.status == "unknown"
    assert "bedrock" in result.detail
