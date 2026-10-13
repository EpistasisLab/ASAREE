"""Command policy and download capabilities, without runtime infrastructure."""

import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from asaree.api.result_jobs import Command
from asaree.services import result_exports
from asaree.services.result_jobs import eligible


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
