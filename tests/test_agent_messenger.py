"""Consultation delivery: authorization, budgets, transcript, and the clock.

The property under test throughout is that **a refusal is an answer, not an
error**. Every cap and every authorization failure below asserts on the returned
:class:`AgentReply`, because the whole point is that the calling model reads the
outcome and still writes something useful. A test here that expected an
exception would be asserting the opposite of the design.

DB-free by construction: ``get_session`` and the five service calls the
messenger makes through it are stubbed, so what runs is exactly the ordering,
budget and authorization logic and nothing else.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from asaree.services import agent_messenger as am
from asaree.services import protocol_execution as pe
from asaree.services.agent_messenger import AgentMessenger
from asaree.services.deadline import Deadline, active_deadline, deadlines_paused

PROTOCOL_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
OWNER = uuid.uuid4()


def _agent(node_id: str, label: str) -> dict[str, Any]:
    return {"id": node_id, "type": "agent", "data": {"label": label, "config": {"description": label}}}


def _graph() -> dict[str, Any]:
    """Planner <-> Critic, plus an unconnected Loner."""
    return {
        "nodes": [_agent("planner", "Planner"), _agent("critic", "Critic"), _agent("loner", "Loner")],
        "edges": [{"id": "e1", "source": "planner", "target": "critic"}],
    }


class _Row:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Cut every DB call, keeping the real authorization and budget logic."""
    state: dict[str, Any] = {
        "checkpoints": [],
        "node_runs": [],
        "cancel_requested_at": None,
        "live_graph": _graph(),
        "peer_runs": [],
        # A tuple, or a callable taking the _run_agent_node kwargs and
        # returning one -- the callable form is how a test asserts on state
        # mid-consultation.
        "peer_result": ("A peer answer.", None, uuid.uuid4()),
    }

    @contextlib.asynccontextmanager
    async def _session() -> AsyncIterator[None]:
        yield None

    async def _update_conversation(_db: Any, _run_id: uuid.UUID, conversation: dict[str, Any]) -> None:
        state["checkpoints"].append(conversation)

    async def _get_protocol_run(_db: Any, _run_id: uuid.UUID) -> Any:
        return _Row(cancel_requested_at=state["cancel_requested_at"])

    async def _get_protocol(_db: Any, _protocol_id: uuid.UUID) -> Any:
        return _Row(graph=state["live_graph"])

    async def _update_node_run(_db: Any, _run_id: uuid.UUID, node_id: str, patch: dict[str, Any]) -> None:
        state["node_runs"].append((node_id, patch))

    async def _run_agent_node(node: dict[str, Any], **kwargs: Any) -> tuple[str | None, str | None, uuid.UUID | None]:
        state["peer_runs"].append((node["id"], kwargs["user_input"], kwargs["agent_messenger"]))
        result = state["peer_result"]
        resolved: tuple[str | None, str | None, uuid.UUID | None] = result(kwargs) if callable(result) else result
        return resolved

    async def _resolve_available_agents(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(am, "get_session", _session)
    monkeypatch.setattr(am, "update_conversation", _update_conversation)
    monkeypatch.setattr(am, "get_protocol_run", _get_protocol_run)
    monkeypatch.setattr(am, "get_protocol", _get_protocol)
    monkeypatch.setattr(am, "update_node_run", _update_node_run)
    monkeypatch.setattr(am, "_run_agent_node", _run_agent_node)
    monkeypatch.setattr(am, "resolve_available_agents", _resolve_available_agents)

    async def _node_run_context(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], Any]:
        return {}, SimpleNamespace(seeded_name="", unsplit_name="", data_path=None, target_column=None)

    monkeypatch.setattr(am, "_node_run_context", _node_run_context)
    return state


def _messenger(**kwargs: Any) -> AgentMessenger:
    return AgentMessenger(
        protocol_id=PROTOCOL_ID,
        protocol_run_id=RUN_ID,
        owner_id=OWNER,
        graph=_graph(),
        entry_agent_id="planner",
        **kwargs,
    )


async def _ask(messenger: AgentMessenger, to: str = "critic", text: str = "What is weak here?") -> Any:
    return await messenger.send(
        from_agent_id="planner", to_agent_id=to, parts=[{"kind": "text", "text": text}], context=None
    )


# ----------------------------------------------------------------------
# Delivery
# ----------------------------------------------------------------------


async def test_a_consultation_runs_the_peer_and_returns_its_words(stubs: dict[str, Any]) -> None:
    reply = await _ask(_messenger())
    assert reply.state == "completed"
    assert reply.text == "A peer answer."
    assert [r[0] for r in stubs["peer_runs"]] == ["critic"]
    assert stubs["peer_runs"][0][1] == "What is weak here?"


