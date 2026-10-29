"""Exercise installed Motoro through the application boundary, without live providers."""

import importlib.metadata
import json
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from motoro.schemas.agent import ModelConfig
from motoro.services import providers
from motoro.services.llm_service import _sampling_kwargs
from motoro.services.provider_types import ModelListing, ProviderConfig

from asaree.api import llm_settings as api
from asaree.deps import get_current_user
from asaree.models.database import get_db
from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services import credential_resolver, llm_model_cache, model_config, user_llm_settings
from asaree.services.llm_connection_check import check_connection
from asaree.services.llm_model_discovery import discover_models
from asaree.services.provider_config import make_provider_config, provider_config
from asaree.services.user_llm_settings import encrypt

RELEASE_TAG = "v0.14.0"
RELEASE_COMMIT = "bdb1dd9d62dbcfb9b6e131baedda7787f71c5eb8"
PROVIDERS = ["anthropic", "openai", "azure_foundry", "openrouter", "local"]
OWNER = uuid.UUID("11111111-1111-1111-1111-111111111111")
SECRET = "provider-secret-for-tests"


@pytest.fixture(autouse=True)
def isolated_refresh_state(monkeypatch):
    llm_model_cache._recent.clear()
    monkeypatch.setattr(llm_model_cache, "consume_rate_limit", AsyncMock(return_value=(True, 0)))
    yield
    llm_model_cache._recent.clear()


def setting(provider):
    return UserLLMSetting(
        user_id=OWNER,
        provider=provider,
        api_key_encrypted=encrypt(SECRET),
        api_base="https://provider.test/v1",
        azure_project_endpoint="https://provider.test/api/projects/example" if provider == "azure_foundry" else None,
    )


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("outcome", [200, 401, 403, 429, 503, "timeout", "network", "malformed", "empty"])
async def test_application_and_independent_provider_outcomes_match(monkeypatch, provider, outcome):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        assert set(request.extensions["timeout"].values()) == {10.0}
        if outcome == "timeout":
            raise httpx.ReadTimeout(SECRET, request=request)
        if outcome == "network":
            raise httpx.ConnectError(SECRET, request=request)
        if outcome == "malformed":
            return httpx.Response(200, text="malformed " + SECRET)
        entries = (
            []
            if outcome == "empty"
            else [{"id": "example/chat", "name": "prod-chat", "modelName": "gpt-4o", "display_name": "Example"}]
        )
        return httpx.Response(
            outcome if isinstance(outcome, int) else 200,
            json={"data": entries, "value": entries, "error": SECRET},
        )

    transport = httpx.MockTransport(respond)
    client = httpx.AsyncClient

    def provider_client(**kwargs):
        return client(**{**kwargs, "transport": transport})

    monkeypatch.setattr(providers.httpx, "AsyncClient", provider_client)
    saved = setting(provider)
    config = ProviderConfig(
        provider=provider, api_key=SECRET, api_base=saved.api_base, azure_project_endpoint=saved.azure_project_endpoint
    )
    expected = await providers.discover_models(config, transport=transport)
    listing = await discover_models(provider=provider, setting=saved)
    models, note = listing.models, listing.note
    assert listing == expected
    check = await check_connection(provider=provider, setting=saved)
    assert check == await providers.check_connection(config, transport=transport)
    assert SECRET not in json.dumps([check.model_dump(), [entry.model_dump() for entry in models], note])
    assert SECRET not in repr(config) + config.model_dump_json()
    if expected.error is not None:
        assert expected.error.kind in {
            "credential_rejected",
            "rate_limited",
            "http",
            "timeout",
            "network",
            "invalid_response",
            "empty",
        }
    assert requests


