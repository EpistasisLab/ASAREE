"""Forced dispatch: every agent in a supervisor topology runs, exactly once.

The property under test throughout is that **no turn is the model's choice**.
Peer Collaboration can draw this same graph, but there consultation is a
function schema a model may decline to call, so "all three workers contributed"
is a property of the model's mood rather than of the design. Here ASAREE
dispatches each turn itself, and the assertions below are about that: the run
order, the fact that a failure doesn't shorten the roster, and the fact that
parallel and serial dispatch produce the same set of turns.

DB-free by construction, the same way ``test_agent_messenger`` is: every
service call the executor makes through ``get_session`` is stubbed, so what
runs is the orchestration and nothing else.
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

PROTOCOL_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
OWNER = uuid.uuid4()


def _agent(node_id: str, label: str) -> dict[str, Any]:
    return {"id": node_id, "type": "agent", "data": {"label": label, "config": {"description": label}}}


def _graph(*, reviewer: bool = True, workers: int = 3) -> dict[str, Any]:
    """The user's target topology: one supervisor, three workers, one QC agent
    that sees every worker and reports back to the supervisor."""
    worker_ids = [f"w{i}" for i in range(1, workers + 1)]
    nodes = [_agent("sup", "Supervisor"), *(_agent(nid, nid.upper()) for nid in worker_ids)]
    edges = [{"id": f"e-sup-{nid}", "source": "sup", "target": nid} for nid in worker_ids]
    if reviewer:
        nodes.append(_agent("qc", "Reviewer"))
        edges += [{"id": f"e-{nid}-qc", "source": nid, "target": "qc"} for nid in worker_ids]
        edges.append({"id": "e-qc-sup", "source": "qc", "target": "sup"})
    return {"nodes": nodes, "edges": edges}


class _Row:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "checkpoints": [],
        "node_runs": [],
        "cancel_requested_at": None,
        # node_id -> (output_text, error). A node id absent from this answers
        # with a generic completion; a callable is invoked with the
        # _run_agent_node kwargs, which is how a test asserts mid-run.
        "answers": {},
        "turns": [],
        "run_contexts": [],
        "concurrent": 0,
        "max_concurrent": 0,
    }

    @contextlib.asynccontextmanager
    async def _session() -> AsyncIterator[None]:
        yield None

    async def _update_conversation(_db: Any, _run_id: uuid.UUID, conversation: dict[str, Any]) -> None:
        state["checkpoints"].append(conversation)

    async def _get_protocol_run(_db: Any, _run_id: uuid.UUID) -> Any:
        return _Row(cancel_requested_at=state["cancel_requested_at"])

    async def _get_protocol(_db: Any, _protocol_id: uuid.UUID) -> Any:
        return _Row(graph=_graph())

    async def _update_node_run(_db: Any, _run_id: uuid.UUID, node_id: str, patch: dict[str, Any]) -> None:
        state["node_runs"].append((node_id, patch))

    async def _run_agent_node(node: dict[str, Any], **kwargs: Any) -> tuple[str | None, str | None, uuid.UUID | None]:
        node_id = node["id"]
        state["turns"].append((node_id, kwargs["user_input"]))
        state["concurrent"] += 1
        state["max_concurrent"] = max(state["max_concurrent"], state["concurrent"])
        try:
            # A real turn awaits; without one, "parallel" would be
            # indistinguishable from serial because nothing ever yields.
            await asyncio.sleep(0)
            answer = state["answers"].get(node_id, (f"{node_id} did its part.", None))
            output, error = answer(kwargs) if callable(answer) else answer
        finally:
            state["concurrent"] -= 1
        return output, error, uuid.uuid4() if error is None else None

    async def _node_run_context(*_args: Any, **kwargs: Any) -> tuple[dict[str, Any], Any]:
        state["run_contexts"].append(kwargs)
        return {}, SimpleNamespace(seeded=(), unsplit_name="", data_path=None, target_column=None)

    async def _resolve_available_agents(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("a supervisor run must not offer anyone a peer-consultation schema")

    monkeypatch.setattr(am, "get_session", _session)
    monkeypatch.setattr(am, "update_conversation", _update_conversation)
    monkeypatch.setattr(am, "get_protocol_run", _get_protocol_run)
    monkeypatch.setattr(am, "get_protocol", _get_protocol)
    monkeypatch.setattr(am, "update_node_run", _update_node_run)
    monkeypatch.setattr(am, "_run_agent_node", _run_agent_node)
    monkeypatch.setattr(am, "_node_run_context", _node_run_context)
    monkeypatch.setattr(am, "resolve_available_agents", _resolve_available_agents)
    return state


async def _run(stubs: dict[str, Any], graph: dict[str, Any] | None = None, **kwargs: Any) -> tuple[dict, str]:
    graph = graph if graph is not None else _graph()
    return await am.execute_supervisor_architecture(
        RUN_ID,
        protocol_id=PROTOCOL_ID,
        owner_id=OWNER,
        graph=graph,
        roles=pe.resolve_supervisor_roles(graph),
        user_input="Assess this cohort.",
        workspace_id=None,
        **kwargs,
    )


def _ran(stubs: dict[str, Any]) -> list[str]:
    return [node_id for node_id, _prompt in stubs["turns"]]


def _prompt_for(stubs: dict[str, Any], node_id: str, *, occurrence: int = 0) -> str:
    return [prompt for nid, prompt in stubs["turns"] if nid == node_id][occurrence]


# ----------------------------------------------------------------------
# Forced dispatch
# ----------------------------------------------------------------------


async def test_the_target_topology_runs_every_agent(stubs: dict[str, Any]) -> None:
    """The exact shape the user asked for: 1 supervisor, 3 workers, 1 QC. Five
    agents, six turns, and the supervisor's second turn is the result."""
    final, status = await _run(stubs)
    assert status == "completed"
    assert sorted(_ran(stubs)) == ["qc", "sup", "sup", "w1", "w2", "w3"]
    # The supervisor brackets the run; the reviewer sees the workers, never the
    # other way round.
    assert _ran(stubs)[0] == "sup"
    assert _ran(stubs)[-1] == "sup"
    assert _ran(stubs).index("qc") > max(_ran(stubs).index(w) for w in ("w1", "w2", "w3"))
    assert final["status"] == "completed"
    assert final["output_text"] == "sup did its part."


