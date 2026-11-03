"""Refresh budgets remain atomic when Redis is unavailable."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from asaree.services import rate_limit


async def test_concurrent_refreshes_cannot_exceed_fallback_budget(monkeypatch):
    async def unavailable(*args):
        await asyncio.sleep(0)
        raise ConnectionError("unavailable")

    monkeypatch.setattr(rate_limit, "get_redis", lambda: SimpleNamespace(execute_command=unavailable))
    key = f"model-refresh:{uuid.uuid4()}:anthropic"
    outcomes = await asyncio.gather(
        *(rate_limit.consume_rate_limit(key, limit=10, window_seconds=60) for _ in range(30))
    )
    assert sum(allowed for allowed, _ in outcomes) == 10
    assert all(retry > 0 for allowed, retry in outcomes if not allowed)


@pytest.mark.parametrize("retry_after", [0, 23])
async def test_shared_redis_budget_is_honored(monkeypatch, retry_after):
    evaluate = AsyncMock(return_value=retry_after)
    monkeypatch.setattr(rate_limit, "get_redis", lambda: SimpleNamespace(execute_command=evaluate))
    key = f"model-refresh:{uuid.uuid4()}:local"
    assert await rate_limit.consume_rate_limit(key, limit=10, window_seconds=60) == (retry_after == 0, retry_after)
    assert evaluate.call_args.args[0] == "EVAL"
    args = evaluate.call_args.args[1:]
    assert args[1:3] == (1, f"ratelimit:{key}")
    assert args[4:6] == (60, 10)
