"""Unit tests for llm_model_discovery.discover_models (pure/mocked -- never a
real network call in an automated test)."""

from __future__ import annotations

import json

import httpx
import pytest
from motoro.services import providers

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


async def test_discover_models_anthropic_without_a_credential_returns_error() -> None:
    listing = await discovery.discover_models(provider="anthropic", setting=None)
    models, source, note = listing.models, listing.source, listing.note
    assert source == "error"
    assert note
    assert models == []


async def test_discover_models_openai_without_credentials_returns_error() -> None:
    listing = await discovery.discover_models(provider="openai", setting=None)
    models, source = listing.models, listing.source
    assert source == "error"
    assert models == []


async def test_discover_models_azure_without_setting_returns_error() -> None:
    listing = await discovery.discover_models(provider="azure_foundry", setting=None)
    models, source, note = listing.models, listing.source, listing.note
    assert models == []
    assert source == "error"
    assert note


async def test_discover_models_unsupported_provider_returns_error() -> None:
    listing = await discovery.discover_models(provider="bedrock", setting=None)
    models, source, note = listing.models, listing.source, listing.note
    assert models == []
    assert source == "error"
    assert "bedrock" in note


class _FakeResponse:
    def __init__(self, json_data: dict, status_code: int = 200) -> None:
        self._json_data = json_data
        self.text = json.dumps(json_data)
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

    async def get(self, url: str, *, params: dict, headers: dict, **kwargs) -> _FakeResponse:
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

    monkeypatch.setattr(providers.httpx, "AsyncClient", _explode)

    listing = await discovery.discover_models(provider="azure_foundry", setting=_foundry_setting())
    models, source, note = listing.models, listing.source, listing.note

    assert models == []
    assert source == "error"
    assert note == providers.NO_PROJECT_ENDPOINT_NOTE
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
        providers.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(fake_response, captured_headers=captured_headers),
    )

    setting = _foundry_setting(azure_project_endpoint=_PROJECT_ENDPOINT)
    listing = await discovery.discover_models(provider="azure_foundry", setting=setting)
    models, source, note = listing.models, listing.source, listing.note

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

        async def get(self, url: str, *, params: dict, headers: dict, **kwargs) -> _FakeResponse:
            captured_urls.append(url)
            return _FakeResponse({"value": []})

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _RecordingClient())

    setting = _foundry_setting(azure_project_endpoint=_PROJECT_ENDPOINT)
    await discovery.discover_models(provider="azure_foundry", setting=setting)

    assert captured_urls == [f"{_PROJECT_ENDPOINT}/deployments"]


async def test_discover_models_azure_project_endpoint_failure_scrubs_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_response = _FakeResponse({}, status_code=401)
    monkeypatch.setattr(
        providers.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(fake_response, captured_headers=[])
    )

    setting = _foundry_setting(azure_project_endpoint=_PROJECT_ENDPOINT)
    listing = await discovery.discover_models(provider="azure_foundry", setting=setting)
    models, source, note = listing.models, listing.source, listing.note

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

    async def get(self, url: str, *, params: dict, headers: dict, **kwargs) -> _FakeResponse:
        self._captured.setdefault("urls", []).append(url)
        self._captured.setdefault("params", []).append(dict(params))
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
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _PagingClient([page], captured))

    listing = await discovery.discover_models(provider="anthropic", setting=_anthropic_setting())
    models, source, note = listing.models, listing.source, listing.note

    assert source == "api"
    assert note is None
    by_id = {m.id: m for m in models}

    # Per-model ladders really do differ -- the exact thing a single hardcoded
    # registry list gets wrong.
    assert by_id["claude-opus-5"].capabilities.effort_levels == ["low", "medium", "high", "xhigh", "max"]
    assert by_id["claude-opus-4-5-20251101"].capabilities.effort_levels == ["low", "medium", "high"]

    # Effort support does not imply anything about temperature support.
    assert by_id["claude-opus-5"].capabilities.supports_effort is True
    assert by_id["claude-opus-5"].capabilities.supports_temperature is None
    assert by_id["claude-haiku-4-5-20251001"].capabilities.supports_effort is False
    assert by_id["claude-haiku-4-5-20251001"].capabilities.supports_temperature is None
    assert by_id["claude-haiku-4-5-20251001"].capabilities.effort_levels == []

    assert by_id["claude-opus-5"].label == "Claude Opus 5"
    assert captured["headers"] == {"x-api-key": "secret-key", "anthropic-version": "2023-06-01"}
    assert captured["urls"] == ["https://api.anthropic.com/v1/models"]


