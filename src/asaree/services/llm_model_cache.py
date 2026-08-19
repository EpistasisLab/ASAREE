"""Server-side cache for discovered provider model lists.

Speed, not provenance: nothing here is a record of what was true, it's a way
to avoid asking a provider the same question repeatedly. That's why it lives
in Redis (alongside ``ratelimit:`` and ``token:deny:``, see redis_client.py)
rather than in a table -- there's nothing to migrate, nothing to back up, and
an empty cache is a fully valid state.

Cached per (user, provider), never per provider alone. A discovered list is a
property of the *credential*, not the provider: OpenAI returns what that key's
org can reach, and an Azure Foundry project returns only its own deployments.
A provider-wide cache would happily offer user A a model only user B can call,
which fails at inference time -- after cells have been generated.

Two clocks, deliberately:

* ``fresh_until`` (6h) is when we start asking the provider again.
* the Redis TTL (24h) is when the entry actually disappears.

The gap between them is the stale-fallback window. A provider blip after
``fresh_until`` returns the last known-good list with a note attached instead
of collapsing the dropdown to empty -- an empty list is exactly the state the
free-text ModelField escape hatch exists to survive, so falling back to it on
a transient 503 would be a real regression in the picker.

Only live results (``source == "api"``) are stored. The static catalog costs
no network call, so caching it would buy nothing and add a second place for
it to go stale. Errors are never cached -- they should be retried, and the
endpoint's own 10-per-60s limiter is what bounds retry cost.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from motoro.services.model_capabilities import ModelCapabilities

from asaree.models.user_llm_setting import UserLLMSetting
from asaree.redis_client import get_redis
from asaree.services.llm_model_discovery import ModelInfo, discover_models

logger = logging.getLogger(__name__)

_FRESH_SECONDS = 6 * 60 * 60
_RETAIN_SECONDS = 24 * 60 * 60

STALE_NOTE = (
    "Couldn't reach the provider just now, so this list is the last one that loaded successfully and may be "
    "out of date. If a model you expect is missing, you can still type its name directly in the Model field."
)


def _key(user_id: uuid.UUID, provider: str) -> str:
    return f"llm:models:{user_id}:{provider}"


def _dump(models: list[ModelInfo], source: str, note: str | None, *, now: float) -> str:
    return json.dumps(
        {
            "fresh_until": now + _FRESH_SECONDS,
            "source": source,
            "note": note,
            "models": [{"id": m.id, "label": m.label, "capabilities": m.capabilities.model_dump()} for m in models],
        }
    )


def _load(raw: str) -> tuple[list[ModelInfo], str, str | None, float]:
    payload = json.loads(raw)
    models = [
        ModelInfo(
            id=entry["id"],
            label=entry["label"],
            capabilities=ModelCapabilities.model_validate(entry["capabilities"]),
        )
        for entry in payload["models"]
    ]
    return models, payload["source"], payload["note"], float(payload["fresh_until"])


async def discover_models_cached(
    *, user_id: uuid.UUID, provider: str, setting: UserLLMSetting | None
) -> tuple[list[ModelInfo], str, str | None]:
    """``discover_models`` with the Redis layer described above in front of it.

    Every Redis interaction is best-effort: a cache that's down must degrade
    to "call the provider every time," never to an error page.
    """
    redis = get_redis()
    key = _key(user_id, provider)
    now = time.time()

    cached_raw: str | None = None
    try:
        cached_raw = await redis.get(key)
    except Exception as e:  # noqa: BLE001 -- any redis failure is a cache miss, not an outage
        logger.warning("model_cache_read_failed", extra={"error": str(e)})

    if cached_raw is not None:
        try:
            models, source, note, fresh_until = _load(cached_raw)
            if now < fresh_until:
                return models, source, note
        except (ValueError, KeyError, TypeError) as e:
            # A payload written by an older shape of this module. Drop it and
            # rediscover rather than 500 on someone else's leftovers.
            logger.warning("model_cache_payload_unreadable", extra={"error": str(e)})
            cached_raw = None

    models, source, note = await discover_models(provider=provider, setting=setting)

    if source == "error" and cached_raw is not None:
        try:
            stale_models, stale_source, _stale_note, _ = _load(cached_raw)
        except (ValueError, KeyError, TypeError):
            pass
        else:
            logger.info("model_cache_serving_stale", extra={"provider": provider, "discovery_note": note})
            return stale_models, stale_source, STALE_NOTE

    if source == "api":
        try:
            await redis.set(key, _dump(models, source, note, now=now), ex=_RETAIN_SECONDS)
        except Exception as e:  # noqa: BLE001 -- failing to cache is not failing to answer
            logger.warning("model_cache_write_failed", extra={"error": str(e)})

    return models, source, note


async def invalidate_models_cache(*, user_id: uuid.UUID, provider: str) -> None:
    """Drop a cached list after its credential changed.

    The important one: "I just saved my key and the models are still wrong"
    is the failure people actually hit, and a 6h freshness window would make
    it look broken for the rest of the working day. The frontend's own
    ``invalidateQueries({queryKey: ['llm-settings']})`` covers the browser
    cache by key prefix (see useProviderModels.ts); this covers ours.
    """
    try:
        await get_redis().delete(_key(user_id, provider))
    except Exception as e:  # noqa: BLE001
        logger.warning("model_cache_invalidate_failed", extra={"error": str(e)})
