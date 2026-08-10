"""Run resource, matching asaree.api.runs.

``POST /runs`` enqueues a run for the background worker (asaree.worker) and
returns as soon as it's queued (status ``PENDING``), not once it's terminal
— execution happens out of band. ``start`` returns quickly; ``wait`` is the
long call now, polling ``GET /runs/{id}`` until the run reaches a terminal
status. This is the inverse of this module's own history: it used to be
``start`` that blocked (inline execution, pre-worker) and ``wait`` that was a
same-request no-op re-fetch — callers written against that shape (``start``
immediately followed by ``wait(timeout=..., poll_interval=...)``, reading
only ``wait``'s return) need no changes; the ones already accepted-and-
ignored ``timeout``/``poll_interval`` kwargs on ``wait`` are now real.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from asaree_client.models import Run, RunStep

ResourceId = uuid.UUID | str

# paused/awaiting_human are deliberately NOT terminal here: nothing in this
# codebase drives either state back to running yet, so treating them as
# "done" would have wait() silently under-deliver the moment a HITL story
# lands and a caller's run parks in one of them mid-flight.
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class Runs:
    def __init__(self, client: Any) -> None:
        self._client = client

    def start(
        self,
        agent_id: ResourceId,
        user_input: str,
        *,
        pattern_overrides: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        model_config_override: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Run:
        """Create a run and enqueue it. Returns immediately (status PENDING)
        — call ``wait`` for the terminal result.

        *timeout* overrides the client's default HTTP timeout for this one
        call. No longer the long-blocking call it once was (enqueueing is
        fast), but still accepted: a caller passing it for the old reason
        does no harm passing it for no reason.
        """
        payload: dict[str, Any] = {"agent_id": str(agent_id), "user_input": user_input}
        if pattern_overrides is not None:
            payload["pattern_overrides"] = pattern_overrides
        if metadata is not None:
            payload["metadata"] = metadata
        if model_config_override is not None:
            payload["model_config_override"] = model_config_override
        kwargs: dict[str, Any] = {"json": payload}
        if timeout is not None:
            kwargs["timeout"] = timeout
        data = self._client._post("/runs", **kwargs)
        return Run(**data)

    def get(self, run_id: ResourceId) -> Run:
        data = self._client._get(f"/runs/{run_id}")
        return Run(**data)

    def wait(
        self,
        run_id: ResourceId,
        *,
        timeout: float | None = None,
        poll_interval: float = 2.0,
    ) -> Run:
        """Poll until *run_id* reaches a terminal status (completed, failed,
        cancelled), then return it.

        *timeout* bounds the total wait, in seconds — ``None`` (the default)
        polls indefinitely. Raises ``TimeoutError`` if it elapses with the run
        still non-terminal, rather than returning a PENDING/RUNNING run that
        would look like a normal result to a caller that doesn't check.
        """
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            run = self.get(run_id)
            if run.status in _TERMINAL_STATUSES:
                return run
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(
                    f"run {run_id} did not reach a terminal status within {timeout}s (last status: {run.status})"
                )
            time.sleep(poll_interval)

    def list_all(
        self,
        *,
        agent_id: ResourceId | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[Run]:
        """*metadata*, if given, requires every key/value pair to match a
        run's own ``run_metadata`` exactly, filtered server-side (in SQL) —
        NOT by fetching every run for *agent_id* and checking client-side:
        that silently drops matches once the agent has more total runs than
        *limit* would otherwise return, with nothing to signal it happened.
        Pass *metadata* to filter on e.g. an experiment id you stamped onto
        ``run_metadata`` at ``start()`` time, instead of pulling everything.
        """
        params: dict[str, Any] = {}
        if agent_id is not None:
            params["agent_id"] = str(agent_id)
        if status is not None:
            params["status"] = status
        if metadata is not None:
            params["metadata"] = json.dumps(metadata)
        if limit is not None:
            params["limit"] = limit
        data = self._client._get("/runs", params=params or None)
        return [Run(**r) for r in data]

    def get_steps(self, run_id: ResourceId) -> list[RunStep]:
        data = self._client._get(f"/runs/{run_id}/steps")
        return [RunStep(**s) for s in data]
