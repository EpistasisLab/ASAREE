"""Command policy and download capabilities, without runtime infrastructure."""

import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from asaree.api.result_jobs import Command
from asaree.services import result_exports
from asaree.services.protocol_execution import ProtocolValidationError
from asaree.services.result_jobs import eligible


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ProtocolValidationError("Publish the generated draft design before running this experiment."),
            "Publish the generated draft design before running this experiment.",
        ),
        (ProtocolValidationError("x" * 600), "x" * 512),
        (ValueError("Publication changed."), "Publication changed."),
        (RuntimeError("private internal details"), "Command failed; retry preparation."),
    ],
)
async def test_preparation_failure_preserves_validation_messages(monkeypatch, caplog, error, expected):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from asaree.services import result_jobs

    job = SimpleNamespace(id=uuid.uuid4(), kind="plan", status="pending")
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=True),
        get=AsyncMock(return_value=job),
        execute=AsyncMock(),
    )

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(result_jobs, "get_session", session)
    monkeypatch.setattr(result_jobs, "job_scope", AsyncMock(return_value=SimpleNamespace(row_plan=None)))
    prepare = AsyncMock(side_effect=error)
    monkeypatch.setattr(result_jobs, "prepare_step", prepare)

    assert await result_jobs._advance_command(job.id) is False

    prepare.assert_awaited_once()
    db.execute.assert_awaited_once()
    values = db.execute.call_args.args[0].compile().params
    assert values["status"] == "failed"
    assert values["error"] == expected
    record = next(record for record in caplog.records if record.message == "result_command_failed")
    assert record.exc_info[1] is error


def test_pending_and_failed_work_never_implicitly_reruns_finished_or_active_slots():
    for status in ("not_started", "failed", "cancelled"):
        assert eligible(status, "slot", set())
    # An iteration-limited attempt finished with a (truncated) result: like a
    # completed one, it only runs again when the user selects it.
    for status in ("pending", "running", "finalizing", "completed", "limit_reached"):
        assert not eligible(status, "slot", set())
    for status in ("completed", "limit_reached"):
        assert eligible(status, "slot", {"slot"})
        assert eligible(status, "slot", set(), rerun_completed=True)
        assert not eligible(status, "another-slot", {"slot"})
    assert not eligible("running", "slot", {"slot"})


def test_command_selections_are_bounded_and_use_stable_ids():
    with pytest.raises(ValidationError):
        Command(kind="plan", request_key="key", replicate_ids=[uuid.uuid4() for _ in range(101)])
    with pytest.raises(ValidationError):
        Command(kind="plan", request_key="key", rerun_slot_ids=["ambiguous-label"])
    with pytest.raises(ValidationError):
        Command(kind="execute-immediately", request_key="key")


def test_download_capability_is_signed_and_bound_to_job_and_owner(monkeypatch):
    monkeypatch.setattr(
        result_exports,
        "get_settings",
        lambda: SimpleNamespace(auth_secret_key="test-secret-key-at-least-32-characters"),
    )
    job = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4())
    token = result_exports.download_token(job)
    assert result_exports.validate_download_token(token, job.id) == job.owner_id
    with pytest.raises(ValueError):
        result_exports.validate_download_token(token, uuid.uuid4())
    with pytest.raises(ValueError):
        result_exports.validate_download_token(token + "tampered", job.id)


async def test_export_relay_only_passes_options_arq_understands(monkeypatch):
    """An unknown underscore option (it was `_keep_result`) isn't an arq option: arq
    hands it to the task as a keyword, every export crashed on start, and the job
    sat in "preparing..." forever."""
    import inspect
    from contextlib import asynccontextmanager

    from arq.connections import ArqRedis

    pending = [uuid.uuid4(), uuid.uuid4()]
    results = iter([pending, []])  # pending exports, then expired ones

    class Db:
        async def scalars(self, _statement):
            return SimpleNamespace(all=lambda: next(results))

    @asynccontextmanager
    async def session():
        yield Db()

    calls = []

    class Pool:
        async def enqueue_job(self, function, *args, **kwargs):
            calls.append((function, args, kwargs))

    async def pool():
        return Pool()

    monkeypatch.setattr(result_exports, "get_session", session)
    monkeypatch.setattr(result_exports, "_get_pool", pool)
    await result_exports.relay_exports({})

    arq_options = {name for name in inspect.signature(ArqRedis.enqueue_job).parameters if name.startswith("_")}
    assert [args for _, args, _ in calls] == [(str(identifier),) for identifier in pending]
    for function, _, kwargs in calls:
        assert function == "execute_result_export"
        assert {key for key in kwargs if key.startswith("_")} <= arq_options, kwargs
    # And the task accepts exactly what is passed to it.
    inspect.signature(result_exports.execute_result_export).bind({}, *calls[0][1])


async def test_one_worker_tick_keeps_advancing_a_job_until_it_is_done(monkeypatch):
    """One step per one-second tick set the pace of every preview and batch."""
    from contextlib import asynccontextmanager

    from asaree.services import result_jobs

    job_id = uuid.uuid4()

    class Db:
        async def execute(self, _statement):
            return None

        async def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [job_id])

    @asynccontextmanager
    async def session():
        yield Db()

    remaining = iter([True] * 7 + [False])
    calls = []

    async def advance(identifier):
        calls.append(identifier)
        return next(remaining)

    async def deliver():
        return None

    monkeypatch.setattr(result_jobs, "get_session", session)
    monkeypatch.setattr(result_jobs, "_advance_command", advance)
    monkeypatch.setattr(result_jobs, "deliver_planned_runs", deliver)
    await result_jobs.drain_result_commands({})
    assert calls == [job_id] * 8
