"""A wall-clock budget for one agent run that stops ticking while it waits.

``asyncio.wait_for`` takes a fixed duration decided before the wait starts,
which is the wrong shape once an agent can consult a peer: the peer's run
executes *inside* the caller's timeout, so a peer that thinks for three minutes
spends three minutes of a budget meant to bound the caller's own thinking. The
caller would then time out through no fault of its own, and the more useful the
delegation the more likely that becomes.

**Each agent keeps its own wall-clock allowance.** A :class:`Deadline` is a
mutable expiry that the run's supervisor re-reads while it waits, and
:func:`deadlines_paused` freezes it for the span of a consultation.

Pausing rather than crediting the time back afterwards is the whole point: a
consultation longer than the caller's remaining budget would blow the deadline
*while it was still in flight*, and no amount of repayment afterwards brings a
run back. The clock has to stop at the moment the caller starts waiting.

The active chain is a :class:`~contextvars.ContextVar` rather than an argument
threaded through the executor, because the pause has to reach every caller above
the peer, not just the immediate one. A depth-2 consultation blocks the depth-1
caller *and* the entry agent, and those frames are separated by the whole Motoro
engine -- code that must not learn what a peer is. An asyncio task copies the
context at creation and the tuple holds the same ``Deadline`` objects by
reference, so a nested run's pause is visible to every ancestor still waiting.

This is deliberately *not* the backstop. The conversation-level wall clock in
:mod:`asaree.services.agent_messenger` never pauses and does count peer time:
total work stays bounded even though no individual agent is charged for
delegating.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

#: What a paused deadline reports as remaining. Deliberately not the frozen
#: remainder, which can be arbitrarily small -- the supervisor uses this value
#: as its wait interval, and a near-zero one would spin.
_PAUSED_POLL_SECONDS = 0.5


class Deadline:
    """A point in time a run must finish by, which can be paused.

    Monotonic on purpose: a clock adjustment mid-run must not shorten or
    lengthen an agent's allowance. Reentrant, because nested consultations pause
    the same ancestor frames more than once and only the outermost resume may
    restart the clock.
    """

    __slots__ = ("_expires_at", "_paused_at", "_pauses")

    def __init__(self, seconds: float) -> None:
        self._expires_at = time.monotonic() + seconds
        self._paused_at: float | None = None
        self._pauses = 0

    def pause(self) -> None:
        self._pauses += 1
        if self._pauses == 1:
            self._paused_at = time.monotonic()

    def resume(self) -> None:
        self._pauses -= 1
        if self._pauses == 0 and self._paused_at is not None:
            self._expires_at += time.monotonic() - self._paused_at
            self._paused_at = None

    def remaining(self) -> float:
        if self._paused_at is not None:
            return _PAUSED_POLL_SECONDS
        return self._expires_at - time.monotonic()

    def expired(self) -> bool:
        return self._paused_at is None and self._expires_at <= time.monotonic()


_ACTIVE: ContextVar[tuple[Deadline, ...]] = ContextVar("asaree_active_deadlines", default=())


@contextmanager
def active_deadline(deadline: Deadline) -> Iterator[Deadline]:
    """Register *deadline* as the innermost frame of the active caller chain.

    Enter this *before* creating the task that does the work, so the task's
    copied context already contains the frame.
    """
    token = _ACTIVE.set((*_ACTIVE.get(), deadline))
    try:
        yield deadline
    finally:
        _ACTIVE.reset(token)


@contextmanager
def deadlines_paused() -> Iterator[None]:
    """Stop the clock on every agent currently waiting, for this block's span.

    Every frame, not just the innermost: a nested consultation blocks its own
    caller and everyone above it for the same wall-clock span, so none of them
    should be charged for it. The frames are captured on entry so an exception
    inside cannot leave a deadline paused forever.
    """
    frames = _ACTIVE.get()
    for deadline in frames:
        deadline.pause()
    try:
        yield
    finally:
        for deadline in frames:
            deadline.resume()


__all__ = ["Deadline", "active_deadline", "deadlines_paused"]