async def test_anthropic_missing_defaults_are_not_fabricated(monkeypatch: pytest.MonkeyPatch) -> None:
    page = {
        "data": [
            {"id": "has-medium", "display_name": "A", "capabilities": {"effort": _effort("low", "medium", "high")}},
            {"id": "no-medium", "display_name": "B", "capabilities": {"effort": _effort("high", "max")}},
        ],
        "has_more": False,
    }
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _PagingClient([page], {}))

    listing = await discovery.discover_models(provider="anthropic", setting=_anthropic_setting())
    models, _, _ = listing.models, listing.source, listing.note
    by_id = {m.id: m for m in models}

    assert by_id["has-medium"].capabilities.default_effort is None
    assert by_id["no-medium"].capabilities.default_effort is None


async def test_anthropic_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    pages = [
        {"data": [{"id": "first", "capabilities": {}}], "has_more": True, "last_id": "first"},
        {"data": [{"id": "second", "capabilities": {}}], "has_more": False},
    ]
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _PagingClient(pages, captured))

    listing = await discovery.discover_models(provider="anthropic", setting=_anthropic_setting())
    models, source, _ = listing.models, listing.source, listing.note

    assert source == "api"
    assert [m.id for m in models] == ["first", "second"]
    assert captured["params"][1]["after_id"] == "first"


async def test_anthropic_missing_capability_tree_remains_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    # Missing metadata uses the same effort fallback as request construction.
    page = {"data": [{"id": "mystery-model", "display_name": "Mystery"}], "has_more": False}
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _PagingClient([page], {}))

    listing = await discovery.discover_models(provider="anthropic", setting=_anthropic_setting())
    models, _, _ = listing.models, listing.source, listing.note

    assert models[0].capabilities.supports_effort is None
    assert models[0].capabilities.supports_temperature is None
    assert models[0].capabilities.default_effort is None


async def test_anthropic_failure_returns_no_fabricated_models_and_scrubs_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(_FakeResponse({}, status_code=401), captured_headers=[]),
    )

    listing = await discovery.discover_models(provider="anthropic", setting=_anthropic_setting())
    models, source, note = listing.models, listing.source, listing.note

    assert source == "error"
    assert models == []
    assert note and "secret-key" not in note


async def test_anthropic_honours_a_custom_api_base(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **kwargs: _PagingClient([{"data": [], "has_more": False}], captured),
    )

    await discovery.discover_models(
        provider="anthropic", setting=_anthropic_setting(api_base="https://proxy.internal/")
    )

    assert captured["urls"] == ["https://proxy.internal/v1/models"]


async def test_anthropic_empty_listing_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        providers.httpx, "AsyncClient", lambda **kwargs: _PagingClient([{"data": [], "has_more": False}], {})
    )

    listing = await discovery.discover_models(provider="anthropic", setting=_anthropic_setting())
    models, source, _ = listing.models, listing.source, listing.note

    assert source == "error"
    assert models == []


# --- OpenRouter / Local live discovery ----------------------------------
#
# Both hit a plain GET {base}/models with no `params` kwarg (unlike Azure's
# deployments call), so they get their own minimal fake client.


class _SimpleClient:
    def __init__(self, response: _FakeResponse, *, captured: dict) -> None:
        self._response = response
        self._captured = captured

    async def __aenter__(self) -> _SimpleClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def get(self, url: str, *, headers: dict, params: dict | None = None, **kwargs) -> _FakeResponse:
        self._captured.setdefault("urls", []).append(url)
        self._captured["headers"] = headers
        return self._response


def _openrouter_setting(api_key: str = "sk-or-real-key", api_base: str | None = None) -> UserLLMSetting:
    return UserLLMSetting(provider="openrouter", api_key_encrypted=encrypt(api_key), api_base=api_base)


async def test_openrouter_discovery_needs_no_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike every other live listing here, OpenRouter's catalog is public."""
    captured: dict = {}
    response = _FakeResponse({"data": [{"id": "anthropic/claude-sonnet-5", "name": "Claude Sonnet 5"}]})
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _SimpleClient(response, captured=captured))

    listing = await discovery.discover_models(provider="openrouter", setting=None)
    models, source, note = listing.models, listing.source, listing.note

    assert source == "api"
    assert note is None
    assert [m.id for m in models] == ["anthropic/claude-sonnet-5"]
    assert captured["headers"] == {}  # no Authorization header with no credential
    assert captured["urls"] == ["https://openrouter.ai/api/v1/models"]