async def test_the_peer_is_handed_the_messenger_so_it_can_consult_back(stubs: dict[str, Any]) -> None:
    """Recursion is the mechanism, bounded by depth -- not something bolted on
    top of a flat exchange."""
    await _ask(messenger := _messenger())
    assert stubs["peer_runs"][0][2] is messenger


async def test_identity_and_ordering_come_from_the_runtime(stubs: dict[str, Any]) -> None:
    """Invariant 2 -- the model supplies content only."""
    messenger = _messenger()
    await _ask(messenger)
    await _ask(messenger, text="And now?")
    messages = messenger.conversation["messages"]
    assert [m["sequence"] for m in messages] == [1, 2, 3, 4]
    assert len({m["message_id"] for m in messages}) == 4
    assert [(m["from_agent_id"], m["to_agent_id"]) for m in messages] == [
        ("planner", "critic"),
        ("critic", "planner"),
        ("planner", "critic"),
        ("critic", "planner"),
    ]


async def test_the_transcript_is_checkpointed_around_the_peer_turn(stubs: dict[str, Any]) -> None:
    """A worker retry has to be able to see how far the conversation got."""

    def _result(kwargs: Any) -> tuple[str, None, uuid.UUID]:
        # Mid-flight: the question must already be durable, the reply must not.
        assert len(stubs["checkpoints"][-1]["messages"]) == 1
        return ("A peer answer.", None, uuid.uuid4())

    stubs["peer_result"] = _result
    await _ask(_messenger())
    assert len(stubs["checkpoints"][-1]["messages"]) == 2


async def test_a_consulted_peer_shows_on_the_canvas_as_a_node_that_ran(stubs: dict[str, Any]) -> None:
    await _ask(_messenger())
    assert [n for n, _ in stubs["node_runs"]] == ["critic", "critic"]
    assert stubs["node_runs"][0][1]["status"] == "running"
    assert stubs["node_runs"][1][1]["status"] == "completed"


async def test_a_peer_that_fails_is_a_reply_not_an_exception(stubs: dict[str, Any]) -> None:
    stubs["peer_result"] = (None, "boom", uuid.uuid4())
    reply = await _ask(_messenger())
    assert reply.state == "failed"
    assert reply.error == "boom"
    assert "boom" in reply.text


async def test_a_cancelled_peer_is_canceled_not_failed(stubs: dict[str, Any]) -> None:
    """A Stop is not an error, and reporting it as one would put a red node on
    a canvas the user themselves stopped."""
    stubs["peer_result"] = (None, pe._AGENT_CANCELLED, uuid.uuid4())
    reply = await _ask(_messenger())
    assert reply.state == "canceled"
    assert reply.error is None


async def test_a_silent_peer_still_says_something(stubs: dict[str, Any]) -> None:
    """An empty function result reads to a model as a malfunction rather than
    as an answer to work around."""
    stubs["peer_result"] = ("   ", None, uuid.uuid4())
    reply = await _ask(_messenger())
    assert reply.state == "completed"
    assert reply.text


# ----------------------------------------------------------------------
# Briefing -- what a turn gets to read
# ----------------------------------------------------------------------


async def test_the_first_consultation_carries_only_the_question(stubs: dict[str, Any]) -> None:
    """Nothing has been said yet, so there is nothing to brief -- and a peer
    asked once reads exactly what it read before this existed."""
    await _ask(_messenger())
    assert stubs["peer_runs"][0][1] == "What is weak here?"


async def test_a_peer_asked_twice_is_reminded_of_its_own_earlier_turn(stubs: dict[str, Any]) -> None:
    """Peer memory. Each turn is still a fresh AgentRun, so without this the
    second turn would have no idea it had already spoken."""
    stubs["peer_result"] = ("The sample size is too small.", None, uuid.uuid4())
    messenger = _messenger()
    await _ask(messenger)
    await _ask(messenger, text="How would you fix it?")

    second = stubs["peer_runs"][1][1]
    assert "The sample size is too small." in second
    assert "What is weak here?" in second
    # The live question stays last and is not also quoted as history.
    assert second.count("How would you fix it?") == 1
    assert second.endswith("How would you fix it?")


async def test_an_agent_sees_what_other_agents_have_already_found(stubs: dict[str, Any]) -> None:
    """Shared context: Loner's turn must carry Critic's finding, or the two
    agents are answering in isolation rather than building on each other."""
    stubs["live_graph"]["edges"].append({"id": "e2", "source": "planner", "target": "loner"})
    stubs["peer_result"] = ("The outcome is bimodal.", None, uuid.uuid4())
    messenger = _messenger()
    await _ask(messenger)
    await _ask(messenger, to="loner", text="Design a test.")

    loner_input = stubs["peer_runs"][1][1]
    assert "The outcome is bimodal." in loner_input
    assert "Critic" in loner_input
    # And it is told which participant it is, since the transcript names it in
    # the third person.
    assert "You are Loner" in loner_input