async def test_defaults_and_provenance_survive_cache_and_execution(monkeypatch):
    entry = {
        "id": "new/model",
        "supported_parameters": ["temperature"],
        "default_parameters": {"temperature": 0, "stream": False, "stop": [], "extra": {}, "prefix": ""},
    }
    listing = await providers.discover_models(
        ProviderConfig(provider="openrouter"),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [entry]})),
    )
    encoded = llm_model_cache._dump(listing, now=100)
    cached = llm_model_cache._load(encoded)
    assert cached.fresh_until == 100 + 6 * 60 * 60
    assert cached.listing == listing
    monkeypatch.setattr(model_config, "get_setting", AsyncMock(return_value=None))
    monkeypatch.setattr(model_config, "discover_models_cached", AsyncMock(return_value=cached.listing))
    metadata = await model_config.resolve_model_metadata(
        provider="openrouter", model=entry["id"], db=None, owner_id=OWNER
    )
    assert metadata == providers.get_model_metadata(model=entry["id"], listing=listing)
    defaults = providers.get_model_defaults(model=entry["id"], listing=listing)
    assert metadata.defaults == defaults
    assert defaults.temperature.known and defaults.temperature.value == 0
    assert defaults.temperature.source == "provider"
    assert not defaults.effort.known and defaults.effort.source == "unknown"
    for value in defaults.additional.values():
        assert value.known and value.source == "provider"
    unknown = providers.get_model_metadata(model="unrecognized", listing=listing)
    assert not unknown.defaults.temperature.known
    assert unknown.capabilities.supports_temperature is None
    assert set(unknown.capability_sources.values()) == {"unknown"}

    @asynccontextmanager
    async def session():
        yield None

    monkeypatch.setattr(model_config, "get_session", session)
    config = await model_config.build_model_config(
        {"provider": "openrouter", "model": entry["id"], "temperature": 0.7}, owner_id=OWNER
    )
    assert config.temperature == 0.7
    assert config.resolved_capabilities.default_temperature == 0


@pytest.mark.parametrize("provider", ["openrouter", "anthropic"])
@pytest.mark.parametrize("preference", [None, 0.7])
async def test_defaults_without_support_survive_application_cache_without_changing_sampling(
    monkeypatch, provider, preference
):
    model = "defaults-only/model"
    entry = (
        {"id": model, "default_parameters": {"temperature": 0}}
        if provider == "openrouter"
        else {"id": model, "capabilities": {"temperature": {"default": 0}}}
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [entry]}))
    client = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: client(**{**kw, "transport": transport}))
    saved = setting(provider)
    listing = await discover_models(provider=provider, setting=saved)
    assert listing.source == "api"
    live = listing
    encoded = llm_model_cache._dump(live, now=100)
    cached = llm_model_cache._load(encoded).listing
    assert cached == live

    for listing in (live, cached):
        metadata = providers.get_model_metadata(model=model, listing=listing)
        defaults = providers.get_model_defaults(model=model, listing=listing)
        for info in (listing.models[0], metadata):
            assert info.defaults.temperature.known is True
            assert info.defaults.temperature.value == 0
            assert info.defaults.temperature.source == "provider"
            assert info.capabilities.supports_temperature is None
            assert info.capabilities.default_temperature is None
            assert info.capability_sources["supports_temperature"] == "unknown"
        assert defaults == metadata.defaults
        assert defaults.temperature.known is True
        assert defaults.temperature.value == 0
        assert defaults.temperature.source == "provider"

    monkeypatch.setattr(model_config, "get_setting", AsyncMock(return_value=saved))
    monkeypatch.setattr(model_config, "discover_models_cached", AsyncMock(return_value=cached))
    metadata = await model_config.resolve_model_metadata(provider=provider, model=model, db=None, owner_id=OWNER)
    assert metadata == live.models[0]

    @asynccontextmanager
    async def session():
        yield None

    monkeypatch.setattr(model_config, "get_session", session)
    data = {"provider": provider, "model": model}
    if preference is not None:
        data["temperature"] = preference
    original = ModelConfig(**data)
    expected_sampling = _sampling_kwargs(original, f"{provider}/{model}")
    resolved = await model_config.build_model_config(data, owner_id=OWNER)
    assert resolved.temperature == original.temperature
    assert resolved.effort == original.effort
    assert resolved.resolved_capabilities == metadata.capabilities
    assert _sampling_kwargs(resolved, f"{provider}/{model}") == expected_sampling == {}
    assert providers.get_model_defaults(model=model, listing=cached) == metadata.defaults


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_inference_uses_only_requested_users_saved_credentials(monkeypatch, provider):
    @asynccontextmanager
    async def session():
        yield None

    saved = setting(provider)
    lookup = AsyncMock(return_value=saved)
    monkeypatch.setattr(credential_resolver, "get_engine", lambda: None)
    monkeypatch.setattr(credential_resolver, "async_sessionmaker", lambda *a, **kw: session)
    monkeypatch.setattr(credential_resolver, "get_setting", lookup)
    config = ModelConfig(provider=provider, model="custom-model")
    actual = await credential_resolver.resolve(config, OWNER)
    expected = providers.get_inference_connection(
        ProviderConfig(
            provider=provider,
            api_key=SECRET,
            api_base=saved.api_base,
            azure_project_endpoint=saved.azure_project_endpoint,
        ),
        model=config.model,
    )
    assert actual == expected
    lookup.assert_awaited_once_with(None, user_id=OWNER, provider=provider)
    lookup.return_value = None
    with pytest.raises(credential_resolver.LLMCredentialNotConfiguredError):
        await credential_resolver.resolve(config, uuid.uuid4())