async def test_the_budget_is_the_turn_count(stubs: dict[str, Any]) -> None:
    """`execution_budget` is a description of the run, not a cap on it -- so the
    number of turns actually dispatched has to equal it."""
    graph = _graph()
    roles = pe.resolve_supervisor_roles(graph)
    await _run(stubs, graph)
    assert len(stubs["turns"]) == roles.execution_budget


async def test_the_brief_reaches_every_worker(stubs: dict[str, Any]) -> None:
    stubs["answers"]["sup"] = ("Split the cohort three ways.", None)
    await _run(stubs)
    for worker in ("w1", "w2", "w3"):
        assert "Split the cohort three ways." in _prompt_for(stubs, worker)


async def test_each_agent_is_told_its_role(stubs: dict[str, Any]) -> None:
    """The stages are forced, so the prompts have to say so -- a supervisor left
    to infer that its first turn is a brief writes the answer itself instead."""
    await _run(stubs)
    assert "You are the supervisor of this run" in _prompt_for(stubs, "sup")
    assert "3 worker agents report to you" in _prompt_for(stubs, "sup")
    assert "in parallel" in _prompt_for(stubs, "sup")
    assert "You are one of 3 worker agents" in _prompt_for(stubs, "w2")
    assert "cannot consult the supervisor or the other workers" in _prompt_for(stubs, "w2")
    assert "You are the quality reviewer" in _prompt_for(stubs, "qc")
    assert "ADVISORY" in _prompt_for(stubs, "qc")
    assert "FINAL ANSWER" in _prompt_for(stubs, "sup", occurrence=1)


async def test_the_review_is_advisory_in_the_synthesis_prompt(stubs: dict[str, Any]) -> None:
    """The user's explicit decision: QC reports, the supervisor decides. A
    binding gate is what the Critic Gate strategy is for."""
    await _run(stubs)
    synthesis = _prompt_for(stubs, "sup", occurrence=1)
    assert "advisory -- weigh it and say so when you overrule it" in synthesis
    # With no reviewer there is nothing advisory to mention.
    stubs["turns"].clear()
    await _run(stubs, _graph(reviewer=False))
    assert "advisory" not in _prompt_for(stubs, "sup", occurrence=1)


async def test_the_supervisor_synthesizes_from_what_the_workers_said(stubs: dict[str, Any]) -> None:
    stubs["answers"].update(
        {
            "w1": ("Cohort A is clean.", None),
            "w2": ("Cohort B has 12 missing labels.", None),
            "w3": ("Cohort C looks bimodal.", None),
            "qc": ("W3's bimodality claim needs a test.", None),
        }
    )
    await _run(stubs)
    synthesis = _prompt_for(stubs, "sup", occurrence=1)
    for said in ("Cohort A is clean.", "Cohort B has 12 missing labels.", "Cohort C looks bimodal."):
        assert said in synthesis
    assert "W3's bimodality claim needs a test." in synthesis
    assert "(advisory review)" in synthesis


