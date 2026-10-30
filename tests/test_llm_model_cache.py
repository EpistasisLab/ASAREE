"""Credential identity, refresh coordination, and metadata fallback without infrastructure."""

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from motoro.services.provider_types import ModelInfo, ModelListing, ProviderError

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services import llm_model_cache as cache

OWNER = uuid.UUID("11111111-1111-1111-1111-111111111111")


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expires = {}
        self.fail = False

    async def get(self, key):
        if self.fail:
            raise ConnectionError("unavailable")
        if self.expires.get(key, float("inf")) <= cache.time.time():
            self.values.pop(key, None)
        return self.values.get(key)

    async def set(self, key, value, *, ex, nx=False):
        if nx and await self.get(key) is not None:
            return None
        if self.fail:
            raise ConnectionError("unavailable")
        self.values[key] = value
        self.expires[key] = cache.time.time() + ex
        return True

    async def execute_command(self, command, script, count, key, token):
        assert command == "EVAL"
        if await self.get(key) == token:
            self.values.pop(key, None)
            return 1
        return 0


@pytest.fixture
def redis(monkeypatch):
    instance = FakeRedis()
    cache._recent.clear()
    monkeypatch.setattr(cache, "get_redis", lambda: instance)
    monkeypatch.setattr(cache, "consume_rate_limit", AsyncMock(return_value=(True, 0)))
    yield instance
    cache._recent.clear()


def listing(model="new-model", *, source="api", error=None):
    return ModelListing(models=[ModelInfo(id=model)], source=source, error=error)


def credential(revision="old"):
    return UserLLMSetting(
        id=uuid.UUID(int=1),
        provider="azure_foundry",
        api_key_encrypted=f"encrypted-{revision}",
        api_base="https://example.test",
        azure_project_endpoint="https://example.test/api/projects/example",
    )


async def lookup(setting=None, owner=OWNER, provider="azure_foundry"):
    return await cache.discover_models_cached(user_id=owner, provider=provider, setting=setting)


async def seed(redis, data, *, setting=None, age=0):
    key = cache._key(OWNER, "azure_foundry", setting)
    await redis.set(key, cache._dump(data, now=cache.time.time() - age), ex=cache._RETAIN_SECONDS - age)
    return key


async def test_fresh_cache_avoids_discovery_and_budget(redis, monkeypatch):
    expected = listing()
    await seed(redis, expected)
    discover = AsyncMock(side_effect=AssertionError("must use cache"))
    monkeypatch.setattr(cache, "discover_models", discover)
    assert await lookup() == expected
    discover.assert_not_awaited()
    cache.consume_rate_limit.assert_not_awaited()


async def test_live_results_roundtrip_complete_metadata(redis, monkeypatch):
    from motoro.services.provider_types import DefaultValue

    expected = listing()
    expected.models[0].defaults.temperature = DefaultValue(value=0, source="provider")
    expected.models[0].capability_sources = {"supports_temperature": "unknown"}
    monkeypatch.setattr(cache, "discover_models", AsyncMock(return_value=expected))
    assert await lookup() == expected
    cache._recent.clear()
    assert await lookup() == expected
    cache.discover_models.assert_awaited_once()


@pytest.mark.parametrize("change", ["api_key_encrypted", "api_base", "azure_project_endpoint", "id"])
async def test_credential_changes_get_a_new_namespace(redis, monkeypatch, change):
    old = credential()
    new = credential()
    setattr(new, change, uuid.uuid4() if change == "id" else "changed")
    await seed(redis, listing("old-project"), setting=old)
    monkeypatch.setattr(cache, "discover_models", AsyncMock(return_value=listing("new-project")))
    assert (await lookup(new)).models[0].id == "new-project"
    assert (await lookup(old)).models[0].id == "old-project"