async def test_cache_client_creation_failure_is_a_cache_miss(monkeypatch):
    def unavailable():
        raise ConnectionError("cache unavailable")

    monkeypatch.setattr(llm_model_cache, "get_redis", unavailable)
    result = ModelListing(source="api")
    discover = AsyncMock(return_value=result)
    monkeypatch.setattr(llm_model_cache, "discover_models", discover)
    assert await llm_model_cache.discover_models_cached(user_id=OWNER, provider="local", setting=None) == result
    discover.assert_awaited_once()


def test_azure_resource_name_trailing_slash_keeps_existing_inference_connection():
    config = ProviderConfig(provider="azure_foundry", api_key=SECRET, api_base="my-resource/")
    connection = providers.get_inference_connection(config, model="prod-chat")
    assert connection["api_base"] == "https://my-resource.services.ai.azure.com"
    assert connection["model"] == "azure_ai/prod-chat"


async def test_azure_resource_name_trailing_slash_without_project_stays_inconclusive(monkeypatch):
    def no_network(**kwargs):
        raise AssertionError("No provider request should be made without a project endpoint")

    monkeypatch.setattr(providers.httpx, "AsyncClient", no_network)
    saved = setting("azure_foundry")
    saved.api_base = "my-resource/"
    saved.azure_project_endpoint = None
    result = await check_connection(provider="azure_foundry", setting=saved)
    assert result.status == "unknown"
    assert result.endpoint == saved.api_base


