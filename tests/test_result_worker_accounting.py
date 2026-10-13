import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from asaree.services import protocol_execution, result_usage
from asaree.worker import tasks


@pytest.mark.parametrize("failure", [False, True])
async def test_terminal_worker_paths_capture_usage_independently_of_declared_metrics(monkeypatch, failure):
    @asynccontextmanager
    async def session():
        yield object()

    monkeypatch.setattr(tasks, "get_session", session)
    monkeypatch.setattr(tasks, "get_protocol_run", AsyncMock(return_value=SimpleNamespace(status="pending")))
    monkeypatch.setattr(tasks, "get_settings", lambda: SimpleNamespace(worker_job_timeout_seconds=1))
    fail = AsyncMock()
    capture = AsyncMock()
    monkeypatch.setattr(tasks, "fail_protocol_run", fail)
    monkeypatch.setattr(result_usage, "capture_result_usage", capture)
    work = AsyncMock(side_effect=ValueError("executor failed") if failure else None)
    identifier = uuid4()
    await tasks._guarded_protocol_run(str(identifier), work)
    capture.assert_awaited_once_with({}, only_run_id=identifier)
    assert fail.await_count == int(failure)


async def test_duplicate_whole_dataset_delivery_cannot_execute_twice(monkeypatch):
    identifier = uuid4()
    run = SimpleNamespace(
        id=identifier,
        status="pending",
        row_result_id=None,
        replicate_result_id=None,
        attempt_result={},
        dataset_row=None,
        cancel_requested_at=None,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: run)))

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(protocol_execution, "get_session", session)

    async def status(_db, _identifier, *, status):
        run.status = status

    set_status = AsyncMock(side_effect=status)
    monkeypatch.setattr(protocol_execution, "set_status", set_status)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hydrate():
        entered.set()
        await release.wait()

    hydration = AsyncMock(side_effect=hydrate)
    monkeypatch.setattr(protocol_execution, "hydrate_registry", hydration)
    monkeypatch.setattr(protocol_execution, "get_protocol_run", AsyncMock(return_value=None))
    first = asyncio.create_task(protocol_execution.run_protocol(identifier))
    await entered.wait()
    await protocol_execution.run_protocol(identifier)
    release.set()
    await first
    set_status.assert_awaited_once_with(db, identifier, status="running")
    hydration.assert_awaited_once()
