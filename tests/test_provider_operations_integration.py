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
from motoro.services.provider_types import ProviderConfig
from pydantic import ValidationError

from asaree.api import llm_settings as api
from asaree.deps import get_current_user
from asaree.models.database import get_db
from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services import credential_resolver, llm_model_cache, model_config
from asaree.services.llm_connection_check import check_connection
from asaree.services.llm_model_discovery import discover_models
from asaree.services.user_llm_settings import encrypt

REVISION = "1a1955144230dab0db5c7b390d5b292f101da490"
PROVIDERS = ["anthropic", "openai", "azure_foundry", "openrouter", "local"]
OWNER = uuid.UUID("11111111-1111-1111-1111-111111111111")
SECRET = "provider-secret-for-tests"


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
        assert kwargs["timeout"] == 10.0
        assert kwargs["follow_redirects"] is False
        return client(**{**kwargs, "transport": transport})

    monkeypatch.setattr(providers.httpx, "AsyncClient", provider_client)
    saved = setting(provider)
    config = ProviderConfig(
        provider=provider, api_key=SECRET, api_base=saved.api_base, azure_project_endpoint=saved.azure_project_endpoint
    )
    expected = await providers.discover_models(config, transport=transport)
    models, source, note = await discover_models(provider=provider, setting=saved)
    assert (models, source, note) == (expected.models, expected.source, expected.note)
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
    encoded = llm_model_cache._dump(listing.models, listing.source, listing.note, now=100)
    models, source, note, fresh_until = llm_model_cache._load(encoded)
    assert fresh_until == 100 + 6 * 60 * 60
    assert models == listing.models
    monkeypatch.setattr(model_config, "get_setting", AsyncMock(return_value=None))
    monkeypatch.setattr(model_config, "discover_models_cached", AsyncMock(return_value=(models, source, note)))
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
    result = ([], "api", None)
    discover = AsyncMock(return_value=result)
    monkeypatch.setattr(llm_model_cache, "discover_models", discover)
    assert await llm_model_cache.discover_models_cached(user_id=OWNER, provider="local", setting=None) == result
    discover.assert_awaited_once()


@pytest.mark.xfail(
    strict=True,
    raises=ValidationError,
    reason="Motoro 1a195514: ProviderConfig must accept the baseline Azure resource-name trailing slash",
)
def test_azure_resource_name_trailing_slash_keeps_existing_inference_connection():
    config = ProviderConfig(provider="azure_foundry", api_key=SECRET, api_base="my-resource/")
    connection = providers.get_inference_connection(config, model="prod-chat")
    assert connection["api_base"] == "https://my-resource.services.ai.azure.com"
    assert connection["model"] == "azure_ai/prod-chat"


@pytest.mark.xfail(
    strict=True,
    raises=ValidationError,
    reason="Motoro 1a195514: Azure resource-name validation must not prevent the no-project inconclusive check",
)
async def test_azure_resource_name_trailing_slash_without_project_stays_inconclusive():
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


async def test_credential_changes_invalidate_only_callers_provider(endpoint, monkeypatch):
    _, client = endpoint
    stored = {}

    async def put(key, value, *, ex):
        assert ex == 24 * 60 * 60
        stored[key] = value

    async def delete(key):
        stored.pop(key, None)

    redis = SimpleNamespace(set=put, delete=delete)
    monkeypatch.setattr(llm_model_cache, "get_redis", lambda: redis)
    other = uuid.uuid4()
    for owner, provider in ((OWNER, "openai"), (OWNER, "local"), (other, "openai")):
        await llm_model_cache.store_models_cache(user_id=owner, provider=provider, models=[])
    saved = setting("openai")
    monkeypatch.setattr(api, "upsert_setting", AsyncMock(return_value=saved))
    monkeypatch.setattr(api, "delete_setting", AsyncMock(return_value=True))
    response = await client.put("/llm-settings", json={"provider": "openai", "api_key": SECRET})
    assert response.status_code == 201
    assert SECRET not in response.text
    assert llm_model_cache._key(OWNER, "openai") not in stored
    assert len(stored) == 2
    api.upsert_setting.assert_awaited_once_with(
        None, user_id=OWNER, provider="openai", api_key=SECRET, api_base=None, azure_project_endpoint=None
    )
    response = await client.delete("/llm-settings/local")
    assert response.status_code == 204
    assert list(stored) == [llm_model_cache._key(other, "openai")]
    api.delete_setting.assert_awaited_once_with(None, user_id=OWNER, provider="local")


def test_exact_installed_revision_and_standalone_consumer(tmp_path):
    distribution = importlib.metadata.distribution("motoro")
    origin = json.loads(distribution.read_text("direct_url.json"))
    assert origin["vcs_info"]["commit_id"] == REVISION
    assert origin["vcs_info"]["requested_revision"] == REVISION
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