@pytest.fixture
async def endpoint(monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=OWNER)
    app.dependency_overrides[get_db] = lambda: None
    monkeypatch.setattr(api, "check_rate_limit", AsyncMock(return_value=(True, 0)))
    monkeypatch.setattr(api, "record_attempt", AsyncMock())
    monkeypatch.setattr(api, "get_setting", AsyncMock(return_value=None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield app, client


async def test_endpoints_keep_missing_credentials_public_catalog_and_limits(endpoint, monkeypatch):
    _, client = endpoint
    missing = await client.get("/llm-settings/openai/connection")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "No credential saved for this provider."}
    monkeypatch.setattr(llm_model_cache, "get_redis", lambda: SimpleNamespace(get=AsyncMock(return_value=None)))
    catalog = await client.get("/llm-settings/openai/models")
    assert catalog.status_code == 200
    assert set(catalog.json()) == {"models", "source", "note"}
    assert catalog.json()["source"] == "static"
    assert catalog.json()["models"]
    for action in ("connection", "models"):
        monkeypatch.setattr(api, "check_rate_limit", AsyncMock(return_value=(False, 23)))
        response = await client.get(f"/llm-settings/openai/{action}")
        assert response.status_code == 429
        assert response.json()["detail"]["retry_after_seconds"] == 23
        api.check_rate_limit.assert_awaited_once_with(
            f"{'connection-check' if action == 'connection' else 'model-discovery'}:{OWNER}:openai",
            limit=10,
            window_seconds=60,
        )


async def test_endpoint_authentication_is_required(endpoint):
    app, client = endpoint
    del app.dependency_overrides[get_current_user]
    for action in ("connection", "models", "model-capabilities?model=gpt-4o"):
        assert (await client.get(f"/llm-settings/openai/{action}")).status_code == 401


@pytest.mark.parametrize("project", [None, "", "   "])
async def test_missing_azure_project_endpoint_is_inconclusive_without_network(endpoint, monkeypatch, project):
    _, client = endpoint
    saved = setting("azure_foundry")
    saved.api_base = "example-resource"
    saved.azure_project_endpoint = project
    monkeypatch.setattr(api, "get_setting", AsyncMock(return_value=saved))

    def no_network(**kwargs):
        raise AssertionError("Missing project endpoints must not contact a provider")

    monkeypatch.setattr(providers.httpx, "AsyncClient", no_network)
    response = await client.get("/llm-settings/azure_foundry/connection")
    assert response.status_code == 200
    assert response.json()["status"] == "unknown"


@pytest.mark.parametrize("project", [None, "", "   "])
@pytest.mark.parametrize("updating", [False, True])
async def test_saving_empty_optional_endpoints_normalizes_before_storage(endpoint, monkeypatch, project, updating):
    app, client = endpoint
    saved = []
    db = SimpleNamespace(add=saved.append, flush=AsyncMock(), refresh=AsyncMock())
    app.dependency_overrides[get_db] = lambda: db
    existing = setting("azure_foundry") if updating else None
    monkeypatch.setattr(user_llm_settings, "get_setting", AsyncMock(return_value=existing))
    response = await client.put(
        "/llm-settings",
        json={
            "provider": "azure_foundry",
            "api_key": SECRET,
            "api_base": "example-resource",
            "azure_project_endpoint": project,
        },
    )
    assert response.status_code == 201
    assert response.json()["azure_project_endpoint"] is None
    stored = existing if updating else saved[0]
    assert stored.azure_project_endpoint is None
    monkeypatch.setattr(api, "get_setting", AsyncMock(return_value=stored))
    response = await client.get("/llm-settings/azure_foundry/connection")
    assert response.status_code == 200
    assert response.json()["status"] == "unknown"


async def test_valid_azure_project_still_replaces_stale_resource_field_on_save(endpoint, monkeypatch):
    app, client = endpoint
    saved = []
    db = SimpleNamespace(add=saved.append, flush=AsyncMock(), refresh=AsyncMock())
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(user_llm_settings, "get_setting", AsyncMock(return_value=None))
    project = "https://example-resource.services.ai.azure.com/api/projects/example"
    response = await client.put(
        "/llm-settings",
        json={
            "provider": "azure_foundry",
            "api_key": SECRET,
            "api_base": "stale-and-ignored",
            "azure_project_endpoint": f"  {project}  ",
        },
    )
    assert response.status_code == 201
    assert saved[0].api_base == "https://example-resource.services.ai.azure.com"
    assert saved[0].azure_project_endpoint == project


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("blank", ["", "   "])
async def test_saving_blank_optional_endpoints_stores_none(endpoint, monkeypatch, provider, blank):
    app, client = endpoint
    saved = []
    db = SimpleNamespace(add=saved.append, flush=AsyncMock(), refresh=AsyncMock())
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(user_llm_settings, "get_setting", AsyncMock(return_value=None))
    response = await client.put(
        "/llm-settings",
        json={"provider": provider, "api_key": SECRET, "api_base": blank, "azure_project_endpoint": blank},
    )
    assert response.status_code == 201
    assert response.json()["api_base"] is None
    assert response.json()["azure_project_endpoint"] is None
    assert saved[0].api_base is None
    assert saved[0].azure_project_endpoint is None


@pytest.mark.parametrize(
    "base", ["example-resource", "example-resource/", " https://example-resource.services.ai.azure.com/anthropic/ "]
)
async def test_existing_azure_inference_with_empty_optional_project(monkeypatch, base):
    saved = setting("azure_foundry")
    saved.api_base = base
    saved.azure_project_endpoint = ""

    @asynccontextmanager
    async def session():
        yield None

    monkeypatch.setattr(credential_resolver, "get_engine", lambda: None)
    monkeypatch.setattr(credential_resolver, "async_sessionmaker", lambda *a, **kw: session)
    monkeypatch.setattr(credential_resolver, "get_setting", AsyncMock(return_value=saved))
    config = ModelConfig(provider="azure_foundry", model="prod-chat")
    result = await credential_resolver.resolve(config, OWNER)
    assert result["api_base"] == "https://example-resource.services.ai.azure.com"
    assert result["model"] == "azure_ai/prod-chat"
    assert result["api_key"] == SECRET


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("base", [None, "", "   ", " https://provider.test/v1/ "])
def test_optional_endpoint_representations_remain_usable(provider, base):
    config = make_provider_config(provider=provider, api_key=SECRET, api_base=base, azure_project_endpoint="")
    assert config.api_base == ("https://provider.test/v1/" if base and base.strip() else None)
    assert config.azure_project_endpoint is None
    saved = setting(provider)
    saved.api_base = base
    saved.azure_project_endpoint = ""
    assert provider_config(provider=provider, setting=saved) == config


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    "field,value",
    [
        ("api_base", f"https://user:{SECRET}@provider.test"),
        ("api_base", f"https://provider.test?key={SECRET}"),
        ("api_base", "ftp://provider.test"),
        ("api_base", "https://provider.test:invalid"),
        ("azure_project_endpoint", f"https://provider.test#{SECRET}"),
        ("azure_project_endpoint", "https://[invalid"),
    ],
)
async def test_invalid_saved_and_submitted_configuration_is_controlled_and_secret_free(
    endpoint, monkeypatch, caplog, provider, field, value
):
    app, client = endpoint
    saved = setting(provider)
    setattr(saved, field, value)
    monkeypatch.setattr(api, "get_setting", AsyncMock(return_value=saved))
    monkeypatch.setattr(model_config, "get_setting", AsyncMock(return_value=saved))
    monkeypatch.setattr(llm_model_cache, "get_redis", lambda: SimpleNamespace(get=AsyncMock(return_value=None)))
    db = SimpleNamespace(add=AsyncMock(), flush=AsyncMock())
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(user_llm_settings, "get_setting", AsyncMock(return_value=None))

    def no_network(**kwargs):
        raise AssertionError("Invalid configuration must not contact a provider")

    monkeypatch.setattr(providers.httpx, "AsyncClient", no_network)
    for action in ("connection", "models", "model-capabilities?model=example"):
        response = await client.get(f"/llm-settings/{provider}/{action}")
        assert response.status_code == 422
        assert response.json()["detail"].startswith("Invalid provider configuration.")
        assert SECRET not in response.text
        assert value not in response.text
    response = await client.put("/llm-settings", json={"provider": provider, "api_key": SECRET, field: value})
    assert response.status_code == 422
    assert SECRET not in response.text + caplog.text
    db.flush.assert_not_awaited()
    db.add.assert_not_called()
    user_llm_settings.get_setting.assert_not_awaited()