async def test_a_briefing_names_participants_the_way_the_canvas_does(stubs: dict[str, Any]) -> None:
    messenger = _messenger()
    messenger.append(from_agent_id=am.USER_PARTICIPANT, to_agent_id="planner", parts=[{"kind": "text", "text": "Go."}])
    await _ask(messenger)
    briefed = stubs["peer_runs"][0][1]
    assert "The user -> Planner" in briefed


async def test_a_refused_turn_is_not_presented_as_an_answer(stubs: dict[str, Any]) -> None:
    """It stays in the briefing -- it is part of what happened -- but a model
    reading it must not mistake a refusal for a colleague's finding."""
    messenger = _messenger()
    await _ask(messenger, to="loner", text="Help?")  # unconnected -> refused
    await _ask(messenger)
    briefed = stubs["peer_runs"][0][1]
    assert "(refused)" in briefed


async def test_a_long_earlier_turn_is_truncated_and_says_so(
    stubs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eight full analyses would crowd out the question actually being asked."""
    monkeypatch.setattr(am, "_MAX_BRIEFING_CHARS_PER_MESSAGE", 20)
    stubs["peer_result"] = ("x" * 500, None, uuid.uuid4())
    messenger = _messenger()
    await _ask(messenger)
    await _ask(messenger, text="And now?")
    briefed = stubs["peer_runs"][1][1]
    assert "[...truncated]" in briefed
    assert "x" * 500 not in briefed


# ----------------------------------------------------------------------
# Authorization
# ----------------------------------------------------------------------


async def test_an_unconnected_agent_cannot_be_consulted(stubs: dict[str, Any]) -> None:
    """Invariant 3 -- and it is a rejection, so the caller can still answer."""
    reply = await _ask(_messenger(), to="loner")
    assert reply.state == "rejected"
    assert "not connected" in reply.text
    assert stubs["peer_runs"] == []


async def test_pulling_the_edge_mid_run_stops_the_next_consultation(stubs: dict[str, Any]) -> None:
    """Reachability is live: the run's cards came from a revision, but delivery
    is re-authorized against the draft graph every single time."""
    messenger = _messenger()
    assert (await _ask(messenger)).state == "completed"
    stubs["live_graph"] = {"nodes": _graph()["nodes"], "edges": []}
    assert (await _ask(messenger)).state == "rejected"
    assert len(stubs["peer_runs"]) == 1


async def test_a_refused_consultation_is_still_in_the_transcript(stubs: dict[str, Any]) -> None:
    """Invariant 10 -- a silently dropped question is the failure this makes
    debuggable."""
    messenger = _messenger()
    await _ask(messenger, to="loner")
    messages = messenger.conversation["messages"]
    assert [m["to_agent_id"] for m in messages] == ["loner", "planner"]
    assert messages[1]["state"] == "rejected"


async def test_a_stop_between_consultations_prevents_further_ones(stubs: dict[str, Any]) -> None:
    """Invariant 8."""
    from datetime import UTC, datetime

    stubs["cancel_requested_at"] = datetime.now(UTC)
    reply = await _ask(_messenger())
    assert reply.state == "rejected"
    assert "cancelled" in reply.text
    assert stubs["peer_runs"] == []


# ----------------------------------------------------------------------
# Budgets
# ----------------------------------------------------------------------


async def test_the_execution_budget_is_spent_not_bypassed(
    stubs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(am, "_MAX_PEER_EXECUTIONS", 2)
    messenger = _messenger()
    assert (await _ask(messenger)).state == "completed"
    assert (await _ask(messenger)).state == "completed"
    exhausted = await _ask(messenger)
    assert exhausted.state == "rejected"
    assert "consultations" in exhausted.text
    assert len(stubs["peer_runs"]) == 2
    assert messenger.limit_reached is True


async def test_depth_is_capped_and_the_cap_is_a_reply(stubs: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """The consulted peer consults back *from inside its own turn*; at the cap
    it gets prose telling it to answer instead. Invariants 6 and 7 together."""
    monkeypatch.setattr(am, "_MAX_CONSULT_DEPTH", 1)
    nested_replies: list[Any] = []

    async def _run_agent_node(node: dict[str, Any], **kwargs: Any) -> tuple[str, None, uuid.UUID]:
        messenger: AgentMessenger = kwargs["agent_messenger"]
        nested_replies.append(
            await messenger.send(
                from_agent_id="critic",
                to_agent_id="planner",
                parts=[{"kind": "text", "text": "Clarify?"}],
                context=None,
            )
        )
        return ("A peer answer.", None, uuid.uuid4())

    monkeypatch.setattr(am, "_run_agent_node", _run_agent_node)
    messenger = _messenger()
    outer = await _ask(messenger)

    assert outer.state == "completed"
    assert [r.state for r in nested_replies] == ["rejected"]
    assert "nested" in nested_replies[0].text
    assert messenger.limit_reached is True


async def test_depth_is_released_when_a_consultation_returns(
    stubs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cap on how deep, not on how many in a row -- two sequential
    consultations from the entry agent are both depth 1."""
    monkeypatch.setattr(am, "_MAX_CONSULT_DEPTH", 1)
    messenger = _messenger()
    assert (await _ask(messenger)).state == "completed"
    assert (await _ask(messenger)).state == "completed"


async def test_the_conversation_wall_clock_is_the_backstop(
    stubs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Peer time is given back to individual agents but never to this cap --
    otherwise nothing would bound the total."""
    from datetime import timedelta

    monkeypatch.setattr(am, "_MAX_CONVERSATION_DURATION", timedelta(seconds=0))
    reply = await _ask(_messenger())
    assert reply.state == "rejected"
    assert "seconds" in reply.text
    assert stubs["peer_runs"] == []


# ----------------------------------------------------------------------
# Timeout accounting
# ----------------------------------------------------------------------


async def test_a_paused_deadline_does_not_expire_while_it_waits() -> None:
    deadline = Deadline(0.05)
    deadline.pause()
    await asyncio.sleep(0.15)
    assert deadline.expired() is False
    deadline.resume()
    # The paused span was given back, so what was left before the pause is
    # still left after it.
    assert 0 < deadline.remaining() <= 0.05
    assert deadline.expired() is False


async def test_every_caller_in_the_chain_stops_ticking() -> None:
    """A depth-2 consultation blocks its own caller and the entry agent for the
    same span, so neither is charged for it."""
    entry, middle = Deadline(0.05), Deadline(0.05)
    with active_deadline(entry), active_deadline(middle), deadlines_paused():
        await asyncio.sleep(0.15)
    assert (entry.expired(), middle.expired()) == (False, False)


async def test_a_finished_frame_is_no_longer_paused() -> None:
    """Leaving ``active_deadline`` takes the frame out of the chain, so a later
    consultation cannot freeze a run that already ended."""
    with active_deadline(Deadline(10)):
        with active_deadline(inner := Deadline(0.05)):
            pass
        with deadlines_paused():
            await asyncio.sleep(0.15)
    assert inner.expired() is True


async def test_nested_pauses_only_restart_the_clock_once() -> None:
    deadline = Deadline(0.05)
    with active_deadline(deadline), deadlines_paused():
        with deadlines_paused():
            await asyncio.sleep(0.1)
        assert deadline.expired() is False
        await asyncio.sleep(0.1)
    assert deadline.expired() is False


async def test_peer_time_does_not_burn_the_callers_own_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """The decision this feature turns on: an agent is charged for its own
    thinking, never for waiting on a peer. A consultation longer than the
    caller's whole allowance must not time the caller out."""

    async def _never_cancels(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.Event().wait()

    async def _execute_run(**_kwargs: Any) -> None:
        # Stand in for the engine: think briefly, block on a peer for longer
        # than the entire budget, then finish. Post-hoc repayment would not
        # save this run -- the deadline would already have fired mid-wait --
        # which is why the clock stops instead.
        await asyncio.sleep(0.05)
        with deadlines_paused():
            await asyncio.sleep(0.3)
        await asyncio.sleep(0.05)

    monkeypatch.setattr(pe, "_poll_cancel_flag", _never_cancels)
    monkeypatch.setattr(pe, "execute_run", _execute_run)
    monkeypatch.setattr(pe, "get_registry", lambda: None)

    await pe._execute_run_cancellable(run_id=uuid.uuid4(), protocol_run_id=RUN_ID, available_tools=[], timeout=0.25)


async def test_an_agent_that_overruns_on_its_own_still_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half: the deadline is still a real limit when nothing extends
    it, which is every pipeline run."""

    async def _never_cancels(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.Event().wait()

    async def _execute_run(**_kwargs: Any) -> None:
        await asyncio.sleep(5)

    monkeypatch.setattr(pe, "_poll_cancel_flag", _never_cancels)
    monkeypatch.setattr(pe, "execute_run", _execute_run)
    monkeypatch.setattr(pe, "get_registry", lambda: None)

    with pytest.raises(TimeoutError):
        await pe._execute_run_cancellable(run_id=uuid.uuid4(), protocol_run_id=RUN_ID, available_tools=[], timeout=0.1)
