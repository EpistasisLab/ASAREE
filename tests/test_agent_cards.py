"""Peer topology and AgentCard resolution.

Two properties carry most of the weight here. *Capability is snapshotted,
reachability is live*: a card describes an agent as the run's graph configures
it, while ``_can_deliver_communication`` is re-asked of the draft graph on every
consultation. And *a card is derived, never stored*: every assertion below reads
the graph, because that is the only place the answer lives.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from asaree.services import protocol_execution as pe
from asaree.services.agent_cards import AgentCard, build_agent_card

OWNER = uuid.uuid4()


def _agent(node_id: str, *, label: str = "", config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": node_id, "type": "agent", "data": {"label": label, "config": config or {}}}


def _edge(source: str, target: str, handle: str | None = None) -> dict[str, Any]:
    edge: dict[str, Any] = {"id": f"{source}-{target}", "source": source, "target": target}
    if handle is not None:
        edge["targetHandle"] = handle
    return edge


def _pair_graph() -> dict[str, Any]:
    """Planner <-> Critic, joined by one plain edge."""
    return {
        "nodes": [
            _agent("planner", label="Planner", config={"description": "Breaks a goal into ordered steps."}),
            _agent("critic", label="Critic", config={"description": "Names the weakest claim in a draft."}),
        ],
        "edges": [_edge("planner", "critic")],
    }


# ----------------------------------------------------------------------
# Topology
# ----------------------------------------------------------------------


def test_a_plain_agent_edge_is_a_peer_edge_in_both_directions() -> None:
    """The stored source/target records how the user drew the edge, not who
    may speak."""
    graph = _pair_graph()
    assert pe._connected_agent_ids(graph, "planner") == ["critic"]
    assert pe._connected_agent_ids(graph, "critic") == ["planner"]


def test_connector_edges_are_configuration_not_topology() -> None:
    graph = {
        "nodes": [_agent("planner"), _agent("critic")],
        # Nonsensical wiring on purpose: even if a connector-typed edge joined
        # two Agent nodes, a connector declares capability, not who to talk to.
        "edges": [_edge("planner", "critic", "tool")],
    }
    assert pe._connected_agent_ids(graph, "planner") == []


def test_non_agent_nodes_are_never_peers() -> None:
    """Invariant 4 — a Critic Gate edge keeps its pipeline meaning, and an LLM
    node is not addressable however it is wired."""
    graph = {
        "nodes": [
            _agent("planner"),
            {"id": "gate", "type": "critic_gate", "data": {"config": {}}},
            {"id": "llm", "type": "model_anthropic", "data": {"config": {}}},
        ],
        "edges": [_edge("planner", "gate"), _edge("llm", "planner")],
    }
    assert pe._connected_agent_ids(graph, "planner") == []


def test_peers_are_listed_once_in_wiring_order() -> None:
    graph = {
        "nodes": [_agent("a"), _agent("b"), _agent("c")],
        "edges": [_edge("a", "c"), _edge("b", "a"), {"id": "dup", "source": "c", "target": "a"}],
    }
    assert pe._connected_agent_ids(graph, "a") == ["c", "b"]


def test_an_agent_is_never_its_own_peer() -> None:
    graph = {"nodes": [_agent("a")], "edges": [_edge("a", "a")]}
    assert pe._connected_agent_ids(graph, "a") == []
    assert pe._can_deliver_communication(graph, "a", "a") is False


def test_delivery_is_authorized_by_the_graph_it_is_asked_of() -> None:
    """Reachability is live: unplugging the edge on the canvas stops the next
    consultation, even though the run's cards came from a revision."""
    connected = _pair_graph()
    assert pe._can_deliver_communication(connected, "planner", "critic") is True
    assert pe._can_deliver_communication(connected, "critic", "planner") is True

    unplugged = {"nodes": connected["nodes"], "edges": []}
    assert pe._can_deliver_communication(unplugged, "planner", "critic") is False


def test_an_unconnected_third_agent_is_not_reachable() -> None:
    """Invariant 3 — the two clusters on a canvas stay separate."""
    graph = _pair_graph()
    graph["nodes"].append(_agent("loner", label="Loner"))
    assert pe._connected_agent_ids(graph, "planner") == ["critic"]
    assert pe._can_deliver_communication(graph, "planner", "loner") is False


# ----------------------------------------------------------------------
# The card
# ----------------------------------------------------------------------


def test_description_falls_back_to_goal_then_to_a_default() -> None:
    """The description becomes the consultation function's description, which
    is what a peer's model reads to decide. An empty one is worse than a
    generic one."""
    assert (
        build_agent_card(
            node_id="n1", label="Critic", description="", goal="Find the weakest claim.", skills=[], model=None
        ).description
        == "Find the weakest claim."
    )
    assert (
        build_agent_card(node_id="n1", label="Critic", description="", goal="", skills=[], model=None).description
        == "An agent named Critic."
    )