async def test_real_azure_check_seeds_identical_endpoint_model_listing(endpoint, monkeypatch):
    _, client = endpoint
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"value": [{"name": "prod-chat", "modelName": "gpt-4o"}]})

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: real_client(**{**kw, "transport": transport}))
    monkeypatch.setattr(api, "get_setting", AsyncMock(return_value=setting("azure_foundry")))
    stored = {}

    async def put(key, value, *, ex):
        stored[key] = value

    redis = SimpleNamespace(get=AsyncMock(side_effect=lambda key: stored.get(key)), set=put)
    monkeypatch.setattr(llm_model_cache, "get_redis", lambda: redis)
    checked = await client.get("/llm-settings/azure_foundry/connection")
    menu = await client.get("/llm-settings/azure_foundry/models")
    assert checked.status_code == menu.status_code == 200
    assert checked.json()["status"] == "ok"
    assert checked.json()["models"] == menu.json()["models"]
    assert menu.json()["models"][0]["id"] == "prod-chat"
    assert menu.json()["models"][0]["supports_temperature"] is True
    assert len(requests) == 1


async def test_credential_changes_leave_other_credential_namespaces_intact(endpoint, monkeypatch):
    _, client = endpoint
    stored = {}

    async def put(key, value, *, ex):
        assert ex in (24 * 60 * 60, 30)
        stored[key] = value

    redis = SimpleNamespace(set=put)
    monkeypatch.setattr(llm_model_cache, "get_redis", lambda: redis)
    other = uuid.uuid4()
    saved = setting("openai")
    for owner, provider in ((OWNER, "openai"), (OWNER, "local"), (other, "openai")):
        await llm_model_cache.store_models_cache(user_id=owner, provider=provider, setting=saved, models=[])
    previous = dict(stored)
    monkeypatch.setattr(api, "upsert_setting", AsyncMock(return_value=saved))
    monkeypatch.setattr(api, "delete_setting", AsyncMock(return_value=True))
    response = await client.put("/llm-settings", json={"provider": "openai", "api_key": SECRET})
    assert response.status_code == 201
    assert SECRET not in response.text
    assert stored == previous
    api.upsert_setting.assert_awaited_once_with(
        None, user_id=OWNER, provider="openai", api_key=SECRET, api_base=None, azure_project_endpoint=None
    )
    response = await client.delete("/llm-settings/local")
    assert response.status_code == 204
    assert stored == previous
    api.delete_setting.assert_awaited_once_with(None, user_id=OWNER, provider="local")


