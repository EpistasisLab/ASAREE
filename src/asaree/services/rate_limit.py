"""Sliding-window rate limiting for login/registration — Redis-backed with an
in-memory fallback, fail-open (Redis unavailable never blocks a request; the
in-memory check still applies, it's just per-process rather than shared).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

from asaree.redis_client import get_redis

logger = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "ratelimit:"

# In-memory fallback state, keyed the same way the Redis check is. Capped so
# a sustained credential-stuffing run against many distinct keys can't grow
# this dict without bound.
_attempts: dict[str, list[float]] = defaultdict(list)
_MAX_KEYS = 10_000


def _prune(window_seconds: int) -> None:
    cutoff = datetime.now(UTC).timestamp() - window_seconds
    stale = [k for k, ts in _attempts.items() if not any(t > cutoff for t in ts)]
    for k in stale:
        del _attempts[k]
    while len(_attempts) > _MAX_KEYS:
        del _attempts[next(iter(_attempts))]


async def check_rate_limit(key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds). Does NOT record an attempt —
    call :func:`record_attempt` separately so a caller can choose to record
    only failures (e.g. login) rather than every call (e.g. registration)."""
    now_ts = datetime.now(UTC).timestamp()
    rk = f"{_REDIS_KEY_PREFIX}{key}"
    try:
        redis = get_redis()
        cutoff = now_ts - window_seconds
        pipe = redis.pipeline()
        pipe.zremrangebyscore(rk, "-inf", cutoff)
        pipe.zcard(rk)
        pipe.expire(rk, window_seconds)
        _, count, _ = await pipe.execute()
        if count >= limit:
            oldest = await redis.zrange(rk, 0, 0, withscores=True)
            retry_after = 0
            if oldest:
                retry_after = max(0, int(float(oldest[0][1]) + window_seconds - now_ts) + 1)
            return False, retry_after
        return True, 0
    except Exception:
        logger.warning("rate_limit_redis_unavailable", exc_info=True)

    if len(_attempts) > _MAX_KEYS // 2:
        _prune(window_seconds)
    recent = [t for t in _attempts[key] if t > now_ts - window_seconds]
    _attempts[key] = recent
    if len(recent) >= limit:
        retry_after = int(recent[0] + window_seconds - now_ts) + 1
        return False, retry_after
    return True, 0


async def record_attempt(key: str, *, window_seconds: int) -> None:
    """Record one attempt against *key*, in both Redis and the in-memory
    fallback — call after a failed login/registration attempt."""
    now_ts = datetime.now(UTC).timestamp()
    rk = f"{_REDIS_KEY_PREFIX}{key}"
    try:
        redis = get_redis()
        pipe = redis.pipeline()
        pipe.zadd(rk, {f"{now_ts}": now_ts})
        pipe.expire(rk, window_seconds)
        await pipe.execute()
    except Exception:
        logger.warning("rate_limit_redis_unavailable", exc_info=True)
    _attempts[key].append(now_ts)


async def clear_rate_limit(key: str) -> None:
    """Clear a key's rate-limit state — call on a successful login so a
    legitimate user isn't stuck waiting out the window after mistyping a
    password a few times."""
    try:
        await get_redis().delete(f"{_REDIS_KEY_PREFIX}{key}")
    except Exception:
        logger.warning("rate_limit_redis_unavailable", exc_info=True)
    _attempts.pop(key, None)
