"""Delivery of one agent's question to a connected peer, and the run that hosts it.

Motoro's :class:`~motoro.engine.ports.AgentMessengerPort` is deliberately one
method wide: the engine projects the peers a caller declared into callable
function schemas and hands any resulting call straight back here. Everything
that decides *whether* the call happens lives in this module -- authorization,
message identity and ordering, the transcript, the budget, and cancellation.
The engine never learns what an ASAREE canvas is.

**The reply is a call result, not an exception.** A peer that may not be
reached, a spent budget and a cancelled conversation all come back as an
:class:`~motoro.engine.ports.AgentReply` with a state the calling model can
read, so it absorbs the outcome and still writes a real answer. Only genuine
infrastructure failure raises.

**Capability is snapshotted; reachability is live.** A peer's card describes it
as the published revision configures it, so a canvas edit cannot hot-patch a
run's agents mid-flight. Authorization is re-asked of the *draft* graph on every
single consultation, so pulling the edge on the canvas stops the next
consultation immediately. Two different questions, deliberately reading two
different graphs.

This module imports the executor, never the reverse. A pipeline run knows
nothing about conversations, which is what keeps invariant 11 (single-agent runs
are byte-for-byte unaffected) structural rather than a promise.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from motoro.engine.ports import AgentReply

from asaree.models.database import get_session
from asaree.services.deadline import deadlines_paused
from asaree.services.experiments import get_experiment
from asaree.services.protocol_execution import (
    _AGENT_CANCELLED,
    _ambient_meta_for,
    _can_deliver_communication,
    _compute_workspace_id,
    _run_agent_node,
    resolve_available_agents,
)
from asaree.services.protocol_revisions import get_revision
from asaree.services.protocol_runs import get_protocol_run, set_status, update_conversation, update_node_run
from asaree.services.protocols import get_protocol

logger = logging.getLogger(__name__)

#: Total peer turns allowed per protocol run, across the whole conversation
#: tree. Every one of these is a full agent run with its own Reason/Plan/Act
#: cycle and its own tokens, so this is a cost cap as much as a loop guard.
_MAX_PEER_EXECUTIONS = 8

#: The real backstop. Deliberately **not** extended by peer time the way an
#: individual agent's own deadline is (see :mod:`asaree.services.deadline`):
#: no agent is charged for delegating, but the total stays bounded.
_MAX_CONVERSATION_DURATION = timedelta(minutes=5)

#: How deep consultations may nest. Two is enough for "Planner asks Critic,
#: Critic asks a clarifying question back" -- the shape this feature exists for
#: -- without letting a chain of agents each delegate one level further.
_MAX_CONSULT_DEPTH = 2

#: The transcript's stand-in sender for the human who started the conversation.
#: Not a node id, and deliberately not a valid one: nothing can address it, and
#: authorization would refuse if anything tried.
USER_PARTICIPANT = "user"


def _text_of(parts: list[dict[str, Any]]) -> str:
    return "\n".join(str(p.get("text", "")) for p in parts if p.get("kind") == "text").strip()


class AgentMessenger:
    """One per protocol run. Owns that run's whole agent-to-agent surface.

    Holds the conversation document in memory as the authoritative copy and
    checkpoints it to ``ProtocolRun.conversation`` around every peer execution,
    which is what lets a worker retry see exactly how far it got.

    Not concurrency-safe, by design: invariant 5 is that one agent executes at a
    time and a consulting agent blocks on its peer's reply, so ``sequence`` and
    the budget counters are only ever touched from a single logical call stack.
    """

    def __init__(
        self,
        *,
        protocol_id: uuid.UUID,
        protocol_run_id: uuid.UUID,
        owner_id: uuid.UUID,
        graph: dict[str, Any],
        entry_agent_id: str,
        workspace_id: str | None = None,
    ) -> None:
        #: The *revision* graph -- capability: who each peer is and how it runs.
        #: Authorization reads the live draft graph instead, on every call.
        self._graph = graph
        self._protocol_id = protocol_id
        self._protocol_run_id = protocol_run_id
        self._owner_id = owner_id
        self._workspace_id = workspace_id
        self._entry_agent_id = entry_agent_id
        self._started_at = time.monotonic()
        self._executions = 0
        self._depth = 0
        self._sequence = 0
        self._messages: list[dict[str, Any]] = []
        self._state = "working"
        #: Set when a cap is what stopped the conversation, so the run can land
        #: on ``limit_reached`` rather than looking like a clean completion.
        self.limit_reached = False

    # -- transcript ----------------------------------------------------

    @property
    def conversation(self) -> dict[str, Any]:
        """The transcript as stored: one JSON document, read whole."""
        return {"state": self._state, "entry_agent_id": self._entry_agent_id, "messages": list(self._messages)}

    def set_state(self, state: str) -> None:
        self._state = state

    def append(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        parts: list[dict[str, Any]],
        state: str | None = None,
    ) -> dict[str, Any]:
        """Record one message with a runtime-assigned id and ``sequence``.

        Invariant 2: identity and ordering are assigned here, never taken from
        model output. ``state`` is set on replies only -- a request has no
        outcome of its own yet.
        """
        self._sequence += 1
        message = {
            "message_id": str(uuid.uuid4()),
            "sequence": self._sequence,
            "from_agent_id": from_agent_id,
            "to_agent_id": to_agent_id,
            "parts": parts,
            "created_at": datetime.now(UTC).isoformat(),
            **({"state": state} if state is not None else {}),
        }
        self._messages.append(message)
        return message

    async def checkpoint(self) -> None:
        async with get_session() as db:
            await update_conversation(db, self._protocol_run_id, self.conversation)

    # -- delivery ------------------------------------------------------

    async def send(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        parts: list[dict[str, Any]],
        context: Any = None,
    ) -> AgentReply:
        """Deliver one question to a peer and return its reply.

        The request is recorded *before* any refusal check runs, so a rejected
        consultation still appears in the transcript (invariant 10) -- a
        silently dropped question is exactly the failure this makes debuggable.

        ``context`` is the engine's ``RunContext``. Unused: everything this
        needs is per-protocol-run state held on the instance, and reading run
        state out of the engine's context would be a second source of truth.
        """
        self.append(from_agent_id=from_agent_id, to_agent_id=to_agent_id, parts=parts)

        refusal = await self._refusal(from_agent_id, to_agent_id)
        if refusal is not None:
            logger.info("consultation refused (%s -> %s): %s", from_agent_id, to_agent_id, refusal)
            return await self._reply(to_agent_id, from_agent_id, refusal, state="rejected")

        await self.checkpoint()

        self._executions += 1
        self._depth += 1
        try:
            # The caller's own clock stops for exactly this span, failures
            # included: it waited either way, and charging it for a peer's
            # failure is the same unfairness as charging it for a peer's
            # success. The conversation-level cap above keeps ticking.
            with deadlines_paused():
                output_text, error, run_id = await self._run_peer(to_agent_id, parts)
        finally:
            self._depth -= 1

        if error == _AGENT_CANCELLED:
            return await self._reply(
                to_agent_id, from_agent_id, "The consultation was cancelled.", state="canceled", task_id=run_id
            )
        if error is not None:
            return await self._reply(
                to_agent_id,
                from_agent_id,
                f"The agent could not answer: {error}",
                state="failed",
                task_id=run_id,
                error=error,
            )
        text = (output_text or "").strip()
        return await self._reply(
            to_agent_id,
            from_agent_id,
            text or "The agent finished without producing an answer.",
            state="completed",
            task_id=run_id,
        )

    async def _reply(
        self,
        from_agent_id: str,
        to_agent_id: str,
        text: str,
        *,
        state: str,
        task_id: str | None = None,
        error: str | None = None,
    ) -> AgentReply:
        parts = [{"kind": "text", "text": text}]
        self.append(from_agent_id=from_agent_id, to_agent_id=to_agent_id, parts=parts, state=state)
        await self.checkpoint()
        return AgentReply(state=state, parts=parts, task_id=task_id, error=error)

    async def _refusal(self, from_agent_id: str, to_agent_id: str) -> str | None:
        """The reason this consultation may not proceed, or ``None``.

        Every branch returns prose addressed to the calling *model*, because
        that is who reads it: it has to be able to tell "you may not ask this
        agent" from "you have asked enough" and choose differently, so a bare
        "rejected" would be worse than useless.
        """
        if self._depth >= _MAX_CONSULT_DEPTH:
            self.limit_reached = True
            return (
                f"Consultations are already nested {_MAX_CONSULT_DEPTH} deep, which is the limit. "
                "Answer with what you have rather than delegating further."
            )
        if self._executions >= _MAX_PEER_EXECUTIONS:
            self.limit_reached = True
            return (
                f"This conversation has used all {_MAX_PEER_EXECUTIONS} of its peer consultations. "
                "Answer with what you already have."
            )
        if time.monotonic() - self._started_at >= _MAX_CONVERSATION_DURATION.total_seconds():
            self.limit_reached = True
            return (
                f"This conversation has run for its full {int(_MAX_CONVERSATION_DURATION.total_seconds())} seconds. "
                "Answer with what you already have."
            )

        async with get_session() as db:
            run = await get_protocol_run(db, self._protocol_run_id)
            if run is not None and run.cancel_requested_at is not None:
                # Invariant 8: a Stop seen between consultations stops further
                # ones. An already in-flight peer run is separately interrupted
                # by _execute_run_cancellable's own poller.
                return "This run was cancelled, so the consultation was not delivered."
            protocol = await get_protocol(db, self._protocol_id)

        # The live draft graph, not the revision this run's agents came from.
        live_graph = protocol.graph if protocol is not None else self._graph
        if not _can_deliver_communication(live_graph, from_agent_id, to_agent_id):
            return (
                "That agent is not connected to you on the canvas, so it cannot be consulted. "
                "Answer using the agents listed for you, or with what you already have."
            )
        return None

    async def _run_peer(
        self, to_agent_id: str, parts: list[dict[str, Any]]
    ) -> tuple[str | None, str | None, str | None]:
        """Give the peer its own full turn.

        A real nested agent run, not a prompt trick: its own Motoro ``AgentRun``,
        so cost, steps and the Runs tab attribute it separately, and its own
        ``available_agents`` so it may consult back within the depth cap.
        """
        node = next((n for n in self._graph.get("nodes") or [] if str(n.get("id")) == to_agent_id), None)
        if node is None:
            # Only reachable if the live graph and the revision disagree about
            # whether a node exists, which authorization above cannot catch.
            return None, "the agent no longer exists in this protocol revision", None

        async with get_session() as db:
            await update_node_run(db, self._protocol_run_id, to_agent_id, {"status": "running"})

        output_text, error, run_id = await _run_agent_node(
            node,
            protocol_id=self._protocol_id,
            protocol_run_id=self._protocol_run_id,
            owner_id=self._owner_id,
            user_input=_text_of(parts),
            graph=self._graph,
            workspace_id=self._workspace_id,
            ambient_meta=_ambient_meta_for(self._graph, to_agent_id, self._workspace_id),
            available_agents=await resolve_available_agents(self._graph, to_agent_id, owner_id=self._owner_id),
            agent_messenger=self,
        )
        # The canvas shows a consulted peer as a node that ran, because it did.
        # A peer consulted twice keeps only its latest turn here; the full
        # sequence is the transcript's job, not node_runs'.
        async with get_session() as db:
            await update_node_run(
                db,
                self._protocol_run_id,
                to_agent_id,
                {
                    "status": "cancelled" if error == _AGENT_CANCELLED else ("failed" if error else "completed"),
                    "output_text": output_text,
                    "error": None if error == _AGENT_CANCELLED else error,
                    "run_id": str(run_id) if run_id else None,
                },
            )
        return output_text, error, str(run_id) if run_id else None


async def run_conversation(protocol_run_id: uuid.UUID, *, entry_agent_id: str, user_input: str) -> None:
    """Execute a protocol run in conversation mode.

    Deliberately *not* a turn scheduler. It starts exactly one agent -- the one
    the user addressed -- and consultation is driven from inside that run by the
    messenger, as a nested call. That is what makes the single-agent loop
    unchanged: an agent finishes by writing its answer, not by choosing a
    ``finish`` action, and the only new thing in its world is that a peer exists
    and can be asked.

    So what remains here is: seed the transcript, run the entry agent, map its
    outcome to a terminal conversation state, checkpoint.
    """
    async with get_session() as db:
        run = await get_protocol_run(db, protocol_run_id)
        if run is None:
            return
        protocol = await get_protocol(db, run.protocol_id)
        if protocol is None:
            await set_status(db, protocol_run_id, status="failed", error="protocol no longer exists")
            return
        protocol_id, owner_id, graph = protocol.id, run.owner_id, protocol.graph
        if run.protocol_revision_id is not None:
            revision = await get_revision(db, run.protocol_revision_id)
            if revision is None:
                await set_status(
                    db, protocol_run_id, status="failed", error="published protocol revision no longer exists"
                )
                return
            graph = revision.graph
        experiment_id = protocol.experiment_id
        experiment = await get_experiment(db, experiment_id) if experiment_id else None
        evaluation_metrics = (experiment.design_spec or {}).get("metrics") if experiment is not None else None

    node = next((n for n in graph.get("nodes") or [] if str(n.get("id")) == entry_agent_id), None)
    if node is None or node.get("type") != "agent":
        async with get_session() as db:
            await set_status(db, protocol_run_id, status="failed", error="entry agent is not an Agent node")
        return

    workspace_id = _compute_workspace_id(experiment_id, None, protocol_run_id)
    messenger = AgentMessenger(
        protocol_id=protocol_id,
        protocol_run_id=protocol_run_id,
        owner_id=owner_id,
        graph=graph,
        entry_agent_id=entry_agent_id,
        workspace_id=workspace_id,
    )
    messenger.append(
        from_agent_id=USER_PARTICIPANT,
        to_agent_id=entry_agent_id,
        parts=[{"kind": "text", "text": user_input}],
    )

    async with get_session() as db:
        await set_status(db, protocol_run_id, status="running")
        await update_node_run(db, protocol_run_id, entry_agent_id, {"status": "running"})
    await messenger.checkpoint()

    output_text, error, run_id = await _run_agent_node(
        node,
        protocol_id=protocol_id,
        protocol_run_id=protocol_run_id,
        owner_id=owner_id,
        user_input=user_input,
        graph=graph,
        workspace_id=workspace_id,
        ambient_meta=_ambient_meta_for(graph, entry_agent_id, workspace_id),
        evaluation_metrics=evaluation_metrics,
        available_agents=await resolve_available_agents(graph, entry_agent_id, owner_id=owner_id),
        agent_messenger=messenger,
    )

    cancelled = error == _AGENT_CANCELLED
    if not cancelled and error is None:
        messenger.append(
            from_agent_id=entry_agent_id,
            to_agent_id=USER_PARTICIPANT,
            parts=[{"kind": "text", "text": output_text or ""}],
            state="completed",
        )
    # A cap that was hit but absorbed still produced a real answer, so the
    # conversation is `completed` -- `limit_reached` is reserved for a cap that
    # actually stopped it. Invariant 7 in the run's own status.
    if cancelled:
        state, status = "canceled", "cancelled"
    elif error is not None:
        state, status = ("limit_reached", "limit_reached") if messenger.limit_reached else ("failed", "failed")
    else:
        state, status = "completed", "completed"
    messenger.set_state(state)
    await messenger.checkpoint()

    async with get_session() as db:
        await update_node_run(
            db,
            protocol_run_id,
            entry_agent_id,
            {
                "status": "cancelled" if cancelled else ("failed" if error else "completed"),
                "output_text": output_text,
                "error": None if cancelled else error,
                "run_id": str(run_id) if run_id else None,
            },
        )
        await set_status(db, protocol_run_id, status=status, error=None if cancelled else error)


__all__ = [
    "USER_PARTICIPANT",
    "AgentMessenger",
    "run_conversation",
]
