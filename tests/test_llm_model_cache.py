"""Unit tests for llm_model_cache -- the Redis layer in front of model
discovery. Fake Redis throughout; never a real connection or network call."""

from __future__ import annotations

import json
import uuid

import pytest
from motoro.services.model_capabilities import ModelCapabilities

from asaree.services import llm_model_cache as cache
from asaree.services.llm_model_discovery import ModelInfo

_USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class _FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.fail = fail
        self.set_calls: list[str] = []

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise ConnectionError("redis is down")
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        if self.fail:
            raise ConnectionError("redis is down")
        self.set_calls.append(key)
        self.store[key] = value

    async def delete(self, key: str) -> None:
        if self.fail:
            raise ConnectionError("redis is down")
        self.store.pop(key, None)


def _install(monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis) -> None:
    monkeypatch.setattr(cache, "get_redis", lambda: redis)


def _stub_discovery(
    monkeypatch: pytest.MonkeyPatch, result: tuple[list[ModelInfo], str, str | None], calls: list[str]
) -> None:
    async def _fake(*, provider: str, setting: object) -> tuple[list[ModelInfo], str, str | None]:
        calls.append(provider)
        return result

    monkeypatch.setattr(cache, "discover_models", _fake)


def _model(model_id: str = "claude-opus-4-7") -> ModelInfo:
    return ModelInfo(
        id=model_id,
        label="Claude Opus 4.7",
        capabilities=ModelCapabilities(
            supports_temperature=False, supports_effort=True, effort_levels=["low", "high"], default_effort="high"
        ),
    )


async def test_fresh_cache_hit_does_not_call_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    key = f"llm:models:{_USER_ID}:azure_foundry"
    redis.store[key] = cache._dump([_model()], "api", None, now=cache.time.time())
    _install(monkeypatch, redis)
    calls: list[str] = []
    _stub_discovery(monkeypatch, ([], "error", "should not be reached"), calls)

    models, source, note = await cache.discover_models_cached(user_id=_USER_ID, provider="azure_foundry", setting=None)

    assert calls == []  # the whole point
    assert source == "api"
    assert note is None
    assert [m.id for m in models] == ["claude-opus-4-7"]


async def test_cache_roundtrips_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    # The dial the inspector renders (temperature vs effort) travels through
    # JSON, so a lossy round-trip would silently show the wrong control.
    redis = _FakeRedis()
    _install(monkeypatch, redis)
    _stub_discovery(monkeypatch, ([_model()], "api", None), [])

    await cache.discover_models_cached(user_id=_USER_ID, provider="azure_foundry", setting=None)
    models, _, _ = await cache.discover_models_cached(user_id=_USER_ID, provider="azure_foundry", setting=None)

    caps = models[0].capabilities
    assert caps.supports_temperature is False
    assert caps.supports_effort is True
    assert caps.effort_levels == ["low", "high"]
    assert caps.default_effort == "high"


async def test_live_results_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    _install(monkeypatch, redis)
    calls: list[str] = []
    _stub_discovery(monkeypatch, ([_model()], "api", None), calls)

    await cache.discover_models_cached(user_id=_USER_ID, provider="azure_foundry", setting=None)
    await cache.discover_models_cached(user_id=_USER_ID, provider="azure_foundry", setting=None)

    assert calls == ["azure_foundry"]  # second call served from cache


async def test_static_catalog_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    # Caching it would buy nothing (no network call to save) and add a second
    # place for it to go stale.
    redis = _FakeRedis()
    _install(monkeypatch, redis)
    _stub_discovery(monkeypatch, ([_model()], "static", None), [])

    await cache.discover_models_cached(user_id=_USER_ID, provider="anthropic", setting=None)

    assert redis.set_calls == []


async def test_errors_are_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    _install(monkeypatch, redis)
    _stub_discovery(monkeypatch, ([], "error", "no project endpoint"), [])

    models, source, note = await cache.discover_models_cached(user_id=_USER_ID, provider="azure_foundry", setting=None)

    assert redis.set_calls == []
    assert source == "error"
    assert note == "no project endpoint"
    assert models == []


