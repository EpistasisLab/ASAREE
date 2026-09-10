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

**Every turn reads the whole conversation.** A consulted peer is given the
transcript so far (:meth:`AgentMessenger._briefing`) ahead of the question, so it
recalls its own earlier turns and can build on what other agents have already
found. That is one mechanism serving both, because both are the same question --
what does this turn get to read. The entry agent needs no briefing: it is a
single continuous run, so its own scratchpad already holds every reply it got.

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

import asyncio
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from asaree_workspace_core import agent_slot
from motoro.engine.ports import AgentReply

from asaree.models.database import get_session
from asaree.services.dataset_workspaces import head_data_locator
from asaree.services.deadline import deadlines_paused
from asaree.services.prompt_contract import LEGACY_PROMPT_CONTRACT
from asaree.services.protocol_execution import (
    _AGENT_CANCELLED,
    SupervisorRoles,
    _build_user_input,
    _can_deliver_communication,
    _node_run_context,
    _run_agent_node,
    resolve_available_agents,
)
from asaree.services.protocol_runs import get_protocol_run, update_conversation, update_node_run
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

#: How much of one earlier message a briefing reproduces. A peer's own analysis
#: can run to thousands of tokens, and eight of them would crowd out the
#: question actually being asked. Truncation is marked so the reading model can
#: tell a cut-off answer from a short one.
_MAX_BRIEFING_CHARS_PER_MESSAGE = 1500

#: How a non-``completed`` reply is described in a briefing. A turn that was
#: refused or failed is part of what happened and stays in the transcript, but
#: it must not read as an answer somebody gave.
_BRIEFING_STATE_NOTE = {
    "rejected": " (refused)",
    "failed": " (could not answer)",
    "canceled": " (cancelled)",
}


def _text_of(parts: list[dict[str, Any]]) -> str:
    return "\n".join(str(p.get("text", "")) for p in parts if p.get("kind") == "text").strip()


