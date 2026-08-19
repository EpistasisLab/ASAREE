"""Unit tests for llm_model_discovery.discover_models (pure/mocked -- never a
real network call in an automated test)."""

from __future__ import annotations

import httpx
import pytest

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services import llm_model_discovery as discovery
from asaree.services.user_llm_settings import encrypt

_PROJECT_ENDPOINT = "https://my-resource.services.ai.azure.com/api/projects/my-project"


def _foundry_setting(api_base: str = "my-resource", azure_project_endpoint: str | None = None) -> UserLLMSetting:
    return UserLLMSetting(
        provider="azure_foundry",
        api_key_encrypted=encrypt("secret-key"),
        api_base=api_base,
        azure_project_endpoint=azure_project_endpoint,
    )


async def test_discover_models_anthropic_without_a_credential_returns_static_catalog() -> None:
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

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def get(self, url: str, *, params: dict, headers: dict) -> _FakeResponse:
        self._captured_headers.append(headers)
        return self._response


async def test_discover_models_azure_without_a_project_endpoint_asks_for_one_without_calling_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The project-scoped deployments call is the only listing call this
    # credential could make, so with no project endpoint there is nothing to
    # ask -- don't burn a request finding that out. (The classic Azure OpenAI
    # `{resource}/openai/deployments` endpoint used to be tried here as a
    # fallback; it 404s unconditionally on a Foundry resource hosting Claude
    # models, so it was removed rather than kept.)
    def _explode(**kwargs: object) -> None:
        raise AssertionError("no HTTP call should be made without a project endpoint")

    monkeypatch.setattr(discovery.httpx, "AsyncClient", _explode)

    models, source, note = await discovery.discover_models(provider="azure_foundry", setting=_foundry_setting())

    assert models == []
    assert source == "error"
    assert note == discovery.NO_PROJECT_ENDPOINT_NOTE
    assert "Project endpoint" in note