def test_an_unlabeled_agent_falls_back_to_its_node_id() -> None:
    assert build_agent_card(node_id="n1", label=None, description="d", goal="", skills=[], model=None).name == "n1"


def test_the_card_serializes_to_the_shape_core_consumes() -> None:
    card = build_agent_card(
        node_id="n1",
        label="Critic",
        description="Names the weakest claim.",
        goal="",
        skills=[{"name": "Fact Check", "description": "Verifies claims.", "body": "ignored", "files": {}}],
        model="claude-sonnet-5",
        metadata={"protocol_id": "p1"},
    )
    assert card.to_dict() == {
        "agent_id": "n1",
        "name": "Critic",
        "description": "Names the weakest claim.",
        "skills": [{"id": "fact-check", "name": "Fact Check", "description": "Verifies claims."}],
        "metadata": {"protocol_id": "p1", "model": "claude-sonnet-5"},
    }


def test_the_card_never_carries_tools_or_the_system_prompt() -> None:
    """Exposing another agent's servers invites asking it to proxy a tool call;
    the system prompt is user-authored content meaningless to a peer."""
    card = build_agent_card(node_id="n1", label="X", description="d", goal="", skills=[], model=None)
    assert set(card.to_dict()) == {"agent_id", "name", "description", "skills", "metadata"}


async def test_resolve_agent_card_reads_the_node_it_is_given() -> None:
    card = await pe.resolve_agent_card(_pair_graph(), "critic", owner_id=OWNER)
    assert isinstance(card, AgentCard)
    assert (card.agent_id, card.name) == ("critic", "Critic")
    assert card.description == "Names the weakest claim in a draft."


async def test_the_card_carries_the_node_id_not_the_motoro_agent_id() -> None:
    """The Motoro Agent uuid is an implementation detail of one execution; the
    node id is what topology authorizes against and what the transcript
    stores."""
    card = await pe.resolve_agent_card(_pair_graph(), "planner", owner_id=OWNER)
    assert card is not None
    assert card.agent_id == "planner"


async def test_the_model_family_comes_from_the_wired_ai_connector() -> None:
    graph = _pair_graph()
    graph["nodes"].append({"id": "llm", "type": "model_anthropic", "data": {"config": {"model": "claude-sonnet-5"}}})
    graph["edges"].append(_edge("llm", "critic", "model"))

    card = await pe.resolve_agent_card(graph, "critic", owner_id=OWNER)
    assert card is not None
    assert card.metadata["model"] == "claude-sonnet-5"


async def test_a_non_agent_node_has_no_card() -> None:
    graph = {"nodes": [{"id": "llm", "type": "model_anthropic", "data": {"config": {}}}], "edges": []}
    assert await pe.resolve_agent_card(graph, "llm", owner_id=OWNER) is None
    assert await pe.resolve_agent_card(graph, "missing", owner_id=OWNER) is None


async def test_wired_skills_reach_the_card_as_names_and_descriptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Names and descriptions only — a skill's body is the owning agent's
    business, and a peer cannot load it."""
    skill_id = str(uuid.uuid4())
    graph = _pair_graph()
    graph["nodes"].append({"id": "skill1", "type": "skill", "data": {"config": {"skill_id": skill_id}}})
    graph["edges"].append(_edge("skill1", "critic", "skill"))

    seen: dict[str, Any] = {}

    async def _fake_resolve_skills(skill_config: dict[str, Any] | None, **kwargs: Any) -> list[dict[str, Any]]:
        seen.update(skill_config or {})
        return [{"name": "Fact Check", "description": "Verifies claims.", "body": "SECRET", "files": {}}]

    monkeypatch.setattr(pe, "resolve_skills", _fake_resolve_skills)

    card = await pe.resolve_agent_card(graph, "critic", owner_id=OWNER)
    assert card is not None
    assert seen == {"skill_ids": [skill_id]}
    assert [s.name for s in card.skills] == ["Fact Check"]
    assert "SECRET" not in str(card.to_dict())


async def test_available_agents_are_the_connected_peers_cards() -> None:
    agents = await pe.resolve_available_agents(_pair_graph(), "planner", owner_id=OWNER)
    assert [a["agent_id"] for a in agents] == ["critic"]
    assert agents[0]["name"] == "Critic"


async def test_an_agent_with_no_peers_gets_an_empty_list() -> None:
    """Invariant 11 — a pipeline or single-agent run carries no new field
    content, so nothing about it changes."""
    graph = {"nodes": [_agent("solo", label="Solo")], "edges": []}
    assert await pe.resolve_available_agents(graph, "solo", owner_id=OWNER) == []


async def test_a_peer_never_sees_itself_in_its_own_roster() -> None:
    agents = await pe.resolve_available_agents(_pair_graph(), "critic", owner_id=OWNER)
    assert [a["agent_id"] for a in agents] == ["planner"]