async def test_inflight_old_credential_cannot_repopulate_current_cache(redis, monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()
    old, new = credential(), credential("new")

    async def discover(*, provider, setting):
        if setting is old:
            started.set()
            await finish.wait()
            return listing("old-project")
        return listing("new-project")

    monkeypatch.setattr(cache, "discover_models", discover)
    pending = asyncio.create_task(lookup(old))
    await started.wait()
    assert (await lookup(new)).models[0].id == "new-project"
    finish.set()
    await pending
    assert (await lookup(new)).models[0].id == "new-project"


async def test_deleted_credential_cannot_read_saved_credential_cache(redis, monkeypatch):
    await seed(redis, listing("private-deployment"), setting=credential())
    expected = listing("public-model", source="static")
    monkeypatch.setattr(cache, "discover_models", AsyncMock(return_value=expected))
    assert await lookup(None) == expected


@pytest.mark.parametrize("source", ["static", "error"])
@pytest.mark.parametrize("error", [ProviderError(kind="timeout"), ProviderError(kind="http", http_status=503)])
async def test_transient_failures_reuse_stale_live_metadata(redis, monkeypatch, source, error):
    key = await seed(redis, listing("live-only-model"), age=cache._FRESH_SECONDS + 1)
    original = redis.values[key]
    failed = listing("catalog-model", source=source, error=error)
    monkeypatch.setattr(cache, "discover_models", AsyncMock(return_value=failed))
    result = await lookup()
    assert result.models[0].id == "live-only-model"
    assert result.note == cache.STALE_NOTE
    assert result.error == error
    assert redis.values[key] == original
    assert await lookup() == result
    cache.discover_models.assert_awaited_once()


async def test_rejected_credentials_do_not_serve_stale_as_authoritative(redis, monkeypatch):
    await seed(redis, listing("old-model"), age=cache._FRESH_SECONDS + 1)
    failed = ModelListing(source="error", error=ProviderError(kind="credential_rejected", http_status=401))
    monkeypatch.setattr(cache, "discover_models", AsyncMock(return_value=failed))
    assert await lookup() == failed


async def test_retained_listing_expires_without_failure_extending_it(redis, monkeypatch):
    now = cache.time.time()
    await seed(redis, listing("old-model"), age=cache._RETAIN_SECONDS - 1)
    failed = ModelListing(source="error", error=ProviderError(kind="network"))
    monkeypatch.setattr(cache, "discover_models", AsyncMock(return_value=failed))
    assert (await lookup()).models
    monkeypatch.setattr(cache.time, "time", lambda: now + cache._RETRY_SECONDS + 1)
    cache._recent.clear()
    assert await lookup() == failed


@pytest.mark.parametrize("redis_down", [False, True])
async def test_parallel_misses_share_one_refresh_even_without_redis(redis, monkeypatch, redis_down):
    redis.fail = redis_down
    started, finish = asyncio.Event(), asyncio.Event()

    async def discover(**kwargs):
        started.set()
        await finish.wait()
        return listing()

    discover = AsyncMock(side_effect=discover)
    monkeypatch.setattr(cache, "discover_models", discover)
    pending = [asyncio.create_task(lookup()) for _ in range(8)]
    await started.wait()
    finish.set()
    results = await asyncio.gather(*pending)
    assert all(result == listing() for result in results)
    discover.assert_awaited_once()


async def test_cancelled_waiter_does_not_cancel_other_waiters(redis, monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()

    async def discover(**kwargs):
        started.set()
        await finish.wait()
        return listing()

    monkeypatch.setattr(cache, "discover_models", AsyncMock(side_effect=discover))
    cancelled = asyncio.create_task(lookup())
    other = asyncio.create_task(lookup())
    await started.wait()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    finish.set()
    assert await other == listing()
    cache.discover_models.assert_awaited_once()


async def test_workers_share_refresh_lease_and_failure_cooldown(redis, monkeypatch):
    async def discover(**kwargs):
        await asyncio.sleep(0.01)
        return ModelListing(source="error", error=ProviderError(kind="network"))

    discover = AsyncMock(side_effect=discover)
    monkeypatch.setattr(cache, "discover_models", discover)
    key = cache._key(OWNER, "azure_foundry")
    # Bypass process-local task sharing to exercise independent worker paths.
    results = await asyncio.gather(
        *(cache._refresh(key=key, user_id=OWNER, provider="azure_foundry", setting=None, cached=None) for _ in range(4))
    )
    assert all(result.error.kind == "network" for result in results)
    cache._recent.clear()
    assert (await lookup()).error.kind == "network"
    discover.assert_awaited_once()


async def test_failure_cooldown_expires_and_retries(redis, monkeypatch):
    now = cache.time.time()
    failed = ModelListing(source="error", error=ProviderError(kind="network"))
    discover = AsyncMock(side_effect=[failed, listing()])
    monkeypatch.setattr(cache, "discover_models", discover)
    assert await lookup() == failed
    assert await lookup() == failed
    monkeypatch.setattr(cache.time, "time", lambda: now + cache._RETRY_SECONDS + 1)
    cache._recent.clear()
    assert await lookup() == listing()
    assert discover.await_count == 2


async def test_shared_budget_blocks_refresh_and_is_independent_of_credential(redis, monkeypatch):
    monkeypatch.setattr(cache, "consume_rate_limit", AsyncMock(return_value=(False, 23)))
    discover = AsyncMock(side_effect=AssertionError("budget exhausted"))
    monkeypatch.setattr(cache, "discover_models", discover)
    for saved in (None, credential(), credential("new")):
        with pytest.raises(cache.ModelDiscoveryRateLimitError) as caught:
            await lookup(saved)
        assert caught.value.retry_after == 23
    assert all(
        call.args == (f"model-refresh:{OWNER}:azure_foundry",) for call in cache.consume_rate_limit.call_args_list
    )
    discover.assert_not_awaited()


async def test_corrupt_or_legacy_cache_is_rediscovered(redis, monkeypatch):
    redis.values[cache._key(OWNER, "azure_foundry")] = "invalid JSON"
    redis.values[f"llm:models:{OWNER}:azure_foundry:v4"] = "old payload"
    monkeypatch.setattr(cache, "discover_models", AsyncMock(return_value=listing()))
    assert await lookup() == listing()


async def test_catalog_enriched_cache_layers_are_not_reused(redis, monkeypatch):
    import time

    previous = cache._key(OWNER, "azure_foundry").replace(":v6:", ":v5:")
    old_listing = listing("catalog-enriched")
    encoded = cache._dump(old_listing, now=time.time())
    redis.values[previous] = encoded
    redis.values[f"{previous}:recent"] = encoded
    cache._recent[previous] = (time.monotonic() + 30, old_listing)
    fresh = listing("provider-facts")
    discover = AsyncMock(return_value=fresh)
    monkeypatch.setattr(cache, "discover_models", discover)
    assert await lookup() == fresh
    discover.assert_awaited_once()


async def test_discovery_audit_retains_observation_time_without_credentials(redis):
    saved = credential()
    await seed(redis, listing(), setting=saved, age=60)
    context = await cache.discovery_context(user_id=OWNER, provider="azure_foundry", setting=saved)
    assert context["api_base"] == saved.api_base
    assert context["discovered_at"] < cache.time.time() - 59
    assert context["credential_revision"] != saved.api_key_encrypted
    assert saved.api_key_encrypted not in str(context)
    other = await cache.discovery_context(user_id=OWNER, provider="azure_foundry", setting=credential("changed"))
    assert other["discovered_at"] is None
    assert other["credential_revision"] != context["credential_revision"]


async def test_user_and_provider_scoping(redis, monkeypatch):
    await seed(redis, listing("private"))
    monkeypatch.setattr(cache, "discover_models", AsyncMock(return_value=listing("other")))
    assert (await lookup(owner=uuid.uuid4())).models[0].id == "other"
    assert (await lookup(provider="local")).models[0].id == "other"


async def test_successful_check_seeds_only_its_credential(redis):
    old, new = credential(), credential("new")
    await cache.store_models_cache(user_id=OWNER, provider="azure_foundry", setting=new, models=listing("new").models)
    await cache.store_models_cache(user_id=OWNER, provider="azure_foundry", setting=old, models=listing("old").models)
    assert (await lookup(new)).models[0].id == "new"
    assert (await lookup(old)).models[0].id == "old"
    assert "encrypted-new" not in " ".join(redis.values)