async def test_discover_models_azure_project_endpoint_maps_real_response_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real shape confirmed live against an actual Foundry project: {"value":
    # [{"name", "type", "modelName", "modelPublisher", ...}]}. `name` is the
    # deployment's own callable name; `modelName` is the underlying published
    # model id (better for capability lookup/label when they differ, e.g. a
    # deployment aliased as "prod-claude").
    captured_headers: list[dict] = []
    fake_response = _FakeResponse(
        {
            "value": [
                {
                    "name": "claude-opus-4-7",
                    "type": "ModelDeployment",
                    "modelName": "claude-opus-4-7",
                    "modelPublisher": "Anthropic",
                },
                {
                    "name": "prod-claude",
                    "type": "ModelDeployment",
                    "modelName": "claude-sonnet-4-6",
                    "modelPublisher": "Anthropic",
                },
            ]
        }
    )
    monkeypatch.setattr(
        discovery.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(fake_response, captured_headers=captured_headers),
    )

    setting = _foundry_setting(azure_project_endpoint=_PROJECT_ENDPOINT)
    models, source, note = await discovery.discover_models(provider="azure_foundry", setting=setting)

    assert source == "api"
    assert note is None
    assert [m.id for m in models] == ["claude-opus-4-7", "prod-claude"]  # sorted by id (the callable name)
    aliased = next(m for m in models if m.id == "prod-claude")
    assert aliased.label == "claude-sonnet-4-6"  # label is the underlying model, not the deployment alias
    assert captured_headers[0] == {"api-key": "secret-key"}


async def test_discover_models_azure_hits_only_the_project_deployments_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_urls: list[str] = []

    class _RecordingClient:
        async def __aenter__(self) -> _RecordingClient:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def get(self, url: str, *, params: dict, headers: dict) -> _FakeResponse:
            captured_urls.append(url)
            return _FakeResponse({"value": []})

    monkeypatch.setattr(discovery.httpx, "AsyncClient", lambda **kwargs: _RecordingClient())

    setting = _foundry_setting(azure_project_endpoint=_PROJECT_ENDPOINT)
    await discovery.discover_models(provider="azure_foundry", setting=setting)

    assert captured_urls == [f"{_PROJECT_ENDPOINT}/deployments"]


async def test_discover_models_azure_project_endpoint_failure_scrubs_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_response = _FakeResponse({}, status_code=401)
    monkeypatch.setattr(
        discovery.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(fake_response, captured_headers=[])
    )

    setting = _foundry_setting(azure_project_endpoint=_PROJECT_ENDPOINT)
    models, source, note = await discovery.discover_models(provider="azure_foundry", setting=setting)

    assert models == []
    assert source == "error"
    assert "secret-key" not in (note or "")


# --- Anthropic live discovery -------------------------------------------------
#
# Response shapes below are verbatim from a live GET /v1/models (trimmed to the
# fields this module reads). The capability tree is the whole reason Anthropic
# doesn't go through Motoro's _REGISTRY.


def _anthropic_setting(api_base: str | None = None) -> UserLLMSetting:
    return UserLLMSetting(provider="anthropic", api_key_encrypted=encrypt("secret-key"), api_base=api_base)


def _effort(*levels: str) -> dict:
    tree: dict = {"supported": bool(levels)}
    for level in ("low", "medium", "high", "xhigh", "max"):
        tree[level] = {"supported": level in levels}
    return tree


class _PagingClient:
    """Serves a queue of pages and records what it was asked for."""

    def __init__(self, pages: list[dict], captured: dict) -> None:
        self._pages = pages
        self._captured = captured

    async def __aenter__(self) -> _PagingClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def get(self, url: str, *, params: dict, headers: dict) -> _FakeResponse:
        self._captured.setdefault("urls", []).append(url)
        self._captured.setdefault("params", []).append(params)
        self._captured["headers"] = headers
        return _FakeResponse(self._pages.pop(0))


async def test_anthropic_reads_effort_levels_from_the_live_capability_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}
    page = {
        "data": [
            {
                "id": "claude-opus-5",
                "display_name": "Claude Opus 5",
                "capabilities": {"effort": _effort("low", "medium", "high", "xhigh", "max")},
            },
            {
                "id": "claude-opus-4-5-20251101",
                "display_name": "Claude Opus 4.5",
                "capabilities": {"effort": _effort("low", "medium", "high")},
            },
            {
                "id": "claude-haiku-4-5-20251001",
                "display_name": "Claude Haiku 4.5",
                "capabilities": {"effort": _effort()},
            },
        ],
        "has_more": False,
    }
    monkeypatch.setattr(discovery.httpx, "AsyncClient", lambda **kwargs: _PagingClient([page], captured))

    models, source, note = await discovery.discover_models(provider="anthropic", setting=_anthropic_setting())

    assert source == "api"
    assert note is None
    by_id = {m.id: m for m in models}

    # Per-model ladders really do differ -- the exact thing a single hardcoded
    # registry list gets wrong.
    assert by_id["claude-opus-5"].capabilities.effort_levels == ["low", "medium", "high", "xhigh", "max"]
    assert by_id["claude-opus-4-5-20251101"].capabilities.effort_levels == ["low", "medium", "high"]

    # An effort model is a no-temperature model, and vice versa.
    assert by_id["claude-opus-5"].capabilities.supports_effort is True
    assert by_id["claude-opus-5"].capabilities.supports_temperature is False
    assert by_id["claude-haiku-4-5-20251001"].capabilities.supports_effort is False
    assert by_id["claude-haiku-4-5-20251001"].capabilities.supports_temperature is True
    assert by_id["claude-haiku-4-5-20251001"].capabilities.effort_levels == []

    assert by_id["claude-opus-5"].label == "Claude Opus 5"
    assert captured["headers"] == {"x-api-key": "secret-key", "anthropic-version": "2023-06-01"}
    assert captured["urls"] == ["https://api.anthropic.com/v1/models"]


