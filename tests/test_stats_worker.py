"""Interrupted statistics are explicitly incomplete, including saved snapshots."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from asaree.worker import stats_task


@pytest.mark.parametrize("termination", ["completed", "timeout", "signal"])
async def test_worker_preserves_incomplete_execution_status(monkeypatch, termination):
    analysis = SimpleNamespace(id=uuid4(), status="pending", request={}, output={}, error=None)
    db = SimpleNamespace(get=AsyncMock(return_value=analysis))

    @asynccontextmanager
    async def session():
        yield db

    process = SimpleNamespace(
        returncode=0, communicate=AsyncMock(return_value=(json.dumps({"execution_status": "incomplete"}).encode(), b""))
    )
    if termination == "timeout":
        process.communicate.side_effect = TimeoutError()
    elif termination == "signal":
        process.returncode = -9
    monkeypatch.setattr(stats_task, "get_session", session)
    monkeypatch.setattr(stats_task.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    await stats_task.execute_stats_task({}, str(analysis.id))
    assert analysis.status == "incomplete"
    if termination == "timeout":
        assert "30 minutes" in analysis.error
    elif termination == "signal":
        assert "signal 9" in analysis.error
    else:
        assert analysis.output["execution_status"] == "incomplete"


async def test_stale_interrupted_analysis_is_marked_incomplete(monkeypatch):
    db = SimpleNamespace(execute=AsyncMock())

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(stats_task, "get_session", session)
    await stats_task.check_stale_stats({})
    statement = db.execute.call_args.args[0]
    assert statement.compile().params["status"] == "incomplete"