async def test_stale_entry_is_served_when_the_provider_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty dropdown is exactly what ModelField's free-text escape hatch
    # exists to survive, so a transient provider failure must not produce one.
    redis = _FakeRedis()
    key = f"llm:models:{_USER_ID}:azure_foundry"
    redis.store[key] = cache._dump([_model()], "api", None, now=cache.time.time() - cache._FRESH_SECONDS - 1)
    _install(monkeypatch, redis)
    calls: list[str] = []
    _stub_discovery(monkeypatch, ([], "error", "connection refused"), calls)

    models, source, note = await cache.discover_models_cached(user_id=_USER_ID, provider="azure_foundry", setting=None)

    assert calls == ["azure_foundry"]  # staleness means we DO retry
    assert [m.id for m in models] == ["claude-opus-4-7"]
    assert source == "api"
    assert note == cache.STALE_NOTE


async def test_stale_entry_is_replaced_when_rediscovery_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    key = f"llm:models:{_USER_ID}:azure_foundry"
    redis.store[key] = cache._dump([_model("old-deployment")], "api", None, now=cache.time.time() - 10**6)
    _install(monkeypatch, redis)
    _stub_discovery(monkeypatch, ([_model("new-deployment")], "api", None), [])

    models, source, note = await cache.discover_models_cached(user_id=_USER_ID, provider="azure_foundry", setting=None)

    assert [m.id for m in models] == ["new-deployment"]
    assert note is None
    assert "new-deployment" in redis.store[key]


async def test_unreadable_payload_falls_back_to_rediscovery(monkeypatch: pytest.MonkeyPatch) -> None:
    # Leftovers from an older shape of this module must not 500 the endpoint.
    redis = _FakeRedis()
    key = f"llm:models:{_USER_ID}:azure_foundry"
    redis.store[key] = json.dumps({"unexpected": "shape"})
    _install(monkeypatch, redis)
    _stub_discovery(monkeypatch, ([_model()], "api", None), [])

    models, source, _ = await cache.discover_models_cached(user_id=_USER_ID, provider="azure_foundry", setting=None)

    assert source == "api"
    assert [m.id for m in models] == ["claude-opus-4-7"]


async def test_a_broken_cache_degrades_to_calling_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    # A cache that's down must mean "slower," never "broken."
    _install(monkeypatch, _FakeRedis(fail=True))
    calls: list[str] = []
    _stub_discovery(monkeypatch, ([_model()], "api", None), calls)

    models, source, note = await cache.discover_models_cached(user_id=_USER_ID, provider="azure_foundry", setting=None)

    assert calls == ["azure_foundry"]
    assert source == "api"
    assert [m.id for m in models] == ["claude-opus-4-7"]
    assert note is None


async def test_cache_is_scoped_per_user(monkeypatch: pytest.MonkeyPatch) -> None:
    # A discovered list is a property of the credential, not the provider --
    # one user's list must never be served to another.
    other_user = uuid.UUID("22222222-2222-2222-2222-222222222222")
    redis = _FakeRedis()
    mine = cache._dump([_model("mine")], "api", None, now=cache.time.time())
    redis.store[f"llm:models:{_USER_ID}:azure_foundry"] = mine
    _install(monkeypatch, redis)
    _stub_discovery(monkeypatch, ([_model("theirs")], "api", None), [])

    models, _, _ = await cache.discover_models_cached(user_id=other_user, provider="azure_foundry", setting=None)

    assert [m.id for m in models] == ["theirs"]


async def test_invalidate_drops_only_that_users_provider_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    mine = f"llm:models:{_USER_ID}:azure_foundry"
    other_provider = f"llm:models:{_USER_ID}:openai"
    redis.store[mine] = "x"
    redis.store[other_provider] = "y"
    _install(monkeypatch, redis)

    await cache.invalidate_models_cache(user_id=_USER_ID, provider="azure_foundry")

    assert mine not in redis.store
    assert other_provider in redis.store


async def test_invalidate_survives_a_broken_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeRedis(fail=True))
    await cache.invalidate_models_cache(user_id=_USER_ID, provider="azure_foundry")  # must not raise
