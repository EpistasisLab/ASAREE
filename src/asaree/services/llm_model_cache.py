"""User/credential-scoped metadata caching; Motoro owns provider operations.

Live listings are fresh for six hours and retained for 24 hours. Transient
discovery failures can reuse that retained listing, with its error attached.
Every refresh path shares a user/provider budget and a 30-second result cache.
Redis coordinates workers; bounded local state keeps outages from causing a
request stampede within a process. No credential plaintext is used in keys.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import OrderedDict

from motoro.services.provider_types import ModelInfo, ModelListing, ProviderError
from pydantic import BaseModel

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.redis_client import get_redis
from asaree.services.llm_model_discovery import discover_models
from asaree.services.rate_limit import consume_rate_limit

logger = logging.getLogger(__name__)

_FRESH_SECONDS = 6 * 60 * 60
_RETAIN_SECONDS = 24 * 60 * 60
_RETRY_SECONDS = 30
# Anthropic can fetch ten pages, each with a ten-second request timeout.
_REFRESH_SECONDS = 110
_LOCK_SECONDS = 120
_MAX_RECENT = 128
_inflight: dict[str, asyncio.Task[ModelListing]] = {}
_recent: OrderedDict[str, tuple[float, ModelListing]] = OrderedDict()
_RELEASE_LOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

STALE_NOTE = (
    "Couldn't reach the provider just now, so this list is the last one that loaded successfully and may be "
    "out of date. If a model you expect is missing, you can still type its name directly in the Model field."
)


class ModelDiscoveryRateLimitError(RuntimeError):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Too many model discovery attempts. Please wait {retry_after} seconds.")


class _CachedListing(BaseModel):
    listing: ModelListing
    fresh_until: float


def _key(user_id: uuid.UUID, provider: str, setting: UserLLMSetting | None = None) -> str:
    # Encryption produces a new ciphertext on every save. Including endpoints
    # also handles legacy/out-of-band endpoint edits. Old requests can finish,
    # but their entries can never be read under a different credential revision.
    revision = "public"
    if setting is not None:
        fields = [str(setting.id), setting.api_key_encrypted, setting.api_base, setting.azure_project_endpoint]
        revision = hashlib.sha256(json.dumps(fields).encode()).hexdigest()
    return f"llm:models:{user_id}:{provider}:v5:{revision}"


def _dump(listing: ModelListing, *, now: float) -> str:
    return _CachedListing(listing=listing, fresh_until=now + _FRESH_SECONDS).model_dump_json()


def _load(raw: str) -> _CachedListing:
    return _CachedListing.model_validate_json(raw)


async def _read(key: str) -> _CachedListing | None:
    try:
        raw = await get_redis().get(key)
        return _load(raw) if raw is not None else None
    except Exception:  # noqa: BLE001 -- Redis/payload failures are cache misses
        logger.warning("model_cache_read_failed")
        return None


async def _write(key: str, value: str, ttl: int) -> None:
    try:
        await get_redis().set(key, value, ex=ttl)
    except Exception:  # noqa: BLE001 -- caching must not fail provider operations
        logger.warning("model_cache_write_failed")


def _remember(key: str, listing: ModelListing) -> None:
    _recent[key] = (time.monotonic() + _RETRY_SECONDS, listing)
    _recent.move_to_end(key)
    while len(_recent) > _MAX_RECENT:
        _recent.popitem(last=False)


def _local_result(key: str) -> ModelListing | None:
    entry = _recent.get(key)
    if entry is None:
        return None
    if entry[0] > time.monotonic():
        return entry[1]
    _recent.pop(key, None)
    return None


def _transient(error: ProviderError | None) -> bool:
    return error is not None and (
        error.kind in {"timeout", "network", "rate_limited", "invalid_response"}
        or (error.kind == "http" and (error.http_status or 0) >= 500)
    )


def _fallback(listing: ModelListing, cached: _CachedListing | None) -> ModelListing:
    if cached is not None and _transient(listing.error):
        return cached.listing.model_copy(update={"error": listing.error, "note": STALE_NOTE})
    return listing


async def _refresh(
    *, key: str, user_id: uuid.UUID, provider: str, setting: UserLLMSetting | None, cached: _CachedListing | None
) -> ModelListing:
    lock_key, token = f"{key}:refresh", uuid.uuid4().hex
    locked = False
    deadline = time.monotonic() + _LOCK_SECONDS
    while True:
        recent = await _read(f"{key}:recent")
        if recent is not None:
            _remember(key, recent.listing)
            return recent.listing
        # Recheck after joining another process's refresh.
        current = await _read(key)
        if current is not None:
            cached = current
            if time.time() < current.fresh_until:
                return current.listing
        try:
            locked = bool(await get_redis().set(lock_key, token, nx=True, ex=_LOCK_SECONDS))
        except Exception:  # noqa: BLE001 -- local single-flight still applies
            logger.warning("model_cache_lock_unavailable")
            break
        if locked:
            break
        if time.monotonic() >= deadline:
            raise ModelDiscoveryRateLimitError(1)
        await asyncio.sleep(0.1)

    try:
        # The previous owner can finish between our last read and acquiring
        # the lease. Check again while holding it before spending any budget.
        if locked:
            recent = await _read(f"{key}:recent")
            current = await _read(key)
            if current is not None and time.time() < current.fresh_until:
                return current.listing
            if recent is not None:
                _remember(key, recent.listing)
                return recent.listing
        # The budget is independent of credential identity. It covers custom
        # capability and execution lookups as well as the model-list route.
        allowed, retry_after = await consume_rate_limit(
            f"model-refresh:{user_id}:{provider}", limit=10, window_seconds=60
        )
        if not allowed:
            if cached is None:
                raise ModelDiscoveryRateLimitError(retry_after)
            listing = ModelListing(source="error", error=ProviderError(kind="rate_limited"))
        else:
            try:
                async with asyncio.timeout(_REFRESH_SECONDS):
                    listing = await discover_models(provider=provider, setting=setting)
            except TimeoutError:
                listing = ModelListing(
                    source="error", note="Model discovery timed out.", error=ProviderError(kind="timeout")
                )
        if listing.source == "api" and listing.error is None:
            await _write(key, _dump(listing, now=time.time()), _RETAIN_SECONDS)
        result = _fallback(listing, cached)
        # Do not extend the retained live listing on failure. The separate
        # short-lived result includes errors/static fallback and enforces a
        # retry cooldown across workers, including when no live list exists.
        await _write(f"{key}:recent", _dump(result, now=time.time()), _RETRY_SECONDS)
        _remember(key, result)
        return result
    finally:
        if locked:
            try:
                # The low-level async command has no annotations in redis-py.
                await get_redis().execute_command("EVAL", _RELEASE_LOCK, 1, lock_key, token)  # type: ignore[no-untyped-call]
            except Exception:  # noqa: BLE001 -- the lease expires if release fails
                logger.warning("model_cache_lock_release_failed")


async def discover_models_cached(*, user_id: uuid.UUID, provider: str, setting: UserLLMSetting | None) -> ModelListing:
    key = _key(user_id, provider, setting)
    cached = await _read(key)
    if cached is not None and time.time() < cached.fresh_until:
        return cached.listing
    recent = _local_result(key)
    if recent is not None:
        return recent
    task = _inflight.get(key)
    if task is None:
        task = asyncio.create_task(
            _refresh(key=key, user_id=user_id, provider=provider, setting=setting, cached=cached)
        )
        _inflight[key] = task

        def finished(done: asyncio.Task[ModelListing]) -> None:
            _inflight.pop(key, None)
            # A cancelled caller does not cancel shared work; retrieve any
            # exception even when no remaining caller awaits its result.
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
    return await asyncio.shield(task)


async def store_models_cache(
    *, user_id: uuid.UUID, provider: str, setting: UserLLMSetting, models: list[ModelInfo]
) -> None:
    """Reuse a successful connection check under the exact checked credential."""
    key = _key(user_id, provider, setting)
    listing = ModelListing(models=models, source="api")
    await _write(key, _dump(listing, now=time.time()), _RETAIN_SECONDS)
    await _write(f"{key}:recent", _dump(listing, now=time.time()), _RETRY_SECONDS)
    _remember(key, listing)
