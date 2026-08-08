"""Runs.wait()'s poll loop -- the piece backward compatibility with
asaree.api.runs's switch to a background worker actually depends on. start()
now returns as soon as a run is queued; a caller that reads only wait()'s
return (the shape this SDK's own driver caller already used) needs wait()
to genuinely poll rather than re-fetch once.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from asaree_client.models import Run
from asaree_client.resources.runs import Runs


def _run(status: str) -> Run:
    return Run(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        status=status,
        input="2 + 2?",
        output=None,
        output_text="",
        error=None,
        token_usage=None,
        cost_estimate=None,
        created_at=datetime.now(tz=UTC),
        completed_at=None,
    )


def test_wait_polls_until_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses = iter(["pending", "running", "running", "completed"])
    calls = []

    def fake_get(self: Runs, run_id: object) -> Run:
        calls.append(run_id)
        return _run(next(statuses))

    monkeypatch.setattr(Runs, "get", fake_get)
    monkeypatch.setattr("asaree_client.resources.runs.time.sleep", lambda _seconds: None)

    result = Runs(client=object()).wait(uuid.uuid4(), poll_interval=0)

    assert result.status == "completed"
    assert len(calls) == 4


@pytest.mark.parametrize("status", ["paused", "awaiting_human"])
def test_wait_treats_paused_and_awaiting_human_as_non_terminal(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    """A run parked in one of these must not be handed back as if it were
    finished -- nothing here drives either state back to running yet, but a
    caller that got one back and trusted it as terminal would silently act
    on an incomplete result the moment a HITL story lands."""
    seen = iter([status, "completed"])
    monkeypatch.setattr(Runs, "get", lambda self, run_id: _run(next(seen)))
    monkeypatch.setattr("asaree_client.resources.runs.time.sleep", lambda _seconds: None)

    result = Runs(client=object()).wait(uuid.uuid4(), poll_interval=0)

    assert result.status == "completed"


def test_wait_raises_timeout_error_if_never_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Runs, "get", lambda self, run_id: _run("running"))
    monkeypatch.setattr("asaree_client.resources.runs.time.sleep", lambda _seconds: None)

    # Fake a clock that has already passed the deadline by the second read,
    # so this doesn't depend on wall-clock time or a real sleep.
    clock = iter([0.0, 100.0])
    monkeypatch.setattr("asaree_client.resources.runs.time.monotonic", lambda: next(clock))

    with pytest.raises(TimeoutError):
        Runs(client=object()).wait(uuid.uuid4(), timeout=1.0, poll_interval=0)
