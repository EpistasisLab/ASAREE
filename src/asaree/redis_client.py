"""ASAREE's own Redis connection — separate from Motoro's internal use
of the same URL for working memory. Different key prefixes (``ratelimit:``,
``token:deny:``, see services/auth_service.py and api/auth.py) keep the two
uses from ever colliding on the same instance.
"""

from __future__ import annotations

import redis.asyncio as redis

from asaree.config import get_settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Get or create the async Redis client (lazy singleton)."""
    global _client
    if _client is None:
        _client = redis.from_url(get_settings().redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
    return _client


async def dispose_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