@pytest.mark.parametrize("action", ["models", "model-capabilities?model=custom-model"])
async def test_all_discovery_routes_honor_shared_refresh_budget(endpoint, monkeypatch, action):
    _, client = endpoint
    monkeypatch.setattr(llm_model_cache, "get_redis", lambda: SimpleNamespace(get=AsyncMock(return_value=None)))
    monkeypatch.setattr(llm_model_cache, "consume_rate_limit", AsyncMock(return_value=(False, 23)))
    monkeypatch.setattr(model_config, "get_setting", AsyncMock(return_value=None))
    discover = AsyncMock(side_effect=AssertionError("No provider request when budget is exhausted"))
    monkeypatch.setattr(llm_model_cache, "discover_models", discover)
    response = await client.get(f"/llm-settings/anthropic/{action}")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "23"
    assert response.json()["detail"]["retry_after_seconds"] == 23
    discover.assert_not_awaited()


async def test_anthropic_outage_preserves_cached_live_metadata_and_error(endpoint, monkeypatch):
    _, client = endpoint
    saved = setting("anthropic")
    monkeypatch.setattr(api, "get_setting", AsyncMock(return_value=saved))
    monkeypatch.setattr(model_config, "get_setting", AsyncMock(return_value=saved))
    from motoro.services.provider_types import ModelInfo

    live = ModelListing(models=[ModelInfo(id="live-only-model")], source="api")
    key = llm_model_cache._key(OWNER, "anthropic", saved)
    stored = {key: llm_model_cache._dump(live, now=llm_model_cache.time.time() - llm_model_cache._FRESH_SECONDS - 1)}
    original = stored[key]

    async def put(key, value, **kwargs):
        stored[key] = value
        return True

    redis = SimpleNamespace(
        get=AsyncMock(side_effect=lambda key: stored.get(key)), set=put, execute_command=AsyncMock()
    )
    monkeypatch.setattr(llm_model_cache, "get_redis", lambda: redis)
    requests = []

    def unavailable(request):
        requests.append(request)
        raise httpx.ReadTimeout("unavailable", request=request)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(unavailable)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: real_client(**{**kw, "transport": transport}))
    menu = await client.get("/llm-settings/anthropic/models")
    assert menu.status_code == 200
    assert menu.json()["models"][0]["id"] == "live-only-model"
    assert menu.json()["note"] == llm_model_cache.STALE_NOTE
    cached = await llm_model_cache.discover_models_cached(user_id=OWNER, provider="anthropic", setting=saved)
    assert cached.error.kind == "timeout"
    assert stored[key] == original
    assert len(requests) == 1


def test_exact_installed_release_and_standalone_consumer(tmp_path):
    distribution = importlib.metadata.distribution("motoro")
    origin = json.loads(distribution.read_text("direct_url.json"))
    assert distribution.version == RELEASE_TAG.removeprefix("v")
    assert origin["vcs_info"]["commit_id"] == RELEASE_COMMIT
    assert origin["vcs_info"]["requested_revision"] == RELEASE_TAG
    assert "dir_info" not in origin
    assert Path(distribution.locate_file("motoro/services/providers.py")).is_file()
    consumer = """
import asyncio, sys
import httpx
from motoro.services.provider_types import ProviderConfig
from motoro.services.providers import discover_models, get_model_metadata, get_model_defaults
async def main():
    listing = await discover_models(ProviderConfig(provider='openrouter'), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'data': [{'id': 'new/model',
        'supported_parameters': ['temperature'], 'default_parameters': {'temperature': 0, 'stream': False}}]})))
    info = get_model_metadata(model='new/model', listing=listing)
    defaults = get_model_defaults(model='new/model', listing=listing)
    assert info.defaults == defaults
    assert defaults.temperature.known and defaults.temperature.value == 0
    assert defaults.additional['stream'].known and defaults.additional['stream'].value is False
    assert not get_model_defaults(model='unknown').temperature.known
    assert not any(name == 'asaree' or name.startswith('asaree.') for name in sys.modules)
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", consumer], cwd=tmp_path, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