class AgentMessenger:
    """One per protocol run. Owns that run's whole agent-to-agent surface.

    Holds the conversation document in memory as the authoritative copy and
    checkpoints it to ``ProtocolRun.conversation`` around every peer execution,
    which is what lets a worker retry see exactly how far it got.

    The consultation path (:meth:`send`) is single-threaded by design:
    invariant 5 is that one agent executes at a time and a consulting agent
    blocks on its peer's reply, so ``sequence``, the budget counters and the
    turn stack are only ever touched from a single logical call stack.

    :func:`execute_supervisor_architecture` is the deliberate exception -- it
    dispatches workers concurrently rather than having a model ask for them --
    and it uses the *transcript* only, through :meth:`record`, which is locked.
    It never calls :meth:`send`, so nothing about the budget or the turn stack
    is ever touched concurrently.
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
        stage_plan: Any = None,
    ) -> None:
        #: The *revision* graph -- capability: who each peer is and how it runs.
        #: Authorization reads the live draft graph instead, on every call.
        self._graph = graph
        self._protocol_id = protocol_id
        self._protocol_run_id = protocol_run_id
        self._owner_id = owner_id
        self._workspace_id = workspace_id
        #: The experiment's declared workspace stage plan, carried so a peer
        #: consulted mid-conversation seeds into the same pipeline as everyone
        #: else. ``None`` means "whatever this cell already stages through".
        self._stage_plan = stage_plan
        self._entry_agent_id = entry_agent_id
        self._started_at = time.monotonic()
        self._executions = 0
        self._sequence = 0
        #: Canvas node ids of the agents whose turns are currently on the stack,
        #: innermost last. This -- not anything the engine hands back -- is who
        #: is speaking: invariant 2 says the runtime assigns sender identity, and
        #: invariant 5 (one agent at a time, a consulting agent blocked on its
        #: peer) is exactly what makes a stack the right shape. Motoro's own
        #: ``from_agent_id`` is ``RunContext.agent_id``, the *durable* Agent row
        #: for this turn, which is not a node id and so can't be authorized
        #: against the graph at all -- see :meth:`send`.
        self._turn_stack: list[str] = []
        self._messages: list[dict[str, Any]] = []
        #: Guards append-then-checkpoint for the one caller that runs agents
        #: concurrently -- see :meth:`record`. Uncontended everywhere else.
        self._lock = asyncio.Lock()
        #: Node id -> the label the canvas shows, so a briefing names agents the
        #: way the user does. Same source ``build_agent_card`` uses, and the same
        #: node-id fallback, so a peer is called one thing everywhere.
        self._display_names = {
            str(n.get("id")): str((n.get("data") or {}).get("label") or "").strip() or str(n.get("id"))
            for n in graph.get("nodes") or []
            if n.get("type") == "agent"
        }
        self._state = "working"
        #: Set when a cap is what stopped the conversation, so the run can land
        #: on ``limit_reached`` rather than looking like a clean completion.
        self.limit_reached = False

    # -- turns ---------------------------------------------------------

    @contextmanager
    def turn(self, node_id: str) -> Iterator[None]:
        """Mark *node_id* as the agent now speaking, for the duration of its run.

        Every agent turn in a conversation is wrapped in this -- the entry
        agent's included -- so that a consultation raised from inside it is
        attributed to the right canvas node without trusting anything the engine
        or the model supplies.
        """
        self._turn_stack.append(node_id)
        try:
            yield
        finally:
            self._turn_stack.pop()

    @property
    def _depth(self) -> int:
        """How deeply consultations are nested right now.

        The entry agent's own turn is not a consultation, so it doesn't count --
        depth 0 is "the agent the user addressed is asking its first peer".
        """
        return max(len(self._turn_stack) - 1, 0)

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

    async def record(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        parts: list[dict[str, Any]],
        state: str | None = None,
    ) -> dict[str, Any]:
        """:meth:`append` and :meth:`checkpoint` as one atomic step.

        For :func:`execute_supervisor_architecture`, whose workers really do run
        concurrently -- the one place invariant 5 (one agent at a time) does not
        hold, because ASAREE dispatches those turns itself rather than one model
        blocking on another. ``append`` alone is already safe under asyncio (it
        touches ``_sequence`` and the list with no await in between), but the
        checkpoint that follows does await, so two finishing workers could
        otherwise write the transcript out of order and leave a stale document
        as the last word. The lock makes "assign a sequence, then persist"
        indivisible.
        """
        async with self._lock:
            message = self.append(from_agent_id=from_agent_id, to_agent_id=to_agent_id, parts=parts, state=state)
            await self.checkpoint()
        return message

    # -- briefing ------------------------------------------------------

    def _display_name(self, participant_id: str) -> str:
        if participant_id == USER_PARTICIPANT:
            return "The user"
        return self._display_names.get(participant_id, participant_id)

    def _briefing(self, *, from_agent_id: str, to_agent_id: str, exclude_message_id: str) -> str:
        """What has already been said, rendered for the agent about to speak.

        This is the whole of both "a peer remembers its own earlier turns" and
        "agents see each other's work". One mechanism, because they are the same
        question -- what does this turn get to read -- and splitting them would
        mean two things to keep consistent.

        Every participant sees the *entire* transcript, not a filtered view: a
        conversation exists so that agents can build on each other, and deciding
        for them which of their colleagues' findings are relevant would be the
        orchestrator doing the reasoning. Everything here happened inside one
        protocol run owned by one user, so there is nothing to partition.

        Memory is *reconstructed* rather than resumed: a peer still gets a fresh
        ``AgentRun`` per turn, and this is what carries its history across them.
        That keeps each consultation separately attributable in the Runs tab and
        priced on its own, which a resumed run would lose -- and it means a
        retried worker rebuilds identical context from the checkpointed
        transcript instead of needing a live run to still exist.

        Returns ``""`` when there is nothing to report, so the very first
        consultation of a conversation reads exactly as it did before.
        """
        entries: list[str] = []
        for message in self._messages:
            if message["message_id"] == exclude_message_id:
                continue
            body = _text_of(message["parts"])
            if not body:
                continue
            if len(body) > _MAX_BRIEFING_CHARS_PER_MESSAGE:
                body = body[:_MAX_BRIEFING_CHARS_PER_MESSAGE].rstrip() + " [...truncated]"
            note = _BRIEFING_STATE_NOTE.get(str(message.get("state") or ""), "")
            sender = self._display_name(message["from_agent_id"])
            recipient = self._display_name(message["to_agent_id"])
            entries.append(f"{sender} -> {recipient}{note}:\n{body}")
        if not entries:
            return ""
        # Second person and the agent's own name together: the transcript refers
        # to it in the third person, so it has to be able to find itself in what
        # it is reading.
        return (
            f"You are {self._display_name(to_agent_id)}, taking part in a conversation between agents "
            "working on the same problem. Everything said so far is below, including your own earlier "
            "turns. Build on it rather than starting over, and don't repeat work that is already done.\n\n"
            "--- conversation so far ---\n"
            + "\n\n".join(entries)
            + "\n--- end of conversation ---\n\n"
            + f"{self._display_name(from_agent_id)} is now asking you:\n\n"
        )

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

        ``from_agent_id`` is the engine's view of the caller -- ``RunContext``'s
        durable ``agent_id``, the reusable Agent row this turn ran as. That is
        the wrong identity here and is deliberately **ignored**: authorization,
        the transcript and the briefing all speak in canvas node ids, one
        durable agent can back turns for more than one node, and taking the
        sender from outside would put identity in the caller's hands (invariant
        2). The agent speaking is whichever turn is innermost on the stack --
        see :meth:`turn`.

        ``context`` is the engine's ``RunContext``. Unused for the same reason:
        everything this needs is per-protocol-run state held on the instance,
        and reading run state out of the engine's context would be a second
        source of truth.
        """
        sender_id = self._turn_stack[-1] if self._turn_stack else self._entry_agent_id
        request = self.append(from_agent_id=sender_id, to_agent_id=to_agent_id, parts=parts)

        refusal = await self._refusal(sender_id, to_agent_id)
        if refusal is not None:
            logger.info("consultation refused (%s -> %s): %s", sender_id, to_agent_id, refusal)
            return await self._reply(to_agent_id, sender_id, refusal, state="rejected")

        await self.checkpoint()

        # The caller's ``data_path`` was bound into its ambient ``_meta`` when
        # its turn started, and Motoro copies that dict once per run
        # (``RunContext.ambient_meta``) then reads the frozen copy on every tool
        # call. So if the peer about to run accepts a stage, HEAD moves and the
        # caller resumes still pointing at the pre-consultation matrix --
        # silently fitting on stale data. Invariant 5 makes the two turns
        # sequential, which prevents a race but not this.
        head_before = head_data_locator(self._workspace_id)[0] if self._workspace_id else ""

        self._executions += 1
        # The caller's own clock stops for exactly this span, failures
        # included: it waited either way, and charging it for a peer's
        # failure is the same unfairness as charging it for a peer's
        # success. The conversation-level cap above keeps ticking.
        with deadlines_paused():
            # Built here, before the peer runs, so it is a snapshot of the
            # conversation as it stood when the question was asked.
            briefing = self._briefing(
                from_agent_id=sender_id,
                to_agent_id=to_agent_id,
                exclude_message_id=request["message_id"],
            )
            # `turn` is what makes the peer the sender of anything IT asks, and
            # is also what advances the depth this consultation is nested at.
            with self.turn(to_agent_id):
                output_text, error, run_id = await self._run_peer(to_agent_id, parts, briefing=briefing)

        if error == _AGENT_CANCELLED:
            return await self._reply(
                to_agent_id, sender_id, "The consultation was cancelled.", state="canceled", task_id=run_id
            )
        if error is not None:
            return await self._reply(
                to_agent_id,
                sender_id,
                f"The agent could not answer: {error}",
                state="failed",
                task_id=run_id,
                error=error,
            )
        text = (output_text or "").strip()
        return await self._reply(
            to_agent_id,
            sender_id,
            (text or "The agent finished without producing an answer.") + self._stale_data_note(head_before),
            state="completed",
            task_id=run_id,
        )

    def _stale_data_note(self, head_before: str) -> str:
        """A warning appended to a reply when the peer moved the workspace HEAD.

        Addressed to the calling *model*, because it is the only party that can
        act on it: its bound ``data_path`` is frozen for the rest of its run
        (see :meth:`send`), so the fix is to stop using the path argument and
        let the workspace tools resolve HEAD themselves. Told rather than
        silently corrected because correcting it would mean reaching into
        Motoro's already-copied ``RunContext.ambient_meta``; Phase 4's per-agent
        slots remove the window structurally instead.
        """
        if not self._workspace_id:
            return ""
        head_after = head_data_locator(self._workspace_id)[0]
        if not head_after or head_after == head_before:
            return ""
        return (
            "\n\n[SYSTEM] While you were waiting, that agent advanced this workspace to a new "
            "version of the data. The file path you were given at the start of your turn now "
            "points at the OLD version. Do not pass data_path (or test_path/target_column) to any "
            "tool from here on -- call the workspace and sklearn tools with those arguments omitted "
            "so they resolve the current HEAD, and call workspace_status() first if you need to see "
            "what changed."
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
        self, to_agent_id: str, parts: list[dict[str, Any]], *, briefing: str = ""
    ) -> tuple[str | None, str | None, str | None]:
        """Give the peer its own full turn.

        A real nested agent run, not a prompt trick: its own Motoro ``AgentRun``,
        so cost, steps and the Runs tab attribute it separately, and its own
        ``available_agents`` so it may consult back within the depth cap.

        *briefing* (:meth:`_briefing`) prefixes the question with the
        conversation so far. It rides on ``user_input`` because that is the one
        channel the model actually reads -- ``ambient_meta`` is bound into MCP
        tool calls and never shown to it.
        """
        node = next((n for n in self._graph.get("nodes") or [] if str(n.get("id")) == to_agent_id), None)
        if node is None:
            # Only reachable if the live graph and the revision disagree about
            # whether a node exists, which authorization above cannot catch.
            return None, "the agent no longer exists in this protocol revision", None

        async with get_session() as db:
            await update_node_run(db, self._protocol_run_id, to_agent_id, {"status": "running"})

        # The same References resolution a pipeline node gets, not just the
        # bare ambient meta: a consulted peer with a Dataset connector needs its
        # workspace seeded and its `data_path` bound before it can run a script,
        # exactly like any other node.
        ambient_meta, dataset = await _node_run_context(
            self._graph, to_agent_id, self._workspace_id, self._owner_id, stage_plan=self._stage_plan
        )
        # A consultation reply is prose the asking agent reads, never a typed
        # value anything binds to, so whatever the peer's parser extracted (if
        # it even ran) is discarded here rather than stored under the peer's
        # node run: this turn isn't that node's own pipeline run.
        output_text, error, run_id, _extraction = await _run_agent_node(
            node,
            protocol_id=self._protocol_id,
            protocol_run_id=self._protocol_run_id,
            owner_id=self._owner_id,
            user_input=f"{briefing}{_text_of(parts)}",
            graph=self._graph,
            workspace_id=self._workspace_id,
            ambient_meta=ambient_meta,
            available_agents=await resolve_available_agents(self._graph, to_agent_id, owner_id=self._owner_id),
            agent_messenger=self,
            unsplit_dataset=dataset.unsplit_name,
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


async def execute_conversation(
    protocol_run_id: uuid.UUID,
    *,
    protocol_id: uuid.UUID,
    owner_id: uuid.UUID,
    graph: dict[str, Any],
    entry_agent_id: str,
    user_input: str,
    workspace_id: str | None,
    ambient_meta: dict[str, Any] | None = None,
    evaluation_metrics: Any = None,
    stage_plan: Any = None,
    unsplit_dataset: str = "",
) -> tuple[dict[str, Any], str]:
    """The conversation itself: seed the transcript, run the entry agent, map
    its outcome to a terminal conversation state, checkpoint.

    Deliberately *not* a turn scheduler. It starts exactly one agent -- the one
    the user addressed -- and consultation is driven from inside that run by the
    messenger, as a nested call. That is what makes the single-agent loop
    unchanged: an agent finishes by writing its answer, not by choosing a
    ``finish`` action, and the only new thing in its world is that a peer exists
    and can be asked.

    Returns the entry agent's node-run dict and the terminal ``ProtocolRun``
    status. It writes the node run but deliberately *not* the run's status,
    because its caller owns it: a ``peer_collaboration`` factorial cell run
    wraps this in the same pre-write / result / metric-promotion path every
    other cell run uses, so the status stays written in one place.

    *ambient_meta* is likewise the caller's when it has already resolved the
    entry agent's References to build *user_input* (a cell run does, to get the
    dataset and script cues into the prompt) -- resolving it twice would seed
    the workspace twice.
    """
    messenger = AgentMessenger(
        protocol_id=protocol_id,
        protocol_run_id=protocol_run_id,
        owner_id=owner_id,
        graph=graph,
        entry_agent_id=entry_agent_id,
        workspace_id=workspace_id,
        stage_plan=stage_plan,
    )
    messenger.append(
        from_agent_id=USER_PARTICIPANT,
        to_agent_id=entry_agent_id,
        parts=[{"kind": "text", "text": user_input}],
    )

    node = next(n for n in graph.get("nodes") or [] if str(n.get("id")) == entry_agent_id)
    async with get_session() as db:
        await update_node_run(db, protocol_run_id, entry_agent_id, {"status": "running"})
    await messenger.checkpoint()

    if ambient_meta is None:
        ambient_meta, entry_dataset = await _node_run_context(
            graph, entry_agent_id, workspace_id, owner_id, stage_plan=stage_plan
        )
        unsplit_dataset = unsplit_dataset or entry_dataset.unsplit_name

    # The entry agent's turn is a turn like any other: without this, the peers
    # it consults would be recorded and authorized against an empty stack.
    with messenger.turn(entry_agent_id):
        output_text, error, run_id, extraction = await _run_agent_node(
            node,
            protocol_id=protocol_id,
            protocol_run_id=protocol_run_id,
            owner_id=owner_id,
            user_input=user_input,
            graph=graph,
            workspace_id=workspace_id,
            ambient_meta=ambient_meta,
            evaluation_metrics=evaluation_metrics,
            available_agents=await resolve_available_agents(graph, entry_agent_id, owner_id=owner_id),
            agent_messenger=messenger,
            unsplit_dataset=unsplit_dataset,
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

    node_run: dict[str, Any] = {
        "status": "cancelled" if cancelled else ("failed" if error else "completed"),
        "output_text": output_text,
        "error": None if cancelled else error,
        "run_id": str(run_id) if run_id else None,
    }
    node_run.update(extraction or {})
    async with get_session() as db:
        await update_node_run(db, protocol_run_id, entry_agent_id, node_run)
    return node_run, status


#: Wall-clock budget per forced turn of a supervisor run. Multiplied by the
#: topology's own execution count (``SupervisorRoles.execution_budget``) to get
#: the run's backstop, rather than being a flat number the way a conversation's
#: is: a conversation's turn count is decided by models and has to be capped
#: from outside, while a supervisor run's is decided by the canvas and is known
#: before anything starts. Parallel workers make this generous, which is the
#: point -- it is a backstop against a wedged run, not a scheduling target.
_MAX_SUPERVISOR_TURN_DURATION = timedelta(minutes=5)

#: Appended to the supervisor's FIRST turn. It writes the brief every worker
#: receives, so it has to know that it is writing for them and not answering
#: yet -- left to infer it, a model answers the question itself and the workers
#: get a finished analysis to "help" with.
_SUPERVISOR_DISPATCH_BLOCK = (
    "Coordination:\n"
    "You are the supervisor of this run. {count} worker agents report to you: {workers}. "
    "This turn is the BRIEF you send them, not the answer -- write the instructions and the division of "
    "labour, addressed to the workers, naming who does what. They run {mode} immediately after this turn "
    "and cannot ask you anything, so anything they need must be in what you write now. Do not attempt the "
    "task yourself here. You will see everything they produce{review_clause}, and you write the final "
    "answer in a later turn."
)

#: Appended to each worker's turn.
_SUPERVISOR_WORKER_BLOCK = (
    "Coordination:\n"
    "You are one of {count} worker agents on this run, reporting to {supervisor}. Its brief is above. Do "
    "your part of it and report back with your findings -- you cannot consult the supervisor or the other "
    "workers, and this is your only turn, so a partial result reported plainly is better than a guess "
    "presented as fact. Say what you could not do and why."
)

#: Appended to the reviewer's turn. The verdict is advisory by explicit
#: decision: the reviewer reports, the supervisor decides. A binding gate is
#: what the Critic Gate strategy is for.
_SUPERVISOR_REVIEW_BLOCK = (
    "Coordination:\n"
    "You are the quality reviewer for this run. Every worker's output is above. Assess it -- what is "
    "sound, what is wrong, what is missing, what should not be relied on -- and report to {supervisor}. "
    "Your verdict is ADVISORY: you are not approving or blocking anything, and the supervisor decides "
    "what to do with it, so be specific about severity rather than issuing a pass/fail."
)

#: Appended to the supervisor's SECOND turn, together with the collected work.
_SUPERVISOR_SYNTHESIS_BLOCK = (
    "Coordination:\n"
    "Your workers have finished and their output is above. This turn is the FINAL ANSWER to the original "
    "task -- it is what this run delivers, so write it in full rather than commenting on your workers. "
    "Reconcile whatever they disagree on, and account for anything a worker could not do instead of "
    "passing the gap on silently.{review_clause}"
)

_SUPERVISOR_ADVISORY_CLAUSE = (
    " The reviewer's assessment is advisory -- weigh it and say so when you overrule it, but it does not "
    "decide the answer."
)


def _supervisor_report(display_name: str, run: dict[str, Any]) -> str:
    """One agent's turn rendered for the next agent's prompt.

    A failed or skipped worker is reported as such rather than omitted: the
    supervisor's job includes noticing that a third of the work is missing, and
    a silently shorter list of contributions is exactly how that goes unnoticed.
    """
    status = str(run.get("status") or "skipped")
    if status == "completed":
        return f"--- {display_name} ---\n{str(run.get('output_text') or '').strip() or '(no output)'}"
    if status == "cancelled":
        return f"--- {display_name} ---\n(this agent's turn was cancelled and produced nothing)"
    if status == "failed":
        reason = run.get("error") or "unknown error"
        return f"--- {display_name} ---\n(this agent failed and produced nothing: {reason})"
    return f"--- {display_name} ---\n(this agent did not run)"


async def execute_supervisor_architecture(
    protocol_run_id: uuid.UUID,
    *,
    protocol_id: uuid.UUID,
    owner_id: uuid.UUID,
    graph: dict[str, Any],
    roles: SupervisorRoles,
    user_input: str,
    workspace_id: str | None,
    evaluation_metrics: Any = None,
    parallel_workers: bool = True,
    experiment_id: uuid.UUID | None = None,
    effective_cell_label: str | None = None,
    contract_version: int = LEGACY_PROMPT_CONTRACT,
    stage_plan: Any = None,
) -> tuple[dict[str, Any], str]:
    """Run one cell as a supervisor dispatching to workers.

    Four forced stages -- supervisor brief, all workers, the optional reviewer,
    supervisor synthesis -- and the word that matters is *forced*. Peer
    Collaboration can express this shape as a graph, but consultation there is a
    function schema the model chooses to call, so a supervisor that decides two
    of its three workers suffice produces a run that isn't comparable to one
    that used all three. An experiment measures a fixed treatment; ASAREE
    dispatches every worker itself, which is what makes "all three ran" a
    property of the design rather than of the model's mood. Same guarantee the
    Sequential strategy makes, one topology up.

    Workers run concurrently unless *parallel_workers* is false. That is safe
    only because each one stages into its own ``agent:<node_id>`` workspace slot
    (Phase 4) -- on a shared lineage, two workers accepting a stage would move
    HEAD out from under each other. Serial mode exists for the case where that
    isolation is not what the user wants (workers deliberately building on each
    other's staged data) and for reproducing a run turn by turn.

    A failed worker does not abort its siblings and does not fail the run: the
    failure is reported to the supervisor in place of that worker's output, and
    the supervisor decides what the answer is without it. That is the same
    stance as the advisory reviewer -- the supervisor holds the pen. A run fails
    only when the supervisor itself fails, or when *every* worker did, since
    then there is nothing to synthesize and a confident final answer would be
    fabricated.

    Returns the supervisor's final node run (this cell's result) and the
    terminal ``ProtocolRun`` status, matching :func:`execute_conversation` so
    ``run_protocol``'s shared write-back path handles both.
    """
    nodes = {str(n.get("id")): n for n in graph.get("nodes") or [] if n.get("id")}
    messenger = AgentMessenger(
        protocol_id=protocol_id,
        protocol_run_id=protocol_run_id,
        owner_id=owner_id,
        graph=graph,
        entry_agent_id=roles.supervisor,
        workspace_id=workspace_id,
        stage_plan=stage_plan,
    )
    started_at = time.monotonic()
    deadline = _MAX_SUPERVISOR_TURN_DURATION.total_seconds() * roles.execution_budget
    limit_reached = False

    def _name(node_id: str) -> str:
        return messenger._display_name(node_id)

    worker_names = ", ".join(_name(nid) for nid in roles.workers)
    review_clause = _SUPERVISOR_ADVISORY_CLAUSE if roles.reviewer else ""

    await messenger.record(
        from_agent_id=USER_PARTICIPANT,
        to_agent_id=roles.supervisor,
        parts=[{"kind": "text", "text": user_input}],
    )

    async def _turn(
        node_id: str,
        *,
        upstream: dict[str, Any],
        block: str,
        extra: str = "",
        slot_prefix: str | None = None,
        metrics: Any = None,
    ) -> dict[str, Any]:
        """Give one agent its whole turn and return its node-run dict.

        The prompt is built by ``_build_user_input`` exactly as a pipeline node's
        is -- same seed prompt, same Dataset/Script cues, same upstream-context
        format -- with the role block appended. Reusing it is what keeps a
        supervisor run's agents reading the same prompt contract as every other
        run's, rather than inventing a second one that drifts.

        *upstream*'s keys are passed on as the authoritative sender list. They
        used to be ignored: the builder re-derived senders from the graph, so a
        brief only rendered when the supervisor happened to be a *direct*
        main-edge predecessor of the worker. Put a Critic Gate or a Script
        between them -- a shape ``resolve_supervisor_roles`` accepts, since it
        reads roles off the agent handoff graph where such a node is plumbing --
        and the brief silently vanished while ``_SUPERVISOR_WORKER_BLOCK`` still
        told the worker to carry it out. Who spoke to whom here is a fact this
        function knows outright; deriving it from topology was the bug.

        The block carries a ``[Sender]`` label and a fence and no framing
        sentence -- there used to be an ``upstream_kind`` saying whether the
        recipient should obey what is in it, and the worker path passed
        ``"brief"`` to opt out of the handoff wording. Nothing is lost:
        ``_SUPERVISOR_WORKER_BLOCK``, appended right after, already tells the
        worker its brief is above and to carry it out. Saying so twice, in the
        platform's words, was the part that had to go.
        """
        async with get_session() as db:
            await update_node_run(db, protocol_run_id, node_id, {"status": "running"})
        ambient_meta, dataset = await _node_run_context(
            graph, node_id, workspace_id, owner_id, slot_prefix=slot_prefix, stage_plan=stage_plan
        )
        prompt = _build_user_input(
            nodes[node_id],
            graph,
            upstream,
            experiment_id=experiment_id,
            effective_cell_label=effective_cell_label,
            script_bound="script_path" in ambient_meta,
            seeded_datasets=dataset.seeded,
            unsplit_dataset=dataset.unsplit_name,
            prompt_contract_version=contract_version,
            upstream_ids=list(upstream),
        )
        sections = [prompt, block]
        if extra:
            sections.insert(1, extra)
        with messenger.turn(node_id):
            output_text, error, run_id, extraction = await _run_agent_node(
                nodes[node_id],
                protocol_id=protocol_id,
                protocol_run_id=protocol_run_id,
                owner_id=owner_id,
                user_input="\n\n".join(s for s in sections if s),
                graph=graph,
                workspace_id=workspace_id,
                ambient_meta=ambient_meta,
                evaluation_metrics=metrics,
                unsplit_dataset=dataset.unsplit_name,
            )
        run: dict[str, Any] = {
            "status": "cancelled" if error == _AGENT_CANCELLED else ("failed" if error else "completed"),
            "output_text": output_text,
            "error": None if error == _AGENT_CANCELLED else error,
            "run_id": str(run_id) if run_id else None,
        }
        run.update(extraction or {})
        async with get_session() as db:
            await update_node_run(db, protocol_run_id, node_id, run)
        return run

    async def _cancelled() -> bool:
        async with get_session() as db:
            run = await get_protocol_run(db, protocol_run_id)
        return run is not None and run.cancel_requested_at is not None

    async def _skip(node_id: str, reason: str) -> dict[str, Any]:
        run = {"status": "skipped", "output_text": None, "error": reason, "run_id": None}
        async with get_session() as db:
            await update_node_run(db, protocol_run_id, node_id, run)
        return run

    # -- 1. the supervisor's brief -------------------------------------
    dispatch = await _turn(
        roles.supervisor,
        upstream={},
        block=_SUPERVISOR_DISPATCH_BLOCK.format(
            count=len(roles.workers),
            workers=worker_names,
            mode="in parallel" if parallel_workers else "one after another",
            review_clause=f", reviewed by {_name(roles.reviewer)}" if roles.reviewer else "",
        ),
    )
    if dispatch["status"] != "completed":
        # Nothing was briefed, so nothing downstream has anything to do. The
        # supervisor's own failure is the run's failure -- see the docstring.
        for node_id in (*roles.workers, *(r for r in [roles.reviewer] if r)):
            await _skip(node_id, "the supervisor did not produce a brief")
        state = "canceled" if dispatch["status"] == "cancelled" else "failed"
        messenger.set_state(state)
        await messenger.checkpoint()
        return dispatch, "cancelled" if state == "canceled" else "failed"

    brief = str(dispatch["output_text"] or "")

    # -- 2. every worker, none skipped ---------------------------------
    async def _worker(node_id: str) -> tuple[str, dict[str, Any]]:
        await messenger.record(
            from_agent_id=roles.supervisor,
            to_agent_id=node_id,
            parts=[{"kind": "text", "text": brief}],
        )
        run = await _turn(
            node_id,
            # The supervisor's brief arrives as ordinary upstream context, so a
            # worker reads it in the same format a pipeline node reads its
            # predecessor's output in. What makes it a brief rather than
            # material is _SUPERVISOR_WORKER_BLOCK right below, which tells the
            # worker to carry it out -- not a framing sentence wrapped around
            # the text itself.
            upstream={roles.supervisor: dispatch},
            block=_SUPERVISOR_WORKER_BLOCK.format(count=len(roles.workers), supervisor=_name(roles.supervisor)),
            # Its own staged lineage -- the reason the workers may run at once.
            slot_prefix=agent_slot(node_id),
        )
        await messenger.record(
            from_agent_id=node_id,
            to_agent_id=roles.supervisor,
            parts=[{"kind": "text", "text": _supervisor_report(_name(node_id), run)}],
            state="completed" if run["status"] == "completed" else str(run["status"]),
        )
        return node_id, run

    if await _cancelled():
        worker_runs = {nid: await _skip(nid, "the run was cancelled") for nid in roles.workers}
    elif parallel_workers:
        # gather, not a TaskGroup: one worker raising must not cancel its
        # siblings, and _turn already turns an agent failure into a run dict, so
        # anything that reaches here is infrastructure and is re-raised below.
        worker_runs = dict(await asyncio.gather(*(_worker(nid) for nid in roles.workers)))
    else:
        worker_runs = dict([await _worker(nid) for nid in roles.workers])

    # -- 3. the advisory reviewer --------------------------------------
    collected = "\n\n".join(_supervisor_report(_name(nid), worker_runs[nid]) for nid in roles.workers)
    review: dict[str, Any] | None = None
    if roles.reviewer is not None:
        if await _cancelled():
            review = await _skip(roles.reviewer, "the run was cancelled")
        elif time.monotonic() - started_at >= deadline:
            limit_reached = True
            review = await _skip(roles.reviewer, "this run reached its time limit before the review")
        else:
            await messenger.record(
                from_agent_id=roles.supervisor,
                to_agent_id=roles.reviewer,
                parts=[{"kind": "text", "text": collected}],
            )
            review = await _turn(
                roles.reviewer,
                # `collected` rather than upstream=worker_runs: upstream context
                # carries only the nodes that produced output, so a worker that
                # failed would be invisible here -- and "a third of this run is
                # missing" is exactly the kind of thing a reviewer is for.
                upstream={},
                extra=f"Every worker's report:\n\n{collected}",
                block=_SUPERVISOR_REVIEW_BLOCK.format(supervisor=_name(roles.supervisor)),
            )
            await messenger.record(
                from_agent_id=roles.reviewer,
                to_agent_id=roles.supervisor,
                parts=[{"kind": "text", "text": _supervisor_report(_name(roles.reviewer), review)}],
                state="completed" if review["status"] == "completed" else str(review["status"]),
            )

    # -- 4. the supervisor synthesizes ---------------------------------
    # Deliberately NOT guarded by the deadline or by a cancel check: this turn
    # is what the run delivers, and skipping it would throw away every turn
    # already paid for. A Stop click still interrupts it mid-flight through
    # Motoro's own cancel poller, which is the granularity the rest of
    # run_protocol uses too.
    gathered = collected
    if review is not None:
        gathered += "\n\n" + _supervisor_report(f"{_name(roles.reviewer or '')} (advisory review)", review)
    final = await _turn(
        roles.supervisor,
        upstream={},
        extra=f"Your workers' reports:\n\n{gathered}",
        block=_SUPERVISOR_SYNTHESIS_BLOCK.format(review_clause=review_clause if review is not None else ""),
        metrics=evaluation_metrics,
    )
    await messenger.record(
        from_agent_id=roles.supervisor,
        to_agent_id=USER_PARTICIPANT,
        parts=[{"kind": "text", "text": str(final["output_text"] or "")}],
        state="completed" if final["status"] == "completed" else str(final["status"]),
    )

    everyone_failed = bool(roles.workers) and all(worker_runs[nid]["status"] != "completed" for nid in roles.workers)
    if final["status"] == "cancelled":
        state, status = "canceled", "cancelled"
    elif final["status"] != "completed":
        state, status = "failed", "failed"
    elif everyone_failed:
        state, status = "failed", "failed"
        final = {**final, "status": "failed", "error": "every worker failed, so there was nothing to synthesize"}
        async with get_session() as db:
            await update_node_run(db, protocol_run_id, roles.supervisor, final)
    elif limit_reached:
        state, status = "limit_reached", "limit_reached"
    else:
        state, status = "completed", "completed"
    messenger.set_state(state)
    await messenger.checkpoint()
    return final, status


async def record_sequential_transcript(
    protocol_run_id: uuid.UUID,
    *,
    protocol_id: uuid.UUID,
    owner_id: uuid.UUID,
    graph: dict[str, Any],
    chain: list[str],
    node_runs: dict[str, Any],
    entry_prompt: str,
    state: str,
) -> None:
    """Render a finished sequential run as an A2A conversation document.

    A sequential handoff *is* one agent sending its work to another, so it
    belongs in the same transcript a peer conversation gets -- the user was
    reading a chain run's handoffs out of five separate node-output panels and
    reconstructing the order by hand. Writing it here costs one row update and
    the transcript panel (``ConversationTranscript.tsx``) already renders any
    ``Conversation``, so the UI is free.

    Deliberately the **message layer only**, not :func:`execute_conversation`:
    the chain is executed by ``run_protocol``'s topological walk, which is the
    entire point of the sequential strategy (every node runs, in order, with no
    agent deciding whether to hand off). Routing it through the conversation
    executor would hand that decision back to the models. Nothing here can
    influence execution -- no budget is charged, no peer is run, no
    authorization is asked.

    Written once, after the walk, rather than appended between nodes: the
    canvas already shows per-node progress live, so an incremental transcript
    would buy nothing and would put a second write inside the two different
    branches (plain node, gated pair) that complete an agent's turn.

    *chain* is ``sequential_chain_order``'s output, so a non-agent node between
    two agents (a Critic Gate, a Script) is already collapsed away -- the
    transcript shows the handoff the user drew, not the plumbing it passed
    through. *entry_prompt* is the head agent's own built ``user_input``, which
    stands in as what the user asked.
    """
    messenger = AgentMessenger(
        protocol_id=protocol_id,
        protocol_run_id=protocol_run_id,
        owner_id=owner_id,
        graph=graph,
        entry_agent_id=chain[0],
        workspace_id=None,
    )
    messenger.append(
        from_agent_id=USER_PARTICIPANT,
        to_agent_id=chain[0],
        parts=[{"kind": "text", "text": entry_prompt}],
    )

    def _outcome(node_id: str) -> tuple[str, str]:
        run = node_runs.get(node_id) or {}
        status = str(run.get("status") or "skipped")
        text = str(run.get("output_text") or "")
        if status == "completed":
            return "completed", text or "The agent finished without producing an answer."
        if status == "cancelled":
            return "canceled", "This step was cancelled."
        if status == "failed":
            return "failed", f"This step failed: {run.get('error') or 'unknown error'}"
        return "failed", "This step did not run."

    for sender, recipient in zip(chain, chain[1:], strict=False):
        # A step the walk never reached has nothing to hand on, and a
        # placeholder message for it would read as an agent that answered.
        if str((node_runs.get(sender) or {}).get("status") or "skipped") == "skipped":
            break
        run_state, text = _outcome(sender)
        messenger.append(
            from_agent_id=sender,
            to_agent_id=recipient,
            parts=[{"kind": "text", "text": text}],
            state=run_state,
        )
        # A failed or cancelled step is where the chain stopped, so nothing
        # downstream of it has a turn to record.
        if run_state != "completed":
            break
    else:
        # Every handoff was recorded, so the walk reached the last agent -- and
        # the last agent answers the user, not another agent.
        tail = chain[-1]
        tail_state, tail_text = _outcome(tail)
        messenger.append(
            from_agent_id=tail,
            to_agent_id=USER_PARTICIPANT,
            parts=[{"kind": "text", "text": tail_text}],
            state=tail_state,
        )

    messenger.set_state(state)
    await messenger.checkpoint()


__all__ = [
    "USER_PARTICIPANT",
    "AgentMessenger",
    "execute_conversation",
]