async def test_a_run_with_no_reviewer_skips_only_the_review(stubs: dict[str, Any]) -> None:
    _final, status = await _run(stubs, _graph(reviewer=False))
    assert status == "completed"
    assert sorted(_ran(stubs)) == ["sup", "sup", "w1", "w2", "w3"]


async def test_nobody_is_offered_a_peer_schema(stubs: dict[str, Any]) -> None:
    """`resolve_available_agents` raises in the fixture. Passing peers here
    would give the supervisor an `ask_<worker>` function *alongside* the forced
    dispatch, so a worker could run twice or the supervisor could route around
    the stages entirely."""
    _final, status = await _run(stubs)
    assert status == "completed"


# ----------------------------------------------------------------------
# Parallel vs. serial
# ----------------------------------------------------------------------


async def test_workers_run_at_once_by_default(stubs: dict[str, Any]) -> None:
    await _run(stubs)
    assert stubs["max_concurrent"] == 3


async def test_serial_mode_runs_one_worker_at_a_time(stubs: dict[str, Any]) -> None:
    await _run(stubs, parallel_workers=False)
    assert stubs["max_concurrent"] == 1
    assert "one after another" in _prompt_for(stubs, "sup")


async def test_parallel_and_serial_dispatch_the_same_turns(stubs: dict[str, Any]) -> None:
    """Concurrency is a scheduling decision, not a change of design -- the same
    agents must run the same number of times either way. Only the *order* of
    the worker turns is allowed to differ, which is why this compares sets."""
    parallel_final, parallel_status = await _run(stubs)
    parallel_turns = sorted(_ran(stubs))
    parallel_messages = sorted(
        (m["from_agent_id"], m["to_agent_id"]) for m in stubs["checkpoints"][-1]["messages"]
    )

    stubs["turns"].clear()
    stubs["checkpoints"].clear()
    serial_final, serial_status = await _run(stubs, parallel_workers=False)

    assert parallel_turns == sorted(_ran(stubs))
    assert parallel_status == serial_status == "completed"
    assert parallel_final["status"] == serial_final["status"]
    assert parallel_messages == sorted(
        (m["from_agent_id"], m["to_agent_id"]) for m in stubs["checkpoints"][-1]["messages"]
    )


async def test_each_worker_stages_into_its_own_slot(stubs: dict[str, Any]) -> None:
    """The reason parallel dispatch is safe at all: on one shared lineage, two
    workers accepting a stage would move HEAD out from under each other."""
    await _run(stubs)
    prefixes = {
        ctx.get("slot_prefix") for ctx in stubs["run_contexts"] if ctx.get("slot_prefix") is not None
    }
    assert prefixes == {"agent:w1", "agent:w2", "agent:w3"}
    # The supervisor and the reviewer read, they don't stage -- they get the
    # workspace's default resolution, the same as any pipeline node.
    assert sum(1 for ctx in stubs["run_contexts"] if ctx.get("slot_prefix") is None) == 3


# ----------------------------------------------------------------------
# Failure: the supervisor holds the pen
# ----------------------------------------------------------------------


async def test_a_failed_worker_does_not_stop_the_others(stubs: dict[str, Any]) -> None:
    stubs["answers"]["w2"] = (None, "the tool exploded")
    _final, status = await _run(stubs)
    assert status == "completed"
    assert sorted(_ran(stubs)) == ["qc", "sup", "sup", "w1", "w2", "w3"]


async def test_the_supervisor_is_told_which_worker_failed(stubs: dict[str, Any]) -> None:
    """A silently shorter list of contributions is exactly how a third of the
    work goes missing unnoticed."""
    stubs["answers"]["w2"] = (None, "the tool exploded")
    await _run(stubs)
    synthesis = _prompt_for(stubs, "sup", occurrence=1)
    assert "--- W2 ---" in synthesis
    assert "this agent failed and produced nothing: the tool exploded" in synthesis
    # And the reviewer sees the gap too, since assessing the run includes it.
    assert "--- W2 ---" in _prompt_for(stubs, "qc")
    assert "the tool exploded" in _prompt_for(stubs, "qc")


async def test_one_failed_worker_does_not_fail_the_run(stubs: dict[str, Any]) -> None:
    stubs["answers"]["w1"] = (None, "boom")
    final, status = await _run(stubs)
    assert status == "completed"
    assert final["status"] == "completed"


async def test_every_worker_failing_fails_the_run(stubs: dict[str, Any]) -> None:
    """There is nothing left to synthesize, so a confident final answer would
    be fabricated -- the one case where the supervisor's own success isn't
    enough."""
    for worker in ("w1", "w2", "w3"):
        stubs["answers"][worker] = (None, "boom")
    final, status = await _run(stubs)
    assert status == "failed"
    assert final["status"] == "failed"
    assert "nothing to synthesize" in str(final["error"])
    # Recorded on the node run too, not just returned.
    assert ("sup", final) in stubs["node_runs"]