async def test_openrouter_discovery_sends_bearer_token_when_credential_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}
    response = _FakeResponse({"data": []})
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _SimpleClient(response, captured=captured))

    await discovery.discover_models(provider="openrouter", setting=_openrouter_setting())

    assert captured["headers"] == {"Authorization": "Bearer sk-or-real-key"}


async def test_openrouter_missing_metadata_stays_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model names never supply capability facts."""
    response = _FakeResponse({"data": [{"id": "anthropic/claude-opus-5", "name": "Claude Opus 5"}]})
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _SimpleClient(response, captured={}))

    listing = await discovery.discover_models(provider="openrouter", setting=None)
    models, _, _ = listing.models, listing.source, listing.note

    assert models[0].capabilities.supports_effort is None
    assert models[0].capabilities.supports_temperature is None


@pytest.mark.parametrize(
    ("metadata", "temperature", "effort", "levels", "default"),
    [
        (
            {"supported_parameters": ["temperature", "reasoning"], "reasoning": {"mandatory": True}},
            True,
            False,
            None,
            None,
        ),
        ({"supported_parameters": []}, False, False, None, None),
        ({}, None, None, None, None),
        (
            {
                "supported_parameters": ["temperature", "reasoning_effort"],
                "reasoning": {"supported_efforts": ["xhigh", "medium", "low", "none"], "default_effort": "xhigh"},
            },
            True,
            True,
            ["xhigh", "medium", "low", "none"],
            "xhigh",
        ),
        (
            {
                "supported_parameters": ["reasoning"],
                "reasoning": {"supported_efforts": ["high", "low", "none"], "mandatory": True},
            },
            False,
            True,
            ["high", "low"],
            None,
        ),
        (
            {"supported_parameters": ["reasoning"], "reasoning": {"supported_efforts": None}},
            False,
            True,
            None,
            None,
        ),
        ({"supported_parameters": ["reasoning_effort"]}, False, True, None, None),
        (
            {
                "supported_parameters": ["reasoning"],
                "reasoning": {"supported_efforts": ["none", "low", "medium"], "default_effort": "none"},
            },
            False,
            True,
            ["none", "low", "medium"],
            "none",
        ),
        (
            {
                "supported_parameters": ["reasoning"],
                "reasoning": {"supported_efforts": ["high", "low"], "default_effort": "max"},
            },
            False,
            True,
            ["high", "low"],
            None,
        ),
    ],
)
async def test_openrouter_reads_sampling_metadata(monkeypatch, metadata, temperature, effort, levels, default):
    response = _FakeResponse({"data": [{"id": "qwen/qwen3.8-27b", **metadata}]})
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _SimpleClient(response, captured={}))
    listing = await discovery.discover_models(provider="openrouter", setting=None)
    models, _, _ = listing.models, listing.source, listing.note
    caps = models[0].capabilities
    assert caps.supports_temperature is temperature
    assert caps.supports_effort is effort
    assert caps.effort_levels == levels
    assert caps.default_effort == default


@pytest.mark.parametrize("default", [0.0, 1.0, None])
async def test_openrouter_temperature_defaults_and_known_mode_restrictions(monkeypatch, default):
    entry = {
        "id": "openai/gpt-5.2",
        "supported_parameters": ["temperature", "reasoning_effort"],
        "default_parameters": {"temperature": default},
        "reasoning": {"supported_efforts": ["none", "low", "high"], "default_effort": "none"},
    }
    response = _FakeResponse({"data": [entry]})
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _SimpleClient(response, captured={}))
    listing = await discovery.discover_models(provider="openrouter", setting=None)
    models, _, _ = listing.models, listing.source, listing.note
    caps = models[0].capabilities
    assert caps.default_temperature == default
    assert caps.supports_temperature is caps.supports_effort is True
    assert caps.temperature_effort_levels is None


async def test_anthropic_explicit_defaults_and_independent_temperature_support(monkeypatch):
    entry = {
        "id": "custom-model",
        "capabilities": {
            "temperature": {"supported": True, "default": 0.0},
            "effort": {"supported": True, "low": {"supported": True}, "default_effort": "low"},
        },
    }
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _PagingClient([{"data": [entry]}], {}))
    listing = await discovery.discover_models(provider="anthropic", setting=_anthropic_setting())
    models, _, _ = listing.models, listing.source, listing.note
    caps = models[0].capabilities
    assert caps.supports_temperature is caps.supports_effort is True
    assert caps.default_temperature == 0.0
    assert caps.default_effort == "low"


async def test_openrouter_live_metadata_overrides_claude_registry(monkeypatch):
    response = _FakeResponse(
        {
            "data": [
                {
                    "id": "anthropic/claude-opus-5",
                    "supported_parameters": ["temperature"],
                }
            ]
        }
    )
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _SimpleClient(response, captured={}))
    listing = await discovery.discover_models(provider="openrouter", setting=None)
    models, _, _ = listing.models, listing.source, listing.note
    assert models[0].capabilities.supports_temperature is True
    assert models[0].capabilities.supports_effort is False


async def test_openrouter_honours_a_custom_api_base(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    response = _FakeResponse({"data": []})
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _SimpleClient(response, captured=captured))

    await discovery.discover_models(
        provider="openrouter", setting=_openrouter_setting(api_base="https://proxy.internal")
    )

    assert captured["urls"] == ["https://proxy.internal/models"]


async def test_openrouter_failure_scrubs_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse({}, status_code=401)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _SimpleClient(response, captured={}))

    listing = await discovery.discover_models(provider="openrouter", setting=_openrouter_setting())
    models, source, note = listing.models, listing.source, listing.note

    assert models == []
    assert source == "error"
    assert "sk-or-real-key" not in (note or "")


def _local_setting(api_key: str = "", api_base: str | None = "http://localhost:8000/v1") -> UserLLMSetting:
    return UserLLMSetting(provider="local", api_key_encrypted=encrypt(api_key), api_base=api_base)


async def test_local_unknown_models_remain_unknown_without_changing_known_claude(monkeypatch):
    response = _FakeResponse({"data": [{"id": "qwen3.8-27b"}, {"id": "claude-opus-5"}]})
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _SimpleClient(response, captured={}))
    listing = await discovery.discover_models(provider="local", setting=_local_setting())
    models, _, _ = listing.models, listing.source, listing.note
    by_id = {model.id: model.capabilities for model in models}
    assert by_id["qwen3.8-27b"].supports_effort is None
    assert by_id["qwen3.8-27b"].supports_temperature is None
    assert by_id["claude-opus-5"].supports_effort is None


async def test_local_discovery_without_a_base_url_returns_error() -> None:
    listing = await discovery.discover_models(provider="local", setting=None)
    models, source, note = listing.models, listing.source, listing.note
    assert models == []
    assert source == "error"
    assert note


async def test_local_discovery_uses_placeholder_key_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    response = _FakeResponse({"data": [{"id": "llama-3-70b-instruct"}]})
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _SimpleClient(response, captured=captured))

    listing = await discovery.discover_models(provider="local", setting=_local_setting())
    models, source, note = listing.models, listing.source, listing.note

    assert source == "api"
    assert note is None
    assert [m.id for m in models] == ["llama-3-70b-instruct"]
    assert captured["headers"] == {"Authorization": "Bearer not-needed"}


async def test_local_discovery_no_listing_route_surfaces_as_error_source_so_the_note_renders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server with no GET /models route is a normal, expected outcome -- not
    a real error -- but ModelNodeInspector.tsx only renders `note` when
    `source == "error"`, so that's the source used to actually surface it."""
    response = _FakeResponse({}, status_code=404)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _SimpleClient(response, captured={}))

    listing = await discovery.discover_models(provider="local", setting=_local_setting())
    models, source, note = listing.models, listing.source, listing.note

    assert models == []
    assert source == "error"
    assert listing.error is not None and listing.error.http_status == 404
    assert note


async def test_openai_lists_actual_models_without_inventing_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    response = _FakeResponse({"data": [{"id": "future-model"}]})
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _SimpleClient(response, captured=captured))

    setting = UserLLMSetting(provider="openai", api_key_encrypted=encrypt("secret-key"), api_base=None)
    listing = await discovery.discover_models(provider="openai", setting=setting)
    models, source, note = listing.models, listing.source, listing.note

    assert source == "api"
    assert note is None
    assert models
    assert captured["headers"] == {"Authorization": "Bearer secret-key"}
    assert models[0].id == "future-model"
    assert models[0].capabilities.supports_temperature is None