async def test_anthropic_default_effort_prefers_medium_but_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    page = {
        "data": [
            {"id": "has-medium", "display_name": "A", "capabilities": {"effort": _effort("low", "medium", "high")}},
            {"id": "no-medium", "display_name": "B", "capabilities": {"effort": _effort("high", "max")}},
        ],
        "has_more": False,
    }
    monkeypatch.setattr(discovery.httpx, "AsyncClient", lambda **kwargs: _PagingClient([page], {}))

    models, _, _ = await discovery.discover_models(provider="anthropic", setting=_anthropic_setting())
    by_id = {m.id: m for m in models}

    assert by_id["has-medium"].capabilities.default_effort == "medium"
    assert by_id["no-medium"].capabilities.default_effort == "high"  # first available, not a missing "medium"


async def test_anthropic_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    pages = [
        {"data": [{"id": "first", "capabilities": {}}], "has_more": True, "last_id": "first"},
        {"data": [{"id": "second", "capabilities": {}}], "has_more": False},
    ]
    monkeypatch.setattr(discovery.httpx, "AsyncClient", lambda **kwargs: _PagingClient(pages, captured))

    models, source, _ = await discovery.discover_models(provider="anthropic", setting=_anthropic_setting())

    assert source == "api"
    assert [m.id for m in models] == ["first", "second"]
    assert captured["params"][1]["after_id"] == "first"


async def test_anthropic_missing_capability_tree_defaults_to_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    # A model published before the capabilities field existed, or any shape we
    # don't recognise, must not silently claim effort support.
    page = {"data": [{"id": "mystery-model", "display_name": "Mystery"}], "has_more": False}
    monkeypatch.setattr(discovery.httpx, "AsyncClient", lambda **kwargs: _PagingClient([page], {}))

    models, _, _ = await discovery.discover_models(provider="anthropic", setting=_anthropic_setting())

    assert models[0].capabilities.supports_effort is False
    assert models[0].capabilities.supports_temperature is True
    assert models[0].capabilities.default_effort is None


async def test_anthropic_failure_falls_back_to_the_catalog_and_scrubs_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A stale catalog beats an empty dropdown, and "static" keeps callers from
    # flagging ids this list can't vouch for.
    monkeypatch.setattr(
        discovery.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(_FakeResponse({}, status_code=401), captured_headers=[]),
    )

    models, source, note = await discovery.discover_models(provider="anthropic", setting=_anthropic_setting())

    assert source == "static"
    assert models  # the curated catalog, not []
    assert any(m.id == "claude-sonnet-5" for m in models)
    assert note and "secret-key" not in note


async def test_anthropic_honours_a_custom_api_base(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        discovery.httpx,
        "AsyncClient",
        lambda **kwargs: _PagingClient([{"data": [], "has_more": False}], captured),
    )

    await discovery.discover_models(
        provider="anthropic", setting=_anthropic_setting(api_base="https://proxy.internal/")
    )

    assert captured["urls"] == ["https://proxy.internal/v1/models"]


async def test_anthropic_empty_listing_falls_back_to_the_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        discovery.httpx, "AsyncClient", lambda **kwargs: _PagingClient([{"data": [], "has_more": False}], {})
    )

    models, source, _ = await discovery.discover_models(provider="anthropic", setting=_anthropic_setting())

    assert source == "static"
    assert models


async def test_openai_stays_on_the_curated_catalog_without_calling_out(monkeypatch: pytest.MonkeyPatch) -> None:
    # GET /v1/models carries no capability data (probed live), and listing it
    # would surface ~124 mostly non-chat models each defaulting to
    # temperature-only. Don't spend a request to get a worse answer.
    def _explode(**kwargs: object) -> None:
        raise AssertionError("openai discovery should make no HTTP call")

    monkeypatch.setattr(discovery.httpx, "AsyncClient", _explode)

    setting = UserLLMSetting(provider="openai", api_key_encrypted=encrypt("secret-key"), api_base=None)
    models, source, note = await discovery.discover_models(provider="openai", setting=setting)

    assert source == "static"
    assert note is None
    assert models