async def test_a_failed_supervisor_skips_everyone(stubs: dict[str, Any]) -> None:
    """No brief means nobody downstream has anything to do, and a worker
    inventing its own task would be running a different experiment."""
    stubs["answers"]["sup"] = (None, "no credentials")
    final, status = await _run(stubs)
    assert status == "failed"
    assert _ran(stubs) == ["sup"]
    skipped = {node_id for node_id, patch in stubs["node_runs"] if patch.get("status") == "skipped"}
    assert skipped == {"w1", "w2", "w3", "qc"}
    assert final["error"] == "no credentials"


async def test_a_cancelled_supervisor_cancels_the_run(stubs: dict[str, Any]) -> None:
    stubs["answers"]["sup"] = (None, am._AGENT_CANCELLED)
    _final, status = await _run(stubs)
    assert status == "cancelled"
    assert stubs["checkpoints"][-1]["state"] == "canceled"


async def test_a_stop_before_the_workers_skips_them(stubs: dict[str, Any]) -> None:
    def _stop(_kwargs: Any) -> tuple[str, None]:
        stubs["cancel_requested_at"] = "now"
        return "Briefed.", None

    stubs["answers"]["sup"] = _stop
    _final, _status = await _run(stubs)
    skipped = {node_id for node_id, patch in stubs["node_runs"] if patch.get("status") == "skipped"}
    assert skipped == {"w1", "w2", "w3", "qc"}
    # The synthesis turn still runs: it is what the run delivers, and skipping
    # it would throw away every turn already paid for.
    assert _ran(stubs) == ["sup", "sup"]


async def test_the_synthesis_turn_survives_the_deadline(
    stubs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wall clock is a backstop against a wedged run, and it gates the
    review -- never the final answer."""
    monkeypatch.setattr(am, "_MAX_SUPERVISOR_TURN_DURATION", am.timedelta(0))
    _final, status = await _run(stubs)
    assert status == "limit_reached"
    assert "qc" not in _ran(stubs)
    assert _ran(stubs)[-1] == "sup"
    assert stubs["checkpoints"][-1]["state"] == "limit_reached"


# ----------------------------------------------------------------------
# The transcript
# ----------------------------------------------------------------------


async def test_the_run_reads_as_a_conversation(stubs: dict[str, Any]) -> None:
    """Same transcript a peer conversation gets, so the canvas overlay and the
    Runs tab need no second renderer -- the user was otherwise reading six node
    output panels and reconstructing who was answering whom."""
    await _run(stubs)
    conversation = stubs["checkpoints"][-1]
    assert conversation["state"] == "completed"
    assert conversation["entry_agent_id"] == "sup"
    pairs = [(m["from_agent_id"], m["to_agent_id"]) for m in conversation["messages"]]
    assert pairs[0] == (am.USER_PARTICIPANT, "sup")
    assert pairs[-1] == ("sup", am.USER_PARTICIPANT)
    for worker in ("w1", "w2", "w3"):
        assert ("sup", worker) in pairs
        assert (worker, "sup") in pairs
    assert ("sup", "qc") in pairs
    assert ("qc", "sup") in pairs
    # Sequence numbers are handed out under the messenger's lock, so parallel
    # workers can't collide on one.
    sequences = [m["sequence"] for m in conversation["messages"]]
    assert sequences == list(range(1, len(sequences) + 1))


async def test_a_failed_workers_message_is_marked_failed(stubs: dict[str, Any]) -> None:
    stubs["answers"]["w2"] = (None, "boom")
    await _run(stubs)
    reported = [
        m for m in stubs["checkpoints"][-1]["messages"] if m["from_agent_id"] == "w2" and m["to_agent_id"] == "sup"
    ]
    assert [m["state"] for m in reported] == ["failed"]


async def test_concurrent_records_do_not_lose_a_message(stubs: dict[str, Any]) -> None:
    """`AgentMessenger` is single-turn-at-a-time everywhere else, so this is the
    one place its append path is actually contended."""
    messenger = am.AgentMessenger(
        protocol_id=PROTOCOL_ID,
        protocol_run_id=RUN_ID,
        owner_id=OWNER,
        graph=_graph(),
        entry_agent_id="sup",
    )
    await asyncio.gather(
        *(
            messenger.record(from_agent_id="sup", to_agent_id=f"w{i % 3 + 1}", parts=[{"kind": "text", "text": "go"}])
            for i in range(30)
        )
    )
    sequences = [m["sequence"] for m in messenger.conversation["messages"]]
    assert sequences == list(range(1, 31))
