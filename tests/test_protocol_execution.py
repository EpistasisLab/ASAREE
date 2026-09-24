"""Unit tests for topological_order/find_gated_pairs (pure) and
_run_gated_worker (mocked -- never a real LLM call in an automated test)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for experiments' FK
from asaree.models.database import dispose_engine, get_session
from asaree.models.user import User
from asaree.services import protocol_execution as pe
from asaree.services.design_generation import generate_design_cells
from asaree.services.experiments import create_experiment, delete_experiment
from asaree.services.factorial_cells import get_replicate, upsert_replicate
from asaree.services.protocol_execution import (
    ProtocolValidationError,
    apply_factor_bindings,
    find_gated_pairs,
    plan_cell_runs,
    plan_single_replicate_run,
    sink_node_ids,
    topological_order,
    validate_coordination_strategy,
)
from asaree.services.protocol_revisions import publish_protocol
from asaree.services.protocol_runs import create_protocol_run, request_protocol_run_cancellation
from asaree.services.protocols import create_protocol, delete_protocol


def _graph(node_ids: list[str], edges: list[tuple[str, str]]) -> dict:
    # "step" is a deliberately-unregistered node type -- not "agent" (needs
    # a Model connector) and not "mcp_tool" (now handle-restricted to its own
    # Tool connector, see _MCP_TOOL_NODE_TYPES) -- so these pure DAG-shape
    # tests (topological order, cycle detection, sink detection) can wire
    # plain edges freely with zero setup. topological_order only applies
    # type-specific validation to types it recognizes, so an unrecognized
    # type sails through with just the generic Kahn's-algorithm order check.
    return {
        "nodes": [{"id": nid, "type": "step", "data": {}} for nid in node_ids],
        "edges": [{"id": f"{s}-{t}", "source": s, "target": t} for s, t in edges],
    }


def _node(node_id: str, node_type: str, config: dict | None = None, label: str = "") -> dict:
    return {"id": node_id, "type": node_type, "data": {"label": label, "config": config or {}}}


def _edges(*pairs: tuple[str, str]) -> list[dict]:
    return [{"id": f"{s}-{t}", "source": s, "target": t} for s, t in pairs]


def _llm_node(node_id: str = "llm", config: dict | None = None) -> dict:
    # model_anthropic -- one arbitrary member of the Model node-type family
    # (pe._MODEL_NODE_TYPES); which one doesn't matter for these DAG-shape/
    # validation tests, only that it's a family member.
    return {"id": node_id, "type": "model_anthropic", "data": {"label": "", "config": config or {}}}


def _llm_edge(source: str, target: str, handle: str = "model") -> dict:
    # `handle` is only ever overridden to exercise the pre-rename "llm"
    # spelling that migration 3f1a7c9b2e04 rewrites -- see
    # test_legacy_llm_handle_still_resolves.
    return {"id": f"{source}-{target}-ai", "source": source, "target": target, "targetHandle": handle}


def _memory_node(node_id: str = "memory") -> dict:
    return {"id": node_id, "type": "memory", "data": {"label": "", "config": {}}}


def _memory_edge(source: str, target: str) -> dict:
    return {"id": f"{source}-{target}-memory", "source": source, "target": target, "targetHandle": "memory"}


def _pattern_node(node_id: str = "pattern") -> dict:
    # pattern_reason_act -- one arbitrary member of the pattern node-type
    # family (pe._PATTERN_NODE_TYPES), same reasoning as _llm_node above.
    return {"id": node_id, "type": "pattern_reason_act", "data": {"label": "", "config": {}}}


def _pattern_edge(source: str, target: str) -> dict:
    return {
        "id": f"{source}-{target}-architectural_pattern",
        "source": source,
        "target": target,
        "targetHandle": "architectural_pattern",
    }


def _tool_node(node_id: str = "tool1", server_name: str = "srv", tool_names: list[str] | None = None) -> dict:
    return {
        "id": node_id,
        "type": "mcp_tool",
        "data": {
            "label": "",
            "config": {"server_id": "s1", "server_name": server_name, "tool_names": tool_names or ["do_thing"]},
        },
    }


def _tool_edge(source: str, target: str) -> dict:
    return {"id": f"{source}-{target}-tool", "source": source, "target": target, "targetHandle": "tool"}


def _dataset_node(node_id: str = "dataset1", dataset_name: str = "spinal-fusion-v1", dataset_id: str = "d1") -> dict:
    return {
        "id": node_id,
        "type": "dataset",
        "data": {"label": "", "config": {"dataset_id": dataset_id, "dataset_name": dataset_name}},
    }


def _dataset_edge(source: str, target: str, handle: str = "dataset") -> dict:
    # Dataset has its own connector handle (see _NODE_TYPE_TO_HANDLE).
    # `handle="resource"` reproduces a graph saved while that slot was still
    # called Resource, and `handle="tool"` one saved before it existed at all,
    # when it shared the Tool handle -- both still accepted, see
    # _LEGACY_DATASET_HANDLES.
    return {"id": f"{source}-{target}-dataset", "source": source, "target": target, "targetHandle": handle}


def _script_node(node_id: str = "script1", code: str = "print('hi')") -> dict:
    return {
        "id": node_id,
        "type": "script",
        "data": {"label": "", "config": {"name": "scoring-script", "language": "python", "code": code}},
    }


def _script_edge(source: str, target: str) -> dict:
    # Script likewise shares the Tool connector handle -- see _dataset_edge's
    # own comment above.
    return {"id": f"{source}-{target}-script", "source": source, "target": target, "targetHandle": "tool"}


def _skill_node(node_id: str = "skill1", skill_id: str = "s1", name: str = "spinal-scoring") -> dict:
    return {
        "id": node_id,
        "type": "skill",
        "data": {"label": "", "config": {"skill_id": skill_id, "skill_name": name}},
    }


def _skill_edge(source: str, target: str) -> dict:
    # Skill gets its own connector handle rather than sharing Tool's, because
    # core has a real slot for it (Agent.skill_config) -- see
    # _resolve_skill_config.
    return {"id": f"{source}-{target}-skill", "source": source, "target": target, "targetHandle": "skill"}


def _okf_bundle_node(
    node_id: str = "okf1",
    server_name: str = "okf-bundle-spine-abc12345",
    tool_names: list[str] | None = None,
) -> dict:
    return {
        "id": node_id,
        "type": "okf_bundle",
        "data": {
            "label": "",
            "config": {
                "bundle_id": "b1",
                "server_name": server_name,
                "bundle_path": "/home/r/okf/spine",
                "bundle_label": "spine",
                "tool_names": ["list_concepts", "read_concept"] if tool_names is None else tool_names,
            },
        },
    }


def _okf_document_node(
    node_id: str = "doc1",
    server_name: str = "okf-doc-spinal-cord-def45678",
    tool_names: list[str] | None = None,
) -> dict:
    # The Knowledge connector's other source type. Structurally a bundle of
    # one concept -- ASAREE stores the upload in its own directory and serves
    # it with the same per-bundle OKF server -- so it carries the same
    # server_name/tool_names and resolves identically.
    return {
        "id": node_id,
        "type": "okf_document",
        "data": {
            "label": "",
            "config": {
                "document_id": "d1",
                "server_name": server_name,
                "document_path": "/data/okf-documents/u/spinal-cord/spinal-cord.md",
                "document_title": "Spinal cord",
                "tool_names": ["list_concepts", "read_concept"] if tool_names is None else tool_names,
            },
        },
    }


def _knowledge_edge(source: str, target: str) -> dict:
    # OKF Bundle gets its own connector handle rather than sharing Tool's:
    # what it declares is a knowledge base, not one more capability. It still
    # resolves into the same tool allow-list -- see _resolve_knowledge_config.
    return {
        "id": f"{source}-{target}-knowledge",
        "source": source,
        "target": target,
        "targetHandle": "knowledge",
    }


def _agent_with_llm(node_id: str, llm_id: str = "llm") -> tuple[dict, dict]:
    """A minimal valid agent + its required Model connector edge -- the
    boilerplate every connector-validation test below needs just to get
    past the "every agent needs exactly one Model connection" rule so it can
    test the thing it actually cares about."""
    return _node(node_id, "agent"), _llm_edge(llm_id, node_id)


def _sub_agent_edge(child: str, parent: str) -> dict:
    return {
        "id": f"{child}-{parent}-sub-agent",
        "source": child,
        "sourceHandle": "sub_agents",
        "target": parent,
        "targetHandle": "sub_agents",
    }


def test_sub_agent_is_a_callable_connector_not_a_pipeline_sink() -> None:
    parent, parent_model = _agent_with_llm("parent", "parent-model")
    child = _node("child", "sub_agent")
    graph = {
        "nodes": [parent, child, _llm_node("parent-model"), _llm_node("child-model")],
        "edges": [parent_model, _llm_edge("child-model", "child"), _sub_agent_edge("child", "parent")],
    }

    topological_order(graph)

    assert pe.sink_node_ids(graph) == ["parent"]
    assert pe._sub_agent_ids(graph, "parent") == ["child"]
    assert pe._can_deliver_communication(graph, "parent", "child") is True
    assert pe._can_deliver_communication(graph, "child", "parent") is False


def test_sub_agent_cannot_have_two_parents() -> None:
    parent_a, parent_a_model = _agent_with_llm("parent-a", "model-a")
    parent_b, parent_b_model = _agent_with_llm("parent-b", "model-b")
    child = _node("child", "sub_agent")
    graph = {
        "nodes": [parent_a, parent_b, child, _llm_node("model-a"), _llm_node("model-b"), _llm_node("model-c")],
        "edges": [
            parent_a_model,
            parent_b_model,
            _llm_edge("model-c", "child"),
            _sub_agent_edge("child", "parent-a"),
            _sub_agent_edge("child", "parent-b"),
        ],
    }

    with pytest.raises(ProtocolValidationError, match="exactly one parent"):
        topological_order(graph)


def test_only_active_connected_sub_agents_require_a_model() -> None:
    parent, parent_model = _agent_with_llm("parent", "parent-model")
    model = _llm_node("parent-model")
    orphan = _node("orphan", "sub_agent")
    inactive = _node("inactive", "sub_agent")
    inactive["data"]["active"] = False
    graph = {
        "nodes": [parent, model, orphan, inactive],
        "edges": [parent_model, _sub_agent_edge("inactive", "parent")],
    }

    topological_order(graph)

    active = _node("active", "sub_agent")
    graph["nodes"].append(active)
    graph["edges"].append(_sub_agent_edge("active", "parent"))
    with pytest.raises(ProtocolValidationError, match="must have exactly one Model"):
        topological_order(graph)


def test_linear_order() -> None:
    order = [n["id"] for n in topological_order(_graph(["a", "b", "c"], [("a", "b"), ("b", "c")]))]
    assert order == ["a", "b", "c"]


def test_branching_respects_dependencies() -> None:
    graph = _graph(["a", "b", "c", "d"], [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
    order = [n["id"] for n in topological_order(graph)]
    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("a") < order.index("c") < order.index("d")


def test_cycle_raises() -> None:
    with pytest.raises(ProtocolValidationError, match="cycle"):
        topological_order(_graph(["a", "b"], [("a", "b"), ("b", "a")]))


def test_empty_graph_raises() -> None:
    with pytest.raises(ProtocolValidationError, match="no nodes"):
        topological_order({"nodes": [], "edges": []})


def test_dangling_edge_ignored_not_a_cycle() -> None:
    # An edge referencing a node that doesn't exist (e.g. a stale edge after
    # a node was deleted client-side without the edge being cleaned up) is
    # simply not counted -- it must not be misread as a cycle.
    graph = {
        "nodes": [{"id": "a", "type": "step", "data": {}}],
        "edges": [{"id": "a-ghost", "source": "a", "target": "ghost"}],
    }
    order = [n["id"] for n in topological_order(graph)]
    assert order == ["a"]


# --- Critic Gate topology validation -----------------------------------------


def test_valid_gated_pair_passes_and_is_mapped() -> None:
    llm = _llm_node()
    graph = {
        "nodes": [llm, _node("w1", "agent"), _node("g1", "critic_gate"), _node("n1", "agent")],
        "edges": _edges(("w1", "g1"), ("g1", "n1"))
        + [_llm_edge(llm["id"], "w1"), _llm_edge(llm["id"], "g1"), _llm_edge(llm["id"], "n1")],
    }
    order = [n["id"] for n in topological_order(graph)]
    assert order.index("w1") < order.index("g1") < order.index("n1")
    gate_node = next(n for n in graph["nodes"] if n["id"] == "g1")
    assert find_gated_pairs(graph) == {"w1": gate_node}


def test_critic_gate_with_two_incoming_edges_raises() -> None:
    graph = {
        "nodes": [_node("w1", "agent"), _node("w2", "agent"), _node("g1", "critic_gate")],
        "edges": _edges(("w1", "g1"), ("w2", "g1")),
    }
    with pytest.raises(ProtocolValidationError, match="exactly one incoming connection"):
        topological_order(graph)


def test_critic_gate_upstream_not_agent_raises() -> None:
    graph = {
        "nodes": [_node("t1", "mcp_tool"), _node("g1", "critic_gate")],
        "edges": _edges(("t1", "g1")),
    }
    with pytest.raises(ProtocolValidationError, match="must come from an Agent node"):
        topological_order(graph)


def test_gated_worker_fanout_raises() -> None:
    # w1 feeds both its critic_gate AND some other node directly -- ambiguous
    # (anything wanting the reviewed output must consume it after the gate).
    graph = {
        "nodes": [_node("w1", "agent"), _node("g1", "critic_gate"), _node("n1", "agent")],
        "edges": _edges(("w1", "g1"), ("w1", "n1")),
    }
    with pytest.raises(ProtocolValidationError, match="can't have any other outgoing connections"):
        topological_order(graph)


# --- _build_user_input -------------------------------------------------------


def test_build_user_input_prefers_prompt_over_goal() -> None:
    config = {"prompt": "Summarize this quarter's results", "goal": "Analyze financials"}
    node = _node("a", "agent", config, label="Analyst")
    assert pe._build_user_input(node, {"nodes": [node], "edges": []}, {}) == "Summarize this quarter's results"


def test_build_user_input_falls_back_to_goal_when_prompt_blank() -> None:
    node = _node("a", "agent", {"prompt": "", "goal": "Analyze financials"}, label="Analyst")
    assert pe._build_user_input(node, {"nodes": [node], "edges": []}, {}) == "Analyze financials"


def test_build_user_input_falls_back_to_label_when_both_blank() -> None:
    node = _node("a", "agent", {"prompt": "", "goal": ""}, label="Analyst")
    assert pe._build_user_input(node, {"nodes": [node], "edges": []}, {}) == "Analyst"


def test_build_user_input_appends_upstream_context_after_prompt() -> None:
    upstream = _node("u", "agent", {"goal": "produce a draft"}, label="Drafter")
    downstream = _node("d", "agent", {"prompt": "Polish the draft"}, label="Editor")
    graph = {"nodes": [upstream, downstream], "edges": _edges(("u", "d"))}
    node_runs = {"u": {"output_text": "draft text here"}}
    result = pe._build_user_input(downstream, graph, node_runs)
    expected = pe._upstream_context(graph, "d", node_runs)
    assert "draft text here" in expected
    assert result == f"Polish the draft\n\n{expected}"


# --- deactivated pass-through ------------------------------------------------


def test_a_deactivated_node_passes_its_input_through_with_no_label_of_its_own() -> None:
    """The pass-through is the predecessor's text *verbatim*. If it stamped the
    predecessor's name into the text, the reader downstream would see that name
    nested inside a block attributed to the deactivated node."""
    a = _node("a", "agent", {"prompt": "Draft it"}, label="Drafter")
    b = _node("b", "agent", {"prompt": "Polish it"}, label="Editor")
    b["data"]["active"] = False
    graph = {"nodes": [a, b], "edges": _edges(("a", "b"))}
    assert pe._upstream_output_text(graph, "b", {"a": {"output_text": "draft text here"}}) == "draft text here"


def test_a_reader_downstream_of_a_deactivated_node_sees_that_nodes_name() -> None:
    """Attribution follows the graph the run actually walked, not the graph the
    user would have drawn with the node removed. Naming the deactivated node is
    the honest answer -- it is the node whose slot that text arrived in, and
    relabelling it as the original author would hide that a step was skipped.
    Both contracts already behave this way; the envelope must not change it.

    ``d`` is here so there is a fan-in to label: a single
    hand-placed reference carries no name, and the point under test is whose
    name appears, not how many senders there are.
    """
    a = _node("a", "agent", {"prompt": "Draft it"}, label="Drafter")
    b = _node("b", "agent", {"prompt": "Polish it"}, label="Editor")
    b["data"]["active"] = False
    d = _node("d", "agent", {"prompt": "Fact-check it"}, label="Checker")
    c = _node("c", "agent", {"prompt": "Publish: {{previous}}"}, label="Publisher")
    graph = {"nodes": [a, b, d, c], "edges": _edges(("a", "b"), ("b", "c"), ("d", "c"))}
    node_runs: dict = {
        "a": {"status": "completed", "output_text": "draft text here"},
        "d": {"status": "completed", "output_text": "checked"},
    }
    node_runs["b"] = {"status": "completed", "output_text": pe._upstream_output_text(graph, "b", node_runs)}
    text = pe._build_user_input(c, graph, node_runs)
    assert "[Editor]" in text
    assert "draft text here" in text
    assert "Drafter" not in text


def test_build_user_input_cues_dataset_without_dictating_ids() -> None:
    # Operational ids reach open_workspace as ambient _meta. The dataset's
    # descriptive metadata is deliberately visible in the resource catalog so
    # the model can decide whether and how to use the resource.
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node(dataset_name="spinal-fusion-v1")
    graph = {"nodes": [agent, dataset], "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")]}
    result = pe._build_user_input(
        agent, graph, {}, experiment_id=uuid.UUID(int=1), effective_cell_label="tier_a__rep_0"
    )
    assert "Dataset context:" in result
    assert "open_workspace()" in result
    assert "Available datasets:" in result
    assert "spinal-fusion-v1" in result
    assert str(uuid.UUID(int=1)) not in result
    assert "tier_a__rep_0" not in result


def test_resource_catalog_exposes_meaning_but_not_bodies_or_paths() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node()
    dataset["data"]["config"].update(
        description="Postoperative outcomes cohort", target_column="fusion", dictionary_available=True
    )
    knowledge = _okf_bundle_node()
    knowledge["data"]["config"].update(bundle_label="Spine ontology", bundle_description="Clinical concepts")
    script = _script_node(code="SECRET_SCRIPT_BODY")
    script["data"]["config"]["description"] = "Compute the validated score"
    graph = {
        "nodes": [agent, dataset, knowledge, script],
        "edges": [
            agent_llm_edge,
            _dataset_edge(dataset["id"], "a"),
            _knowledge_edge(knowledge["id"], "a"),
            _script_edge(script["id"], "a"),
        ],
    }

    catalog = pe._resource_catalog(graph, "a")

    assert "Postoperative outcomes cohort" in catalog
    assert "target=fusion" in catalog
    assert "data dictionary available" in catalog
    assert "Spine ontology: Clinical concepts" in catalog
    assert "scoring-script: Compute the validated score" in catalog
    assert "SECRET_SCRIPT_BODY" not in catalog
    assert "/home/r/okf/spine" not in catalog


def test_ambient_meta_carries_every_wired_dataset_name() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [
            agent,
            _dataset_node("ds1", dataset_name="cohort-a", dataset_id="d1"),
            _dataset_node("ds2", dataset_name="cohort-b", dataset_id="d2"),
        ],
        "edges": [agent_llm_edge, _dataset_edge("ds1", "a"), _dataset_edge("ds2", "a")],
    }
    assert pe._ambient_meta_for(graph, "a") == {"dataset_names": ["cohort-a", "cohort-b"]}


def test_ambient_meta_empty_without_a_dataset() -> None:
    # Motoro skips an absent/empty ambient_meta, so a node with no references
    # must produce nothing rather than an empty-list key.
    agent, agent_llm_edge = _agent_with_llm("a")
    assert pe._ambient_meta_for({"nodes": [agent], "edges": [agent_llm_edge]}, "a") == {}


def test_build_user_input_names_every_wired_dataset() -> None:
    # With several wired, `name` is the one id that stays in the prompt: the
    # ambient fallback refuses to guess among them (see resolve_dataset_name),
    # so the model has to choose. experiment_id/cell_label still don't appear.
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [
            agent,
            _dataset_node("ds1", dataset_name="cohort-a", dataset_id="d1"),
            _dataset_node("ds2", dataset_name="cohort-b", dataset_id="d2"),
        ],
        "edges": [agent_llm_edge, _dataset_edge("ds1", "a"), _dataset_edge("ds2", "a")],
    }
    result = pe._build_user_input(
        agent, graph, {}, experiment_id=uuid.UUID(int=1), effective_cell_label="tier_a__rep_0"
    )
    assert "2 datasets are registered for this run:" in result
    assert '- "cohort-a"' in result
    assert '- "cohort-b"' in result
    assert str(uuid.UUID(int=1)) not in result
    assert "tier_a__rep_0" not in result


def test_build_user_input_states_the_dataset_is_already_open_when_preseeded() -> None:
    # ASAREE seeds the cell workspace before the agent's first turn, so the
    # Dataset block stops asking for a tool call. A step the agent can't skip
    # is a step it can't get wrong -- and a new user never has to learn that
    # "open a workspace" was a thing their agent had to be told to do.
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node(dataset_name="spinal-fusion-v1")
    graph = {"nodes": [agent, dataset], "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")]}
    result = pe._build_user_input(
        agent,
        graph,
        {},
        experiment_id=uuid.UUID(int=1),
        effective_cell_label="tier_a__rep_0",
        seeded_datasets=(("spinal-fusion-v1", "dataset:default"),),
    )
    assert "already open" in result
    assert "spinal-fusion-v1" in result  # named, so the transcript shows what it worked on
    assert "Do NOT call open_workspace" in result
    assert str(uuid.UUID(int=1)) not in result
    assert "tier_a__rep_0" not in result


def _registration(**overrides: object) -> dict[str, object]:
    """A split registration as ``fetch_owned_registration`` returns one."""
    return {
        "description": "A registered test dataset",
        "target_column": "outcome",
        "raw_path": "/data/raw.csv",
        "train_path": "/data/train.parquet",
        "test_path": "/data/test.parquet",
        "dictionary_json": None,
        **overrides,
    }


async def test_sync_durable_agent_recovers_from_a_concurrent_create(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second protocol run reuses the Agent created by the winning run."""
    test_owner_id = uuid.uuid4()
    existing = SimpleNamespace(id=uuid.uuid4())
    lookups = 0
    updates: list[tuple[uuid.UUID, dict[str, object]]] = []

    async def fake_get_agent_by_name(name: str, *, owner_id: uuid.UUID):
        nonlocal lookups
        assert name == "protocol-1-node-a"
        assert owner_id == test_owner_id
        lookups += 1
        return None if lookups == 1 else existing

    async def fake_create_agent(**_kwargs: object):
        raise pe.IntegrityError("INSERT", {}, Exception("duplicate key"))

    async def fake_update_agent(agent_id: uuid.UUID, **fields: object):
        updates.append((agent_id, fields))
        return existing

    monkeypatch.setattr(pe, "get_agent_by_name", fake_get_agent_by_name)
    monkeypatch.setattr(pe, "create_agent", fake_create_agent)
    monkeypatch.setattr(pe, "update_agent", fake_update_agent)

    result = await pe._sync_durable_agent(
        name="protocol-1-node-a", owner_id=test_owner_id, fields={"goal": "Do the work."}
    )

    assert result is existing
    assert lookups == 2
    assert updates == [(existing.id, {"goal": "Do the work."})]


async def test_preseed_skipped_without_a_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    # An unlinked protocol run has no cell workspace to seed, so there is
    # nowhere to put a slot -- it keeps the agent-driven open_workspace(name=...).
    async def _reg(name: str, owner_id: uuid.UUID) -> dict[str, object]:
        return _registration()

    monkeypatch.setattr(pe, "fetch_owned_registration", _reg)
    agent, agent_llm_edge = _agent_with_llm("a")
    one = {
        "nodes": [agent, _dataset_node(dataset_name="solo")],
        "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")],
    }
    assert await pe._resolve_node_dataset(one, "a", None, uuid.UUID(int=7)) == pe.NodeDataset()


async def test_several_wired_datasets_each_get_their_own_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    # Several datasets used to be refused outright (one workspace, one dataset)
    # and fell back to the agent-driven open. A workspace now holds one dataset
    # per named slot, so every wired dataset is seeded and the slot key travels
    # with the name -- the prompt has to tell the agent which slot="..." its
    # tool calls will accept.
    seen: list[str | None] = []

    async def _reg(name: str, owner_id: uuid.UUID) -> dict[str, object]:
        return _registration()

    async def _seed(**kwargs: object) -> object:
        slot = kwargs["slot"]
        seen.append(slot)  # type: ignore[arg-type]
        name = str(kwargs["dataset_name"])
        return SimpleNamespace(dataset_name=name, slot=str(slot or "dataset:default"))

    monkeypatch.setattr(pe, "fetch_owned_registration", _reg)
    monkeypatch.setattr(pe, "seed_cell_workspace", _seed)
    agent, agent_llm_edge = _agent_with_llm("a")
    many = {
        "nodes": [
            agent,
            _dataset_node("ds1", dataset_name="cohort-a", dataset_id="d1"),
            _dataset_node("ds2", dataset_name="cohort-b", dataset_id="d2"),
        ],
        "edges": [agent_llm_edge, _dataset_edge("ds1", "a"), _dataset_edge("ds2", "a")],
    }
    resolved = await pe._resolve_node_dataset(many, "a", "exp/cell", uuid.UUID(int=7))
    assert resolved.seeded == (("cohort-a", "dataset:cohort-a"), ("cohort-b", "dataset:cohort-b"))
    assert seen == ["dataset:cohort-a", "dataset:cohort-b"]

    # A lone dataset still seeds with slot=None, so its workspace keeps the
    # pre-slot on-disk layout untouched.
    seen.clear()
    solo = {
        "nodes": [agent, _dataset_node("ds1", dataset_name="cohort-a", dataset_id="d1")],
        "edges": [agent_llm_edge, _dataset_edge("ds1", "a")],
    }
    await pe._resolve_node_dataset(solo, "a", "exp/cell", uuid.UUID(int=7))
    assert seen == [None]


def test_build_user_input_names_the_slot_of_each_seeded_dataset() -> None:
    # With several open there is no "the" workspace dataset, so the prompt
    # lists them with the slot key each tool call needs; leaving the agent to
    # guess would silently read whichever slot came first.
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [
            agent,
            _dataset_node("ds1", dataset_name="cohort-a", dataset_id="d1"),
            _dataset_node("ds2", dataset_name="cohort-b", dataset_id="d2"),
        ],
        "edges": [agent_llm_edge, _dataset_edge("ds1", "a"), _dataset_edge("ds2", "a")],
    }
    result = pe._build_user_input(
        agent,
        graph,
        {},
        experiment_id=uuid.UUID(int=1),
        effective_cell_label="tier_a__rep_0",
        seeded_datasets=(("cohort-a", "dataset:cohort-a"), ("cohort-b", "dataset:cohort-b")),
    )
    assert 'slot="dataset:cohort-a"' in result
    assert 'slot="dataset:cohort-b"' in result
    assert "Do NOT call open_workspace" in result


async def test_preseed_failure_falls_back_to_the_agent_driven_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # A broken registration must not kill the run before its first turn: the
    # seeding error is logged, an empty NodeDataset comes back, and
    # _build_user_input reverts to asking the agent to call open_workspace --
    # which surfaces the real error where someone is actually reading it.
    async def _reg(name: str, owner_id: uuid.UUID) -> dict[str, object]:
        return _registration()

    async def _boom(**_kwargs: object) -> None:
        raise pe.WorkspaceSeedError("Dataset 'gone' not found in registry.")

    monkeypatch.setattr(pe, "fetch_owned_registration", _reg)
    monkeypatch.setattr(pe, "seed_cell_workspace", _boom)
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [agent, _dataset_node(dataset_name="gone")],
        "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")],
    }
    assert await pe._resolve_node_dataset(graph, "a", "exp/cell", uuid.UUID(int=7)) == pe.NodeDataset()

    result = pe._build_user_input(
        agent, graph, {}, experiment_id=uuid.UUID(int=1), effective_cell_label="tier_a__rep_0", seeded_datasets=()
    )
    assert "Call open_workspace()" in result


async def test_an_unsplit_dataset_binds_its_raw_file_instead_of_a_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Splitting in ASAREE is optional, so a registration can be a raw file with
    # no train/test pair. There is no workspace to seed for one -- the staged
    # pipeline is defined over a frozen split -- so the raw file itself becomes
    # the run's data_path and the agent splits it with the sklearn tools.
    async def _reg(name: str, owner_id: uuid.UUID) -> dict[str, object]:
        return _registration(train_path=None, test_path=None, raw_path="/data/spine/raw.csv")

    def _no_workspace(workspace_id: str) -> tuple[str, str]:
        return "", ""

    async def _never(**_kwargs: object) -> None:
        raise AssertionError("an unsplit dataset must not attempt to seed a workspace")

    monkeypatch.setattr(pe, "fetch_owned_registration", _reg)
    monkeypatch.setattr(pe, "head_data_locator", _no_workspace)
    monkeypatch.setattr(pe, "seed_cell_workspace", _never)
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [agent, _dataset_node(dataset_name="spine-raw")],
        "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")],
    }

    ambient, dataset = await pe._node_run_context(graph, "a", "exp1/cellA", uuid.UUID(int=7))
    assert dataset.unsplit_name == "spine-raw"
    assert dataset.seeded == ()
    assert ambient["data_path"] == "/data/spine/raw.csv"
    assert ambient["target_column"] == "outcome"
    assert ambient["dataset_mode"] == "raw_unsplit"

    # And the prompt says so, because a model left to infer it reaches for
    # open_workspace -- which has nothing to open.
    result = pe._build_user_input(
        agent,
        graph,
        {},
        experiment_id=uuid.UUID(int=1),
        effective_cell_label="tier_a__rep_0",
        unsplit_dataset=dataset.unsplit_name,
    )
    assert "has NOT been split" in result
    assert "Do NOT call open_workspace" in result
    assert "train_test_split" in result


async def test_an_attached_unsplit_dataset_wins_over_a_stale_workspace_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A workspace survives reruns and may belong to a Dataset connector from an
    # older published revision. The current node's explicit unsplit attachment
    # must win; only nodes with no Dataset connector inherit upstream HEAD.
    async def _reg(name: str, owner_id: uuid.UUID) -> dict[str, object]:
        return _registration(train_path=None, test_path=None, raw_path="/data/current.csv")

    monkeypatch.setattr(pe, "fetch_owned_registration", _reg)
    monkeypatch.setattr(pe, "head_data_locator", lambda wid: ("/ws/v2_fte/train.parquet", "outcome"))
    monkeypatch.setattr(
        pe,
        "slot_data_locators",
        lambda wid: {
            "dataset:old": {"data_path": "/ws/old.parquet", "target_column": "old_target"},
            "dataset:other": {"data_path": "/ws/other.parquet", "target_column": "other_target"},
        },
    )
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [agent, _dataset_node(dataset_name="spine-raw")],
        "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")],
    }
    ambient, _dataset = await pe._node_run_context(graph, "a", "exp1/cellA", uuid.UUID(int=7))
    assert ambient["data_path"] == "/data/current.csv"
    assert ambient["target_column"] == "outcome"
    assert ambient["dataset_mode"] == "raw_unsplit"
    assert "data_slots" not in ambient


async def test_an_attached_split_dataset_sees_only_its_workspace_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _resolved(*_args: object, **_kwargs: object) -> pe.NodeDataset:
        return pe.NodeDataset(seeded=(("current", "dataset:current"),))

    monkeypatch.setattr(pe, "_resolve_node_dataset", _resolved)
    monkeypatch.setattr(
        pe,
        "slot_data_locators",
        lambda wid: {
            "dataset:current": {
                "name": "current",
                "data_path": "/ws/current/train.parquet",
                "target_column": "outcome",
            },
            "dataset:unwired": {
                "name": "unwired",
                "data_path": "/ws/unwired/train.parquet",
                "target_column": "other_target",
            },
        },
    )
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [agent, _dataset_node(dataset_name="current")],
        "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")],
    }

    ambient, _dataset = await pe._node_run_context(graph, "a", "exp1/cellA", uuid.UUID(int=7))

    assert ambient["data_path"] == "/ws/current/train.parquet"
    assert ambient["target_column"] == "outcome"
    assert "data_slots" not in ambient


def test_dataset_connector_grants_the_workspace_tools() -> None:
    # Wiring a Dataset node is the whole gesture: the tools that make working
    # on that data possible follow from it, with no second asaree-workspace
    # Tool node to know about. Namespaced, since gather_tools matches against
    # Motoro's registry (see test_resolve_tool_config_namespaces_tool_names_*).
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [agent, _dataset_node(dataset_name="spinal-fusion-v1")],
        "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")],
    }
    resolved = pe._resolve_dataset_tool_config(graph, "a")
    assert resolved["server_names"] == ["asaree-workspace"]
    assert "asaree-workspace.open_workspace" in resolved["tool_names"]
    assert "asaree-workspace.accept_stage" in resolved["tool_names"]
    # A health check and a no-op shim are noise in an agent's tool list.
    assert "asaree-workspace.ping" not in resolved["tool_names"]

    # No Dataset wired -> no implicit grant.
    bare = {"nodes": [agent], "edges": [agent_llm_edge]}
    assert pe._resolve_dataset_tool_config(bare, "a") == {"server_names": [], "tool_names": []}


def test_dataset_with_dictionary_grants_only_the_dictionary_reader() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node(dataset_name="spinal-fusion-v1")
    dataset["data"]["config"]["dictionary_available"] = True
    graph = {
        "nodes": [agent, dataset],
        "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")],
    }

    resolved = pe._resolve_dataset_tool_config(graph, "a")

    assert "asaree-sklearn-eda" in resolved["server_names"]
    eda_tools = {name for name in resolved["tool_names"] if name.startswith("asaree-sklearn-eda.")}
    assert eda_tools == {"asaree-sklearn-eda.get_data_dictionary"}


def test_an_unsplit_dataset_grants_the_tools_its_prompt_names() -> None:
    """The gap the first sequential demo run fell into: an unsplit registration
    has no workspace, so the Dataset block tells the agent NOT to call
    open_workspace and to use describe_dataset/describe_split/train_test_split
    instead -- and none of those were in the allow-list, so the run ended with
    the model reporting the missing tools rather than profiling the data."""
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [agent, _dataset_node(dataset_name="spinal-fusion")],
        "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")],
    }
    resolved = pe._resolve_dataset_tool_config(graph, "a", unsplit_dataset="spinal-fusion")
    assert resolved["server_names"] == ["asaree-workspace", "scikit-learn-mcp"]
    # Exactly the three the prompt names, no more: fitting a model is a real
    # choice about the analysis, so the rest of that server still takes a Tool
    # node. Asserted as a set so a new sklearn tool cannot leak in silently.
    sklearn = {name for name in resolved["tool_names"] if name.startswith("scikit-learn-mcp.")}
    assert sklearn == {
        "scikit-learn-mcp.describe_dataset",
        "scikit-learn-mcp.describe_split",
        "scikit-learn-mcp.train_test_split",
    }
    # The workspace tools stay: workspace_status answering "nothing here" beats
    # a missing tool, and a split dataset gets no sklearn grant at all.
    assert "asaree-workspace.workspace_status" in resolved["tool_names"]
    split = pe._resolve_dataset_tool_config(graph, "a")
    assert split["server_names"] == ["asaree-workspace"]


def test_script_connector_grants_the_script_runner() -> None:
    # Wiring a Script node is the whole gesture, exactly like Dataset above:
    # before this, a script was only runnable if the user ALSO wired one of the
    # two sklearn servers whose script tools read the ambient path -- and both
    # reject a script that isn't fitting a model.
    agent, agent_llm_edge = _agent_with_llm("a")
    script = _script_node(code="print('hello')")
    graph = {"nodes": [agent, script], "edges": [agent_llm_edge, _script_edge("script1", "a")]}
    resolved = pe._resolve_script_tool_config(graph, "a")
    assert resolved["server_names"] == ["asaree-script"]
    assert resolved["tool_names"] == ["asaree-script.run_wired_script"]

    # No Script wired -> no implicit grant.
    bare = {"nodes": [agent], "edges": [agent_llm_edge]}
    assert pe._resolve_script_tool_config(bare, "a") == {"server_names": [], "tool_names": []}

    # An empty Script node publishes no ambient path either, so the tool would
    # only be there to report its own absence.
    empty = {
        "nodes": [agent, _script_node(code="")],
        "edges": [agent_llm_edge, _script_edge("script1", "a")],
    }
    assert pe._resolve_script_tool_config(empty, "a") == {"server_names": [], "tool_names": []}


def test_merge_tool_configs_does_not_double_an_explicitly_wired_workspace() -> None:
    # A user who also wired an asaree-workspace Tool node would otherwise
    # contribute the same namespaced names twice, now that one grant is implicit.
    explicit = {"server_names": ["asaree-workspace"], "tool_names": ["asaree-workspace.open_workspace"]}
    implicit = {
        "server_names": ["asaree-workspace"],
        "tool_names": ["asaree-workspace.open_workspace", "asaree-workspace.accept_stage"],
    }
    assert pe._merge_tool_configs(explicit, implicit) == {
        "server_names": ["asaree-workspace"],
        "tool_names": ["asaree-workspace.open_workspace", "asaree-workspace.accept_stage"],
    }


def test_dataset_resolves_from_legacy_tool_handle() -> None:
    # A graph saved before the Dataset connector existed still has its
    # dataset edge on the Tool handle (see _LEGACY_DATASET_HANDLES) -- it
    # keeps resolving identically, so an old protocol runs unchanged even if
    # it's never opened in the canvas (which would rewrite the handle).
    # Asserted against the ambient meta, which is where the name goes now.
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node(dataset_name="spinal-fusion-v1")
    graph = {
        "nodes": [_llm_node(), agent, dataset],
        "edges": [agent_llm_edge, _dataset_edge("dataset1", "a", handle="tool")],
    }
    topological_order(graph)  # legacy handle is still a valid wiring, not a validation error
    assert pe._ambient_meta_for(graph, "a") == {"dataset_names": ["spinal-fusion-v1"]}
    assert "Dataset context:" in pe._build_user_input(
        agent, graph, {}, experiment_id=uuid.UUID(int=1), effective_cell_label="tier_a__rep_0"
    )


def test_dataset_resolves_from_legacy_resource_handle() -> None:
    # Ditto for the intermediate spelling: the slot existed but was called
    # "Resource" (migration 3f1a7c9b2e04) before being renamed after the only
    # node type it accepts (b7c2d9e14a35).
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node(dataset_name="spinal-fusion-v1")
    graph = {
        "nodes": [_llm_node(), agent, dataset],
        "edges": [agent_llm_edge, _dataset_edge("dataset1", "a", handle="resource")],
    }
    topological_order(graph)
    assert pe._ambient_meta_for(graph, "a") == {"dataset_names": ["spinal-fusion-v1"]}
    assert "Dataset context:" in pe._build_user_input(
        agent, graph, {}, experiment_id=uuid.UUID(int=1), effective_cell_label="tier_a__rep_0"
    )


def test_build_user_input_omits_dataset_context_when_disabled() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = {
        "id": "dataset1",
        "type": "dataset",
        "data": {"label": "", "config": {"dataset_id": "d1", "dataset_name": "spinal-fusion-v1", "enabled": False}},
    }
    graph = {"nodes": [agent, dataset], "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")]}
    result = pe._build_user_input(
        agent, graph, {}, experiment_id=uuid.UUID(int=1), effective_cell_label="tier_a__rep_0"
    )
    assert "Dataset context" not in result


def test_build_user_input_omits_dataset_context_when_unwired() -> None:
    node = _node("a", "agent", {"goal": "do the work"}, label="Worker")
    result = pe._build_user_input(
        node, {"nodes": [node], "edges": []}, {}, experiment_id=uuid.UUID(int=1), effective_cell_label="cell"
    )
    assert "Dataset context" not in result


def test_build_user_input_omits_dataset_context_without_experiment_id() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node(dataset_name="spinal-fusion-v1")
    graph = {"nodes": [agent, dataset], "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")]}
    # No experiment_id/effective_cell_label given -- nothing to build the
    # open_workspace instruction from, so the block is silently omitted
    # rather than emitting a malformed instruction.
    result = pe._build_user_input(agent, graph, {})
    assert "Dataset context" not in result


def test_build_user_input_cues_bound_script_without_inlining_it() -> None:
    # The script reached the tool as a path, so the source stays out of the
    # prompt -- it would otherwise cost tokens on every turn of the loop and
    # be only as faithful as the model's retyping.
    agent, agent_llm_edge = _agent_with_llm("a")
    script = _script_node(code="print('hello')")
    graph = {"nodes": [agent, script], "edges": [agent_llm_edge, _script_edge("script1", "a")]}
    result = pe._build_user_input(agent, graph, {}, script_bound=True)
    assert "Script context:" in result
    assert "print('hello')" not in result
    # And it names the tool that is actually granted with the Script node, not
    # a sklearn harness the agent may well not have.
    assert "run_wired_script()" in result


def test_build_user_input_lists_multiple_bound_scripts() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    first = _script_node("script1", code="print('first')")
    second = _script_node("script2", code="print('second')")
    first["data"]["config"]["name"] = "first-report"
    second["data"]["config"]["name"] = "second-report"
    graph = {
        "nodes": [agent, first, second],
        "edges": [agent_llm_edge, _script_edge("script1", "a"), _script_edge("script2", "a")],
    }
    result = pe._build_user_input(agent, graph, {}, script_bound=True)
    assert "2 scripts are wired" in result
    assert "first-report" in result
    assert "second-report" in result
    assert "run_wired_script(script=...)" in result
    assert "print('first')" not in result
    assert "print('second')" not in result


def test_build_user_input_does_not_inline_script_when_it_could_not_be_bound() -> None:
    # Source code is never prompt content. An unlinked run reports the missing
    # materialization context instead of asking the model to retranscribe code.
    agent, agent_llm_edge = _agent_with_llm("a")
    script = _script_node(code="print('hello')")
    graph = {"nodes": [agent, script], "edges": [agent_llm_edge, _script_edge("script1", "a")]}
    result = pe._build_user_input(agent, graph, {}, script_bound=False)
    assert "no isolated run workspace" in result
    assert "print('hello')" not in result


def test_build_user_input_omits_script_block_when_unwired() -> None:
    node = _node("a", "agent", {"goal": "do the work"}, label="Worker")
    result = pe._build_user_input(node, {"nodes": [node], "edges": []}, {})
    assert "Script to pass verbatim" not in result
    assert "Script context:" not in result


def test_ambient_meta_writes_the_wired_script_and_carries_its_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pe, "WORKSPACE_ROOT", str(tmp_path))
    agent, agent_llm_edge = _agent_with_llm("a")
    script = _script_node(code="print('hello')")
    graph = {"nodes": [agent, script], "edges": [agent_llm_edge, _script_edge("script1", "a")]}

    meta = pe._ambient_meta_for(graph, "a", "exp1/cellA")
    assert Path(meta["script_path"]).read_text() == "print('hello')"

    # An edited Script node must not leave the previous run's copy behind for
    # a rerun to execute -- the graph is the source of truth, not the file.
    script["data"]["config"]["code"] = "print('edited')"
    assert Path(pe._ambient_meta_for(graph, "a", "exp1/cellA")["script_path"]).read_text() == "print('edited')"


def test_ambient_meta_materializes_all_wired_scripts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pe, "WORKSPACE_ROOT", str(tmp_path))
    agent, agent_llm_edge = _agent_with_llm("a")
    first = _script_node("script1", code="print('first')")
    second = _script_node("script2", code="print('second')")
    first["data"]["config"]["name"] = "first-report"
    second["data"]["config"]["name"] = "second-report"
    graph = {
        "nodes": [agent, first, second],
        "edges": [agent_llm_edge, _script_edge("script1", "a"), _script_edge("script2", "a")],
    }

    meta = pe._ambient_meta_for(graph, "a", "exp1/cellA")
    assert "script_path" not in meta
    assert [(item["id"], item["name"]) for item in meta["script_paths"]] == [
        ("script1", "first-report"),
        ("script2", "second-report"),
    ]
    assert [Path(item["path"]).read_text() for item in meta["script_paths"]] == [
        "print('first')",
        "print('second')",
    ]


def test_ambient_meta_carries_the_workspace_head_as_a_data_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # For scikit-learn-mcp, which takes its dataset as a path instead of
    # reading the workspace: without this the only dataset identity the agent
    # could see was the workspace id, and it passed THAT as data_path.
    monkeypatch.setattr(pe, "head_data_locator", lambda wid: (f"/ws/{wid}/v1_dc/train.parquet", "outcome"))
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [agent, _dataset_node(dataset_name="spinal-fusion-v1")],
        "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")],
    }
    meta = pe._ambient_meta_for(graph, "a", "exp1/cellA")
    assert meta["data_path"] == "/ws/exp1/cellA/v1_dc/train.parquet"
    assert meta["target_column"] == "outcome"

    # Keyed off the workspace, NOT off a wired Dataset connector: the spinal
    # graph wires the dataset into DC/FTE/FS only, and the nodes that actually
    # want a path are the model-fitting ones (MLM, Score) that have none.
    bare = {"nodes": [agent], "edges": [agent_llm_edge]}
    assert pe._ambient_meta_for(bare, "a", "exp1/cellA")["data_path"] == "/ws/exp1/cellA/v1_dc/train.parquet"

    # No workspace at all (an unlinked protocol run): nothing to name, and the
    # tool asking for an explicit data_path is the correct outcome.
    assert "data_path" not in pe._ambient_meta_for(graph, "a", None)


async def test_node_run_context_seeds_before_reading_head(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ordering is the whole point: the ambient data_path names the workspace's
    # HEAD version, which doesn't exist until the pre-seed has created it.
    calls: list[str] = []

    async def _seed(
        graph: dict,
        node_id: str,
        workspace_id: str | None,
        owner_id: uuid.UUID,
        *,
        slot_prefix: str | None = None,
        stage_plan: object = None,
    ) -> pe.NodeDataset:
        calls.append("seed")
        return pe.NodeDataset(seeded=(("spinal-fusion-v1", "dataset:default"),))

    def _locator(workspace_id: str) -> tuple[str, str]:
        calls.append("locator")
        return "/ws/train.parquet", "outcome"

    monkeypatch.setattr(pe, "_resolve_node_dataset", _seed)
    monkeypatch.setattr(pe, "head_data_locator", _locator)
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [agent, _dataset_node(dataset_name="spinal-fusion-v1")],
        "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")],
    }
    ambient, dataset = await pe._node_run_context(graph, "a", "exp1/cellA", uuid.UUID(int=7))
    assert calls == ["seed", "locator"]
    assert dataset.seeded_names == ("spinal-fusion-v1",)
    assert ambient["data_path"] == "/ws/train.parquet"


def test_ambient_meta_omits_script_path_without_a_workspace() -> None:
    # The low-level helper still requires an explicit materialization surface.
    agent, agent_llm_edge = _agent_with_llm("a")
    script = _script_node(code="print('hello')")
    graph = {"nodes": [agent, script], "edges": [agent_llm_edge, _script_edge("script1", "a")]}
    assert pe._ambient_meta_for(graph, "a", None) == {}


def test_standalone_run_materializes_script_out_of_band(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pe, "WORKSPACE_ROOT", str(tmp_path))
    agent, agent_llm_edge = _agent_with_llm("a")
    script = _script_node(code="print('hello')")
    graph = {"nodes": [agent, script], "edges": [agent_llm_edge, _script_edge("script1", "a")]}

    meta = pe._ambient_meta_for(graph, "a", script_workspace_id="_protocol_runs/run-1")

    path = Path(meta["script_path"])
    assert path.read_text() == "print('hello')"
    assert "_protocol_runs/run-1" in str(path)
    pe._cleanup_adhoc_scripts(meta)
    assert not path.exists()


def test_standalone_agents_get_distinct_script_directories() -> None:
    run_id = uuid.uuid4()
    assert pe._script_workspace_id(None, run_id, "agent-a") != pe._script_workspace_id(None, run_id, "agent-b")


# --- _run_gated_worker (mocked -- no real LLM calls) -------------------------


def _worker_gate(max_revisions: int = 1, enabled: bool = True) -> tuple[dict, dict]:
    worker = _node("w1", "agent", {"goal": "do the work"}, label="Worker")
    gate = _node("g1", "critic_gate", {"enabled": enabled, "max_revisions": max_revisions}, label="Gate")
    return worker, gate


async def _run(worker: dict, gate: dict) -> tuple[dict, dict]:
    graph = {"nodes": [worker, gate], "edges": [{"id": "e1", "source": worker["id"], "target": gate["id"]}]}
    return await pe._run_gated_worker(
        worker,
        gate,
        protocol_id=uuid.uuid4(),
        protocol_run_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        graph=graph,
        node_runs={},
    )


async def test_gated_worker_approved_first_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    critic_calls = []

    async def fake_run_agent_node(node, **kwargs):
        return "worker output v1", None, None, None

    async def fake_run_critic(gate, **kwargs):
        critic_calls.append(kwargs["worker_output"])
        return {"approved": True, "feedback": "", "rejection_scope": ""}, None, "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate())
    assert worker_run == {
        "status": "completed",
        "output_text": "worker output v1",
        "error": None,
        "attempts": 1,
        "run_id": None,
    }
    assert gate_run["approved"] is True
    assert gate_run["revisions_used"] == 0
    assert gate_run["feedback"] == ""
    assert gate_run["run_id"] == "critic-run-1"
    assert critic_calls == ["worker output v1"]


async def test_gated_worker_rejected_then_approved_on_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    instructions = []
    critic_calls = []

    async def fake_run_agent_node(node, *, user_input, **_kwargs):
        instructions.append(user_input)
        return f"worker output v{len(instructions)}", None, None, None

    async def fake_run_critic(gate, *, worker_output, **_kwargs):
        critic_calls.append(worker_output)
        if len(critic_calls) == 1:
            return {"approved": False, "feedback": "fix the header", "rejection_scope": "partial"}, None, "critic-run-1"
        return {"approved": True, "feedback": "", "rejection_scope": ""}, None, "critic-run-2"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    # max_revisions=2 (not 1): with only 1 revision allowed, the 2nd/final
    # attempt would skip the critic entirely (see the force-accept test
    # below) -- this test needs room for a real "rejected, revised,
    # re-reviewed, approved" cycle before the final attempt.
    worker_run, gate_run = await _run(*_worker_gate(max_revisions=2))
    assert worker_run == {
        "status": "completed",
        "output_text": "worker output v2",
        "error": None,
        "attempts": 2,
        "run_id": None,
    }
    assert gate_run["approved"] is True
    assert gate_run["revisions_used"] == 1
    # The persisted verdict is the one that actually approved it, not the
    # earlier rejection that triggered the revision.
    assert gate_run["feedback"] == ""
    assert gate_run["run_id"] == "critic-run-2"
    assert len(critic_calls) == 2
    # The revised instruction carries the critic's feedback and the scope
    # framing forward -- not just a bare rerun of the original goal.
    assert "fix the header" in instructions[1]
    assert "targeted correction" in instructions[1]  # "partial" scope clause
    assert "worker output v1" in instructions[1]  # previous output included for reference


async def test_gated_worker_force_accepts_without_final_critic_call(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []
    critic_calls = []

    async def fake_run_agent_node(node, *, user_input, **_kwargs):
        attempts.append(user_input)
        return f"worker output v{len(attempts)}", None, None, None

    async def fake_run_critic(gate, *, worker_output, **_kwargs):
        critic_calls.append(worker_output)
        return {"approved": False, "feedback": "still wrong", "rejection_scope": "full"}, None, "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate(max_revisions=1))
    # 2 total attempts (max_revisions=1), but the critic is only ever
    # consulted once -- the final attempt force-accepts without spending a
    # second critic call on a verdict the pipeline would ignore anyway.
    assert worker_run["attempts"] == 2
    assert len(critic_calls) == 1
    # The forced-accept branch still surfaces the *last* critic verdict
    # (the rejection that forced this final attempt) instead of discarding
    # it once it stops being used to build the next instruction.
    assert gate_run == {
        "status": "completed",
        "output_text": "worker output v2",
        "approved": None,
        "revisions_used": 1,
        "forced": True,
        "feedback": "still wrong",
        "rejection_scope": "full",
        "run_id": "critic-run-1",
    }


async def test_gated_worker_disabled_skips_critic_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    critic_calls = []

    async def fake_run_agent_node(node, **kwargs):
        return "worker output", None, None, None

    async def fake_run_critic(gate, **kwargs):
        critic_calls.append(1)
        return {"approved": True, "feedback": "", "rejection_scope": ""}, None, "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate(enabled=False))
    assert worker_run["attempts"] == 1
    assert critic_calls == []
    assert gate_run["approved"] is None


async def test_gated_worker_worker_failure_stops_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    critic_calls = []

    async def fake_run_agent_node(node, **kwargs):
        return None, "the LLM call failed", None, None

    async def fake_run_critic(gate, **kwargs):
        critic_calls.append(1)
        return {"approved": True, "feedback": "", "rejection_scope": ""}, None, "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate())
    assert worker_run == {
        "status": "failed",
        "output_text": None,
        "error": "the LLM call failed",
        "attempts": 1,
        "run_id": None,
    }
    assert gate_run == {"status": "skipped"}
    assert critic_calls == []


async def test_gated_worker_critic_failure_fails_the_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_agent_node(node, **kwargs):
        return "worker output", None, None, None

    async def fake_run_critic(gate, **kwargs):
        return None, "critic run timed out", "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate())
    assert worker_run["status"] == "failed"
    assert "critic failed" in worker_run["error"]
    # Even on failure, the critic's own run_id is surfaced -- lets a user
    # drill into a timed-out/errored critic run for debugging.
    assert gate_run == {
        "status": "failed",
        "output_text": None,
        "error": "critic run timed out",
        "run_id": "critic-run-1",
    }


async def test_gated_worker_cancelled_mid_worker_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """_AGENT_CANCELLED (Motoro's own RunStatus.CANCELLED, detected in
    _run_agent_node) must be distinguished from a plain error -- it needs to
    surface as "cancelled", not "failed", so run_protocol's cancelled flag
    (not its failed flag) is what fires. The critic is never called (there's
    no output to review)."""
    critic_calls = []

    async def fake_run_agent_node(node, **kwargs):
        return None, pe._AGENT_CANCELLED, "worker-run-1", None

    async def fake_run_critic(gate, **kwargs):
        critic_calls.append(1)
        return {"approved": True}, None, "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate())
    assert worker_run == {
        "status": "cancelled",
        "output_text": None,
        "error": None,
        "attempts": 1,
        "run_id": "worker-run-1",
    }
    assert gate_run == {"status": "skipped"}
    assert critic_calls == []


async def test_gated_worker_cancelled_mid_critic_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cancellation caught while the CRITIC is reviewing is different from
    one caught during the worker's own run: the worker already produced a
    real, complete output, so its own node_run still reports "completed"
    with that output -- only the gate's own node_run is "cancelled"."""

    async def fake_run_agent_node(node, **kwargs):
        return "real worker output", None, "worker-run-1", None

    async def fake_run_critic(gate, **kwargs):
        return None, pe._AGENT_CANCELLED, "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate())
    assert worker_run == {
        "status": "completed",
        "output_text": "real worker output",
        "error": None,
        "attempts": 1,
        "run_id": "worker-run-1",
    }
    assert gate_run == {"status": "cancelled", "output_text": None, "error": None, "run_id": "critic-run-1"}


# --- apply_factor_bindings / sink_node_ids (pure) ----------------------------


def test_apply_factor_bindings_substitutes_bound_field() -> None:
    graph = {
        "nodes": [
            {
                "id": "w1",
                "type": "agent",
                "data": {
                    "config": {"model_config_data": {"temperature": 0.7}},
                    "factor_bindings": {"config.model_config_data.temperature": "Temperature"},
                },
            }
        ],
        "edges": [],
    }
    patched = apply_factor_bindings(graph, {"Temperature": 0.2})
    assert patched["nodes"][0]["data"]["config"]["model_config_data"]["temperature"] == 0.2
    # A deep copy, not a mutation -- the original graph is untouched.
    assert graph["nodes"][0]["data"]["config"]["model_config_data"]["temperature"] == 0.7


def test_apply_factor_bindings_skips_factor_not_in_values() -> None:
    graph = {
        "nodes": [
            {
                "id": "w1",
                "type": "agent",
                "data": {
                    "config": {"model_config_data": {"temperature": 0.7}},
                    "factor_bindings": {"config.model_config_data.temperature": "Temperature"},
                },
            }
        ],
        "edges": [],
    }
    patched = apply_factor_bindings(graph, {"SomeOtherFactor": 1})
    assert patched["nodes"][0]["data"]["config"]["model_config_data"]["temperature"] == 0.7


def test_apply_factor_bindings_substitutes_critic_enabled_boolean() -> None:
    graph = {
        "nodes": [
            {
                "id": "g1",
                "type": "critic_gate",
                "data": {"config": {"enabled": True}, "factor_bindings": {"config.enabled": "CriticOn"}},
            }
        ],
        "edges": [],
    }
    patched = apply_factor_bindings(graph, {"CriticOn": False})
    assert patched["nodes"][0]["data"]["config"]["enabled"] is False


def test_apply_factor_bindings_swaps_the_whole_dataset_per_cell() -> None:
    """The dataset-as-factor path end to end, on the pure half: a Dataset
    node's whole `config` bound to a 'dataset_config' factor resolves to
    exactly ONE dataset per cell -- which is what keeps a cell's single
    workspace (keyed by experiment_id/cell_label) holding a single dataset,
    and what lets _resolve_node_dataset's len == 1 rule fire."""
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node(dataset_name="cohort-a", dataset_id="d1")
    dataset["data"]["factor_bindings"] = {"config": "Agent:Dataset:Dataset"}
    graph = {"nodes": [agent, dataset], "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")]}

    for name, dataset_id in (("cohort-a", "d1"), ("cohort-b", "d2")):
        level = {"dataset_id": dataset_id, "dataset_name": name, "enabled": True}
        patched = apply_factor_bindings(graph, {"Agent:Dataset:Dataset": level})
        configs = pe._resolve_dataset_configs(patched, "a")
        assert [c["dataset_name"] for c in configs] == [name]

    # And the base graph is untouched, so the next cell starts from the same
    # place (apply_factor_bindings deep-copies).
    assert [c["dataset_name"] for c in pe._resolve_dataset_configs(graph, "a")] == ["cohort-a"]


def test_apply_factor_bindings_varies_the_tool_allow_list_but_not_the_server() -> None:
    """The tools-as-factor path on the pure half: an MCP node's
    `config.tool_names` bound to a 'tool_names' factor changes which of that
    ONE server's tools reach the agent, still namespaced, while server_names
    stays put across every level -- that invariant is what lets the canvas
    keep printing one server name for the node."""
    agent, agent_llm_edge = _agent_with_llm("a")
    tool = _tool_node(server_name="srv", tool_names=["do_thing", "do_other"])
    tool["data"]["factor_bindings"] = {"config.tool_names": "Agent:Tool:Tools allowed"}
    graph = {"nodes": [agent, tool], "edges": [agent_llm_edge, _tool_edge("tool1", "a")]}

    both = apply_factor_bindings(graph, {"Agent:Tool:Tools allowed": ["do_thing", "do_other"]})
    assert pe._resolve_tool_config(both, "a") == {
        "server_names": ["srv"],
        "tool_names": ["srv.do_thing", "srv.do_other"],
    }

    one = apply_factor_bindings(graph, {"Agent:Tool:Tools allowed": ["do_thing"]})
    assert pe._resolve_tool_config(one, "a") == {"server_names": ["srv"], "tool_names": ["srv.do_thing"]}

    # An empty allow-list is a real level: the server still connects (so
    # server_names is unchanged), it just contributes no tools to that cell.
    none = apply_factor_bindings(graph, {"Agent:Tool:Tools allowed": []})
    assert pe._resolve_tool_config(none, "a") == {"server_names": ["srv"], "tool_names": []}

    # And the base graph is untouched (apply_factor_bindings deep-copies).
    assert pe._resolve_tool_config(graph, "a")["tool_names"] == ["srv.do_thing", "srv.do_other"]


def test_sink_node_ids_linear_chain_single_sink() -> None:
    graph = _graph(["a", "b", "c"], [("a", "b"), ("b", "c")])
    assert sink_node_ids(graph) == ["c"]


def test_sink_node_ids_fanout_multiple_sinks() -> None:
    graph = _graph(["a", "b", "c"], [("a", "b"), ("a", "c")])
    assert set(sink_node_ids(graph)) == {"b", "c"}


# --- plan_cell_runs / run_protocol cell writeback (real Postgres) -----------


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def owner_id() -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        user = User(
            email=f"protocol-exec-test-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="Protocol Execution Test User",
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        uid = user.id
    yield uid
    async with get_session() as db:
        db_user = await db.get(User, uid)
        if db_user is not None:
            await db.delete(db_user)


async def test_plan_cell_runs_raises_without_experiment(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    async with get_session() as db:
        with pytest.raises(ProtocolValidationError, match="no linked experiment"):
            await plan_cell_runs(db, protocol_id=uuid.uuid4(), experiment_id=None, owner_id=owner_id, graph=graph)


async def test_plan_cell_runs_raises_on_multi_sink_graph(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b", "c"], [("a", "b"), ("a", "c")])
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"cell-run-multisink-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
    try:
        async with get_session() as db:
            with pytest.raises(ProtocolValidationError, match="exactly one final node"):
                await plan_cell_runs(
                    db, protocol_id=uuid.uuid4(), experiment_id=experiment_id, owner_id=owner_id, graph=graph
                )
    finally:
        async with get_session() as db:
            await delete_experiment(db, experiment_id)


async def test_plan_cell_runs_creates_one_run_per_pending_cell_skips_scored(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"cell-run-pending-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db, name=f"cell-run-pending-protocol-{uuid.uuid4().hex}", owner_id=owner_id, experiment_id=experiment_id
        )
        protocol_id = protocol.id
        await upsert_replicate(
            db, experiment_id=experiment_id, replicate_label="cell-1", fields={"factor_values": {"x": 1}}
        )
        await upsert_replicate(
            db, experiment_id=experiment_id, replicate_label="cell-2", fields={"factor_values": {"x": 2}}
        )
        # Already scored -- must be skipped, not re-run/re-billed.
        await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="cell-3",
            fields={"factor_values": {"x": 3}, "metric_values": {"roc_auc": 0.9}},
        )

    try:
        async with get_session() as db:
            runs, skipped = await plan_cell_runs(
                db, protocol_id=protocol_id, experiment_id=experiment_id, owner_id=owner_id, graph=graph
            )
        assert skipped == 1
        assert {run.replicate_label for run in runs} == {"cell-1", "cell-2"}
        assert all(r.protocol_id == protocol_id for r in runs)
        by_label = {run.replicate_label: run for run in runs}
        assert by_label["cell-1"].factor_values == {"x": 1}
        assert by_label["cell-2"].factor_values == {"x": 2}
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRuns
            await delete_experiment(db, experiment_id)


async def test_plan_cell_runs_skips_and_allows_rerun_of_completed_unscored_replicate(owner_id: uuid.UUID) -> None:
    """A successful qualitative run is completed even without numeric metrics.

    It must not be re-run and re-billed by a later batch by default, but the
    caller can deliberately select it from the prior-runs list.
    """
    graph = _graph(["a", "b"], [("a", "b")])
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"cell-run-completed-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db, name=f"cell-run-completed-protocol-{uuid.uuid4().hex}", owner_id=owner_id, experiment_id=experiment_id
        )
        protocol_id = protocol.id
        completed_run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        completed_run.status = "completed"
        await db.flush()
        await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="cell-completed-without-metrics",
            fields={"factor_values": {"x": 1}, "run_id": completed_run.id},
        )

    try:
        async with get_session() as db:
            runs, skipped = await plan_cell_runs(
                db, protocol_id=protocol_id, experiment_id=experiment_id, owner_id=owner_id, graph=graph
            )
        assert runs == []
        assert skipped == 1

        async with get_session() as db:
            runs, skipped = await plan_cell_runs(
                db,
                protocol_id=protocol_id,
                experiment_id=experiment_id,
                owner_id=owner_id,
                graph=graph,
                rerun_replicate_labels={"cell-completed-without-metrics"},
            )
        assert [run.replicate_label for run in runs] == ["cell-completed-without-metrics"]
        assert skipped == 0
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
            await delete_experiment(db, experiment_id)


async def test_plan_cell_runs_runs_an_obsolete_completed_replicate(owner_id: uuid.UUID) -> None:
    """A completed result from an older canvas revision is not a prior result.

    It must not appear in the selectable prior-runs set or prevent the
    replicate from running against the newly published canvas.
    """
    graph = _graph(["a", "b"], [("a", "b")])
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"cell-run-obsolete-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db,
            name=f"cell-run-obsolete-protocol-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment_id,
            graph=graph,
        )
        protocol_id = protocol.id
        old_revision = await publish_protocol(db, protocol)
        completed_run = await create_protocol_run(
            db,
            protocol_id=protocol_id,
            owner_id=owner_id,
            protocol_revision_id=old_revision.id,
        )
        completed_run.status = "completed"
        new_revision = await publish_protocol(db, protocol)
        await db.flush()
        await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="cell-obsolete",
            fields={"factor_values": {"x": 1}, "run_id": completed_run.id},
        )

    try:
        async with get_session() as db:
            runs, skipped = await plan_cell_runs(
                db,
                protocol_id=protocol_id,
                experiment_id=experiment_id,
                owner_id=owner_id,
                graph=graph,
                protocol_revision_id=new_revision.id,
            )
        assert [run.replicate_label for run in runs] == ["cell-obsolete"]
        assert skipped == 0
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
            await delete_experiment(db, experiment_id)


async def test_plan_cell_runs_ignores_a_superseded_design(owner_id: uuid.UUID) -> None:
    """The bug this whole revision model exists for: a design shrunk from 6
    cells to 2 used to launch 6 runs, because the 4 the new design dropped
    were still sitting under the experiment."""
    graph = _graph(["a", "b"], [("a", "b")])
    factors_wide = [{"name": "tier", "levels": ["s", "l"]}, {"name": "effort", "levels": ["lo", "mid", "hi"]}]
    factors_narrow = [{"name": "tier", "levels": ["s", "l"]}, {"name": "effort", "levels": ["lo"]}]
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"cell-run-superseded-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db, name=f"cell-run-superseded-protocol-{uuid.uuid4().hex}", owner_id=owner_id, experiment_id=experiment_id
        )
        protocol_id = protocol.id
        await generate_design_cells(db, experiment_id=experiment_id, factors=factors_wide)
    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=factors_narrow)

    try:
        async with get_session() as db:
            runs, skipped = await plan_cell_runs(
                db, protocol_id=protocol_id, experiment_id=experiment_id, owner_id=owner_id, graph=graph
            )
        assert len(runs) == 2  # not 6
        assert skipped == 0
        # Every run is pinned to the design it was planned under, so a
        # regenerate mid-flight can't redirect where its result lands.
        assert len({r.design_revision_id for r in runs}) == 1
        assert runs[0].design_revision_id is not None
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRuns
            await delete_experiment(db, experiment_id)


async def test_plan_single_replicate_run_raises_without_experiment(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    async with get_session() as db:
        with pytest.raises(ProtocolValidationError, match="no linked experiment"):
            await plan_single_replicate_run(
                db,
                protocol_id=uuid.uuid4(),
                experiment_id=None,
                owner_id=owner_id,
                graph=graph,
                replicate_label="cell-1",
            )


async def test_plan_single_replicate_run_raises_on_multi_sink_graph(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b", "c"], [("a", "b"), ("a", "c")])
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"single-cell-multisink-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
    try:
        async with get_session() as db:
            with pytest.raises(ProtocolValidationError, match="exactly one final node"):
                await plan_single_replicate_run(
                    db,
                    protocol_id=uuid.uuid4(),
                    experiment_id=experiment_id,
                    owner_id=owner_id,
                    graph=graph,
                    replicate_label="cell-1",
                )
    finally:
        async with get_session() as db:
            await delete_experiment(db, experiment_id)


async def test_plan_single_replicate_run_raises_on_unknown_replicate_label(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"single-cell-unknown-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db, name=f"single-cell-unknown-protocol-{uuid.uuid4().hex}", owner_id=owner_id, experiment_id=experiment_id
        )
        protocol_id = protocol.id
    try:
        async with get_session() as db:
            with pytest.raises(ProtocolValidationError, match="No such replicate"):
                await plan_single_replicate_run(
                    db,
                    protocol_id=protocol_id,
                    experiment_id=experiment_id,
                    owner_id=owner_id,
                    graph=graph,
                    replicate_label="does-not-exist",
                )
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
            await delete_experiment(db, experiment_id)


async def test_plan_single_replicate_run_does_not_skip_an_already_scored_replicate(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"single-cell-scored-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db, name=f"single-cell-scored-protocol-{uuid.uuid4().hex}", owner_id=owner_id, experiment_id=experiment_id
        )
        protocol_id = protocol.id
        # Already scored -- plan_cell_runs would skip this one; picking it by
        # name is a deliberate re-run, so plan_single_replicate_run must not.
        await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="cell-1",
            fields={"factor_values": {"x": 1}, "metric_values": {"roc_auc": 0.9}},
        )

    try:
        async with get_session() as db:
            run = await plan_single_replicate_run(
                db,
                protocol_id=protocol_id,
                experiment_id=experiment_id,
                owner_id=owner_id,
                graph=graph,
                replicate_label="cell-1",
            )
        assert run.protocol_id == protocol_id
        assert run.replicate_label == "cell-1"
        assert run.factor_values == {"x": 1}
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun
            await delete_experiment(db, experiment_id)


async def test_run_protocol_substitutes_factor_and_writes_back_to_cell(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end (minus the actual LLM call): a run created with
    cell_label/factor_values set gets the substituted value resolvable via
    the worker's Model connector, and the sink node's output lands on the
    right replicate via the real upsert_replicate -- proves apply_factor_bindings is
    actually wired into run_protocol, not just correct in isolation. Model
    config lives on the connected `llm` node now, not the agent's own
    config -- the factor binding targets that node instead."""
    received_configs = []
    received_workspace_ids = []

    async def fake_run_agent_node(node, *, graph, workspace_id=None, **kwargs):
        received_configs.append(pe._resolve_model_config(graph, node["id"]))
        received_workspace_ids.append(workspace_id)
        return f"output for {node['id']}", None, None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    async with get_session() as db:
        experiment = await create_experiment(db, name=f"cell-run-e2e-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db,
            name=f"cell-run-e2e-protocol-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment_id,
            graph={
                "nodes": [
                    {
                        "id": "llm1",
                        "type": "model_anthropic",
                        "data": {
                            "config": {"temperature": 0.9},
                            "factor_bindings": {"config.temperature": "Temperature"},
                        },
                    },
                    {"id": "worker", "type": "agent", "data": {"config": {}}},
                ],
                "edges": [{"id": "llm1-worker", "source": "llm1", "target": "worker", "targetHandle": "model"}],
            },
        )
        protocol_id = protocol.id
        replicate = await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="only-cell",
            fields={"factor_values": {"Temperature": 0.1}},
        )
        run = await create_protocol_run(
            db,
            protocol_id=protocol_id,
            owner_id=owner_id,
            replicate_label="only-cell",
            factor_values={"Temperature": 0.1},
            # Claims the replicate slot, exactly as plan_cell_runs does. Without
            # it run_protocol's write-back is correctly skipped:
            # is_current_replicate_attempt reads run.replicate_result_id to decide
            # whether this run still owns the slot's latest projection.
            replicate_result_id=replicate.id,
        )
        run_id = run.id

    try:
        await pe.run_protocol(run_id)

        assert received_configs[0]["temperature"] == 0.1
        assert received_workspace_ids[0] == f"{experiment_id}/only-cell"

        async with get_session() as db:
            replicate = await get_replicate(db, experiment_id=experiment_id, replicate_label="only-cell")
            assert replicate is not None
            assert replicate.run_id == run_id
            assert replicate.factor_values == {"Temperature": 0.1}
            assert replicate.workspace_id == f"{experiment_id}/only-cell"
            assert replicate.artifacts is not None
            assert replicate.artifacts["output_text"] == "output for worker"
            assert replicate.artifacts["protocol_run_id"] == str(run_id)
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun
            await delete_experiment(db, experiment_id)


# --- node deactivate passthrough (pure helpers + wired-in run_protocol) -----


def test_upstream_output_text_no_upstream_is_empty() -> None:
    graph = _graph(["a"], [])
    assert pe._upstream_output_text(graph, "a", {}) == ""


def test_upstream_output_text_joins_completed_upstream_nodes() -> None:
    graph = _graph(["a", "b", "c"], [("a", "c"), ("b", "c")])
    node_runs = {
        "a": {"status": "completed", "output_text": "from a"},
        "b": {"status": "completed", "output_text": "from b"},
    }
    assert pe._upstream_output_text(graph, "c", node_runs) == "from a\n\nfrom b"


def test_upstream_output_text_skips_upstream_with_no_output() -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    assert pe._upstream_output_text(graph, "b", {"a": {"status": "failed", "output_text": None}}) == ""


# --- workspace_id computation (pure) -----------------------------------------


def test_compute_workspace_id_for_a_real_cell_run() -> None:
    experiment_id = uuid.uuid4()
    protocol_run_id = uuid.uuid4()
    assert pe._compute_workspace_id(experiment_id, "tier_a__rep_0", protocol_run_id) == f"{experiment_id}/tier_a__rep_0"


def test_compute_workspace_id_adhoc_when_no_cell_label() -> None:
    # A manual "Run" click or single-node Play on an experiment-linked
    # protocol -- still gets a stable, per-run workspace so accept/reset
    # semantics still make sense outside the factorial grid.
    experiment_id = uuid.uuid4()
    protocol_run_id = uuid.uuid4()
    assert pe._compute_workspace_id(experiment_id, None, protocol_run_id) == f"{experiment_id}/adhoc-{protocol_run_id}"


def test_compute_workspace_id_none_without_experiment_id() -> None:
    # An unlinked protocol has no dataset to seed a workspace from.
    assert pe._compute_workspace_id(None, "some-cell", uuid.uuid4()) is None


def test_compute_workspace_id_sanitizes_a_real_cell_label() -> None:
    # Regression test: a real cell_label is built from free-text design_spec
    # factor names (e.g. "Azure Foundry:Model", "Critic enabled") and can
    # contain spaces/colons that asaree_workspace_core's own _SAFE_COMPONENT
    # regex rejects. Before this fix, _compute_workspace_id passed the raw
    # label through unsanitized, so run_model_script (which resolves
    # workspace_id purely from ambient _meta) looked for a workspace
    # directory that was never created under that exact raw name -- DC/FTE/
    # FS/MLM completed fine (their agents improvised their own sanitized
    # cell_label when calling open_workspace), but every Score stage failed
    # with "workspace not initialized".
    experiment_id = uuid.uuid4()
    protocol_run_id = uuid.uuid4()
    raw_label = "Azure Foundry:Effort_medium__Azure Foundry:Model_claude-sonnet-5__Critic enabled_false"
    expected = "Azure_Foundry_Effort_medium__Azure_Foundry_Model_claude-sonnet-5__Critic_enabled_false"
    assert pe._compute_workspace_id(experiment_id, raw_label, protocol_run_id) == f"{experiment_id}/{expected}"


def test_default_system_prompt_uses_the_node_own_label() -> None:
    # ASAREE's own explicit default -- never Motoro's own fallback,
    # which would use the internal "protocol-{id}-{id}" agent_name instead.
    assert pe._default_system_prompt("SF-DC", "Agent") == "You are SF-DC."


def test_default_system_prompt_falls_back_to_the_placeholder_when_unlabeled() -> None:
    assert pe._default_system_prompt(None, "Agent") == "You are Agent."
    assert pe._default_system_prompt("", "Critic Gate") == "You are Critic Gate."


def test_is_node_active_defaults_true_when_absent() -> None:
    assert pe._is_node_active(_node("a", "agent")) is True


def test_is_node_active_false_when_explicitly_deactivated() -> None:
    node = _node("a", "agent")
    node["data"]["active"] = False
    assert pe._is_node_active(node) is False


def test_deactivated_gated_worker_raises() -> None:
    worker = _node("w1", "agent")
    worker["data"]["active"] = False
    graph = {
        "nodes": [worker, _node("g1", "critic_gate")],
        "edges": _edges(("w1", "g1")),
    }
    with pytest.raises(ProtocolValidationError, match="can't be deactivated"):
        topological_order(graph)


async def test_run_protocol_deactivated_node_passes_through(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deactivated middle node never calls _run_agent_node -- its
    node_runs output_text is the upstream node's output, verbatim. Real
    Postgres rows (same convention as every other run_protocol-level test
    in this file), only the LLM call itself is mocked."""
    call_count = 0

    async def fake_run_agent_node(node, **kwargs):
        nonlocal call_count
        call_count += 1
        return f"real output from {node['id']}", None, None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    middle = _node("b", "agent")
    middle["data"]["active"] = False
    llm = _llm_node()
    graph = {
        "nodes": [llm, _node("a", "agent"), middle, _node("c", "agent")],
        "edges": _edges(("a", "b"), ("b", "c"))
        + [_llm_edge(llm["id"], "a"), _llm_edge(llm["id"], "b"), _llm_edge(llm["id"], "c")],
    }

    async with get_session() as db:
        protocol = await create_protocol(db, name=f"deactivate-test-{uuid.uuid4().hex}", owner_id=owner_id, graph=graph)
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    try:
        await pe.run_protocol(run_id)

        assert call_count == 2  # only "a" and "c" -- "b" is deactivated

        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "completed"
            assert fetched.node_runs["b"]["status"] == "completed"
            assert fetched.node_runs["b"]["output_text"] == "real output from a"
            assert fetched.node_runs["c"]["output_text"] == "real output from c"
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun


def test_the_extraction_fragment_carries_only_what_there_is() -> None:
    """A node run gains a key only when there is something in it, so the
    presence of `payload`/`caveats` is itself the answer to "did the parser
    produce anything" -- no parser at all and a parser that produced nothing
    both read as absence, which they are."""
    from motoro.schemas.output import OutputEnvelope

    assert pe._extraction_fields(None) is None
    assert pe._extraction_fields(OutputEnvelope(result="x")) is None
    assert pe._extraction_fields(OutputEnvelope(result="x", payload={"n": 1})) == {"payload": {"n": 1}}
    assert pe._extraction_fields(OutputEnvelope(result="x", caveats=["guessed"])) == {"caveats": ["guessed"]}
    # Both together: the extractor can coerce a field and still say it guessed.
    assert pe._extraction_fields(OutputEnvelope(result="x", payload={"n": 1}, caveats=["guessed"])) == {
        "payload": {"n": 1},
        "caveats": ["guessed"],
    }


def test_an_all_null_payload_is_flagged_as_a_failed_read() -> None:
    """The extractor's model types every contracted field `T | None` and may not
    infer, so "found nothing" and "the answer had none of these" produce the
    same all-null object -- which the UI would otherwise show as a tidy list of
    results whose answer is null. One null among real values is a real partial
    reading and says nothing."""
    from motoro.schemas.output import OutputEnvelope

    fields = pe._extraction_fields(OutputEnvelope(result="x", payload={"a": None, "b": None}))
    assert fields is not None
    assert fields["payload"] == {"a": None, "b": None}
    assert fields["caveats"] == [pe._EMPTY_PAYLOAD_CAVEAT]

    # Kept alongside whatever the extractor already said, not instead of it.
    both = pe._extraction_fields(OutputEnvelope(result="x", payload={"a": None}, caveats=["guessed"]))
    assert both is not None
    assert both["caveats"] == ["guessed", pe._EMPTY_PAYLOAD_CAVEAT]

    # A contract that declared nothing has nothing to warn about, and a payload
    # with any real value in it was a successful read.
    assert pe._extraction_fields(OutputEnvelope(result="x", payload={})) == {"payload": {}}
    assert pe._extraction_fields(OutputEnvelope(result="x", payload={"a": None, "b": 2})) == {
        "payload": {"a": None, "b": 2}
    }


def test_a_ceiling_truncated_run_is_flagged_even_though_it_completed() -> None:
    """Motoro reports a cap-exhausted Reason+Act run as `completed` with the
    last tool result as its output, so the only thing that distinguishes it
    from a finished run is the loop summary the pattern persists."""

    class _Run:
        def __init__(self, overrides: dict[str, object] | None) -> None:
            self.pattern_overrides = overrides

    hit = {
        "reason_act_state": {
            "iterations": 15,
            "max_iterations": 15,
            "max_iterations_hit": True,
            "terminated_by": "max_iterations",
        }
    }
    assert pe._truncation_fields(_Run(hit)) == {
        "truncation": {"reason": "max_iterations", "iterations": 15, "max_iterations": 15}
    }

    # An agent that decided it was done, a non-ReasonAct pattern, and a run
    # whose telemetry write lost its race all read as "nothing to say".
    done = {
        "reason_act_state": {
            "iterations": 2,
            "max_iterations": 15,
            "max_iterations_hit": False,
            "terminated_by": "final_answer",
        }
    }
    assert pe._truncation_fields(_Run(done)) is None
    assert pe._truncation_fields(_Run({"other_pattern_state": {}})) is None
    assert pe._truncation_fields(_Run(None)) is None


async def test_run_protocol_stores_the_extraction_beside_the_output_text(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alongside, never instead of: the prose handoff must not regress because
    someone connected a parser. A deactivated node's pass-through carries none
    of it -- the prose it forwards was read against a different node's
    contract."""

    async def fake_run_agent_node(node, **kwargs):
        return f"output from {node['id']}", None, None, {"payload": {"n_rows": 4300}, "caveats": ["guessed"]}

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    middle = _node("b", "agent")
    middle["data"]["active"] = False
    llm = _llm_node()
    graph = {
        "nodes": [llm, _node("a", "agent"), middle],
        "edges": _edges(("a", "b")) + [_llm_edge(llm["id"], "a"), _llm_edge(llm["id"], "b")],
    }

    async with get_session() as db:
        protocol = await create_protocol(db, name=f"payload-test-{uuid.uuid4().hex}", owner_id=owner_id, graph=graph)
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    try:
        await pe.run_protocol(run_id)
        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
        assert fetched is not None
        assert fetched.node_runs["a"]["output_text"] == "output from a"
        assert fetched.node_runs["a"]["payload"] == {"n_rows": 4300}
        assert fetched.node_runs["a"]["caveats"] == ["guessed"]
        assert "payload" not in fetched.node_runs["b"]
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)


async def test_run_protocol_honors_cancellation_between_nodes(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression/behavior test for Stop: cancellation is polled from the DB
    once per node boundary, not mid-node -- the node that's already in
    flight when cancel_requested_at gets set (here, "a") still runs to
    completion; only the nodes after it ("b", "c") are skipped, and the
    overall run lands on "cancelled" rather than "completed"."""
    call_count = 0

    async def fake_run_agent_node(node, **kwargs):
        nonlocal call_count
        call_count += 1
        if node["id"] == "a":
            # Simulates a concurrent Stop click landing while "a" is still
            # running -- a genuinely separate request/transaction in real
            # usage, modeled here as a second, independent session.
            async with get_session() as db:
                await request_protocol_run_cancellation(db, run_id)
        return f"output from {node['id']}", None, None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    llm = _llm_node()
    graph = {
        "nodes": [llm, _node("a", "agent"), _node("b", "agent"), _node("c", "agent")],
        "edges": _edges(("a", "b"), ("b", "c"))
        + [_llm_edge(llm["id"], "a"), _llm_edge(llm["id"], "b"), _llm_edge(llm["id"], "c")],
    }

    async with get_session() as db:
        protocol = await create_protocol(db, name=f"cancel-test-{uuid.uuid4().hex}", owner_id=owner_id, graph=graph)
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    try:
        await pe.run_protocol(run_id)

        assert call_count == 1  # only "a" -- "b" and "c" are skipped once cancellation is seen

        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "cancelled"
            assert fetched.node_runs["a"]["status"] == "completed"
            assert fetched.node_runs["a"]["output_text"] == "output from a"
            assert fetched.node_runs["b"] == {"status": "skipped"}
            assert fetched.node_runs["c"] == {"status": "skipped"}
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun


async def test_run_protocol_honors_mid_node_cancellation(owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    """Distinct from the between-nodes test above: this simulates a Stop
    click landing WHILE "a" is still executing (Motoro's own
    cancel_event interrupts it mid-phase, see _execute_run_cancellable),
    represented here by _run_agent_node returning the _AGENT_CANCELLED
    sentinel directly rather than a real output. "a" itself must be
    recorded "cancelled" (not "completed" with blank output, and not
    "failed"), and "b"/"c" are still skipped via the same cancelled flag."""
    call_count = 0

    async def fake_run_agent_node(node, **kwargs):
        nonlocal call_count
        call_count += 1
        if node["id"] == "a":
            return None, pe._AGENT_CANCELLED, uuid.uuid4(), None
        return f"output from {node['id']}", None, None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    llm = _llm_node()
    graph = {
        "nodes": [llm, _node("a", "agent"), _node("b", "agent"), _node("c", "agent")],
        "edges": _edges(("a", "b"), ("b", "c"))
        + [_llm_edge(llm["id"], "a"), _llm_edge(llm["id"], "b"), _llm_edge(llm["id"], "c")],
    }

    async with get_session() as db:
        protocol = await create_protocol(
            db, name=f"mid-node-cancel-test-{uuid.uuid4().hex}", owner_id=owner_id, graph=graph
        )
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    try:
        await pe.run_protocol(run_id)

        assert call_count == 1  # only "a" -- interrupted mid-flight, "b"/"c" never start

        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "cancelled"
            assert fetched.node_runs["a"]["status"] == "cancelled"
            assert fetched.node_runs["a"]["output_text"] is None
            assert fetched.node_runs["b"] == {"status": "skipped"}
            assert fetched.node_runs["c"] == {"status": "skipped"}
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun


async def test_monitor_protocol_run_sets_event_once_cancellation_requested(owner_id: uuid.UUID) -> None:
    """Isolated test of the poller itself, not the whole run_protocol path
    -- confirms it actually notices a cancellation raised on the row (by a
    separate request, modeled here as a separate session) and sets the
    event. Uses a short interval so the test doesn't sleep the production
    1.5s each time."""
    async with get_session() as db:
        protocol = await create_protocol(db, name=f"poll-test-{uuid.uuid4().hex}", owner_id=owner_id)
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    try:
        cancel_event = asyncio.Event()
        poller = asyncio.create_task(pe._monitor_protocol_run(run_id, cancel_event, interval=0.05))
        await asyncio.sleep(0.15)
        assert not cancel_event.is_set()  # nothing requested yet -- poller shouldn't fire spuriously

        async with get_session() as db:
            await request_protocol_run_cancellation(db, run_id)

        await asyncio.wait_for(poller, timeout=1.0)
        assert cancel_event.is_set()
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun


# --- Model / Tool / Memory connector validation (pure) -------------------------


def test_agent_missing_llm_connection_raises() -> None:
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    with pytest.raises(ProtocolValidationError, match="exactly one Model connection"):
        topological_order(graph)


def test_agent_duplicate_llm_connection_raises() -> None:
    llm1, llm2 = _llm_node("llm1"), _llm_node("llm2")
    graph = {
        "nodes": [llm1, llm2, _node("a", "agent")],
        "edges": [_llm_edge("llm1", "a"), _llm_edge("llm2", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="exactly one Model connection"):
        topological_order(graph)


def test_critic_gate_missing_llm_connection_raises() -> None:
    llm = _llm_node()
    worker, worker_llm_edge = _agent_with_llm("w1")
    graph = {
        "nodes": [llm, worker, _node("g1", "critic_gate")],
        "edges": [worker_llm_edge, {"id": "w1-g1", "source": "w1", "target": "g1"}],
    }
    with pytest.raises(ProtocolValidationError, match="exactly one Model connection"):
        topological_order(graph)


@pytest.mark.parametrize("legacy_handle", ["ai", "llm"])
def test_legacy_model_handle_still_resolves(legacy_handle: str) -> None:
    # The Model connector's handles were "llm" and then "ai" before "model".
    # Data migrations rewrite stored graphs, but an un-migrated edge --
    # or one autosaved by a browser tab still running the pre-rename JS --
    # must resolve identically: same wiring, same model config, no "exactly
    # one Model connection" error from the edge being read as a main pipeline
    # edge instead.
    llm = _llm_node(config={"provider": "anthropic", "model": "claude-sonnet-4-5"})
    agent = _node("a", "agent")
    graph = {
        "nodes": [llm, agent],
        "edges": [_llm_edge("llm", "a", handle=legacy_handle)],
    }
    assert [n["id"] for n in topological_order(graph)] == ["llm", "a"]
    assert pe._resolve_model_config(graph, "a")["model"] == "claude-sonnet-4-5"


def test_llm_connection_from_non_llm_source_raises() -> None:
    graph = {
        "nodes": [_node("t1", "step"), _node("a", "agent")],
        "edges": [{"id": "t1-a-ai", "source": "t1", "target": "a", "targetHandle": "model"}],
    }
    with pytest.raises(ProtocolValidationError, match="must come from a Model node"):
        topological_order(graph)


def test_tool_connection_from_non_mcp_tool_source_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _node("b", "agent")],
        "edges": [agent_llm_edge, _tool_edge("b", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="must come from an MCP Tool or Script node"):
        topological_order(graph)


def test_tool_connection_on_critic_gate_raises() -> None:
    llm = _llm_node()
    worker, worker_llm_edge = _agent_with_llm("w1")
    gate_llm_edge = _llm_edge("llm", "g1")
    tool = _tool_node()
    graph = {
        "nodes": [llm, worker, _node("g1", "critic_gate"), tool],
        "edges": [
            worker_llm_edge,
            gate_llm_edge,
            {"id": "w1-g1", "source": "w1", "target": "g1"},
            _tool_edge("tool1", "g1"),
        ],
    }
    with pytest.raises(
        ProtocolValidationError,
        match="Only Agent nodes can have a Tool, Memory, Architectural Pattern, Skill, Dataset, ",
    ):
        topological_order(graph)


def test_multiple_memory_connections_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    mem1, mem2 = _memory_node("m1"), _memory_node("m2")
    graph = {
        "nodes": [llm, agent, mem1, mem2],
        "edges": [agent_llm_edge, _memory_edge("m1", "a"), _memory_edge("m2", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="at most one Memory connection"):
        topological_order(graph)


def test_memory_connection_from_non_memory_source_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _node("b", "agent")],
        "edges": [agent_llm_edge, _memory_edge("b", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="must come from a Memory node"):
        topological_order(graph)


def test_mcp_tool_node_with_plain_outgoing_edge_raises() -> None:
    # An mcp_tool node is always a Tool-connector source -- there's no more
    # "standalone pipeline step" role, so a plain edge out of one (even
    # alongside a real Tool connection) is rejected the same way an LLM/
    # Memory/Pattern node's plain edge already is.
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    tool = _tool_node()
    graph = {
        "nodes": [llm, agent, tool, _node("b", "agent")],
        "edges": [agent_llm_edge, _tool_edge("tool1", "a"), {"id": "tool1-b", "source": "tool1", "target": "b"}],
    }
    with pytest.raises(ProtocolValidationError, match="Tool node .* can only connect to a node's Tool slot"):
        topological_order(graph)


def test_llm_node_with_plain_outgoing_edge_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _node("b", "agent")],
        "edges": [agent_llm_edge, {"id": "llm-b", "source": "llm", "target": "b"}],
    }
    with pytest.raises(ProtocolValidationError, match="Model node .* can only connect to a node's Model slot"):
        topological_order(graph)


def test_memory_node_with_plain_outgoing_edge_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    memory = _memory_node()
    graph = {
        "nodes": [llm, agent, memory, _node("b", "agent")],
        "edges": [agent_llm_edge, {"id": "memory-b", "source": "memory", "target": "b"}],
    }
    with pytest.raises(ProtocolValidationError, match="Memory node .* can only connect to a node's Memory slot"):
        topological_order(graph)


def test_multiple_dataset_connections_are_allowed() -> None:
    # Uncapped, like Skill and Knowledge: comparing a model across datasets
    # (or joining two tables) is ordinary science, and the old one-dataset cap
    # made it unexpressible.
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    ds1 = _dataset_node("ds1", dataset_name="cohort-a", dataset_id="d1")
    ds2 = _dataset_node("ds2", dataset_name="cohort-b", dataset_id="d2")
    graph = {
        "nodes": [llm, agent, ds1, ds2],
        "edges": [agent_llm_edge, _dataset_edge("ds1", "a"), _dataset_edge("ds2", "a")],
    }
    assert [n["id"] for n in topological_order(graph)] == ["llm", "ds1", "ds2", "a"]


def test_dataset_connections_split_across_legacy_and_current_handles_are_allowed() -> None:
    # A half-migrated graph (one old Tool-handle edge, one new Dataset one)
    # resolves to both datasets rather than tripping a cap.
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    ds1 = _dataset_node("ds1", dataset_name="cohort-a", dataset_id="d1")
    ds2 = _dataset_node("ds2", dataset_name="cohort-b", dataset_id="d2")
    graph = {
        "nodes": [llm, agent, ds1, ds2],
        "edges": [agent_llm_edge, _dataset_edge("ds1", "a"), _dataset_edge("ds2", "a", handle="tool")],
    }
    topological_order(graph)
    assert [c["dataset_name"] for c in pe._resolve_dataset_configs(graph, "a")] == ["cohort-a", "cohort-b"]


def test_dataset_connection_from_non_dataset_source_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _node("b", "agent")],
        "edges": [agent_llm_edge, _dataset_edge("b", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="Dataset connection must come from a Dataset node"):
        topological_order(graph)


def test_dataset_connection_on_critic_gate_raises() -> None:
    llm = _llm_node()
    worker, worker_llm_edge = _agent_with_llm("w1")
    gate_llm_edge = _llm_edge("llm", "g1")
    dataset = _dataset_node()
    graph = {
        "nodes": [llm, worker, _node("g1", "critic_gate"), dataset],
        "edges": [
            worker_llm_edge,
            gate_llm_edge,
            {"id": "w1-g1", "source": "w1", "target": "g1"},
            _dataset_edge("dataset1", "g1"),
        ],
    }
    with pytest.raises(
        ProtocolValidationError,
        match="Only Agent nodes can have a Tool, Memory, Architectural Pattern, Skill, Dataset, ",
    ):
        topological_order(graph)


def test_dataset_node_with_plain_outgoing_edge_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node()
    graph = {
        "nodes": [llm, agent, dataset, _node("b", "agent")],
        "edges": [
            agent_llm_edge,
            _dataset_edge("dataset1", "a"),
            {"id": "dataset1-b", "source": "dataset1", "target": "b"},
        ],
    }
    with pytest.raises(ProtocolValidationError, match="Dataset node .* can only connect to a node's Dataset slot"):
        topological_order(graph)


def test_multiple_script_connections_are_allowed() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    s1, s2 = _script_node("s1"), _script_node("s2")
    graph = {
        "nodes": [llm, agent, s1, s2],
        "edges": [agent_llm_edge, _script_edge("s1", "a"), _script_edge("s2", "a")],
    }
    assert topological_order(graph)


def test_script_connection_from_non_script_source_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _node("b", "agent")],
        "edges": [agent_llm_edge, _script_edge("b", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="must come from an MCP Tool or Script node"):
        topological_order(graph)


def test_script_connection_on_critic_gate_raises() -> None:
    llm = _llm_node()
    worker, worker_llm_edge = _agent_with_llm("w1")
    gate_llm_edge = _llm_edge("llm", "g1")
    script = _script_node()
    graph = {
        "nodes": [llm, worker, _node("g1", "critic_gate"), script],
        "edges": [
            worker_llm_edge,
            gate_llm_edge,
            {"id": "w1-g1", "source": "w1", "target": "g1"},
            _script_edge("script1", "g1"),
        ],
    }
    with pytest.raises(
        ProtocolValidationError,
        match="Only Agent nodes can have a Tool, Memory, Architectural Pattern, Skill, Dataset, ",
    ):
        topological_order(graph)


def test_script_node_with_plain_outgoing_edge_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    script = _script_node()
    graph = {
        "nodes": [llm, agent, script, _node("b", "agent")],
        "edges": [
            agent_llm_edge,
            _script_edge("script1", "a"),
            {"id": "script1-b", "source": "script1", "target": "b"},
        ],
    }
    with pytest.raises(ProtocolValidationError, match="Script node .* can only connect to a node's Tool slot"):
        topological_order(graph)


def test_skill_connector_is_repeatable_and_uncapped() -> None:
    # Unlike Memory and the execution pattern (max 1), the Skill connector is
    # uncapped -- several skills on one agent is the
    # normal case, since each costs ~100 tokens of level-1 metadata until the
    # model actually opens it.
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    s1, s2, s3 = _skill_node("s1", "id1"), _skill_node("s2", "id2"), _skill_node("s3", "id3")
    graph = {
        "nodes": [llm, agent, s1, s2, s3],
        "edges": [agent_llm_edge, _skill_edge("s1", "a"), _skill_edge("s2", "a"), _skill_edge("s3", "a")],
    }
    assert {n["id"] for n in topological_order(graph)} == {"llm", "a", "s1", "s2", "s3"}


def test_skill_connection_from_non_skill_node_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _script_node()],
        "edges": [agent_llm_edge, _skill_edge("script1", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="Skill connection must come from a Skill node"):
        topological_order(graph)


def test_skill_connection_on_critic_gate_raises() -> None:
    llm = _llm_node()
    worker, worker_llm_edge = _agent_with_llm("w1")
    graph = {
        "nodes": [llm, worker, _node("g1", "critic_gate"), _skill_node()],
        "edges": [
            worker_llm_edge,
            _llm_edge("llm", "g1"),
            {"id": "w1-g1", "source": "w1", "target": "g1"},
            _skill_edge("skill1", "g1"),
        ],
    }
    with pytest.raises(
        ProtocolValidationError,
        match="Only Agent nodes can have a Tool, Memory, Architectural Pattern, Skill, Dataset, ",
    ):
        topological_order(graph)


def test_skill_node_with_plain_outgoing_edge_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _skill_node(), _node("b", "agent")],
        "edges": [
            agent_llm_edge,
            _skill_edge("skill1", "a"),
            {"id": "skill1-b", "source": "skill1", "target": "b"},
        ],
    }
    with pytest.raises(ProtocolValidationError, match="Skill node .* can only connect to a node's Skill slot"):
        topological_order(graph)


def test_skill_node_is_not_a_sink() -> None:
    # A pure config source never counts as a pipeline's final output, even
    # unwired -- otherwise a dangling Skill node breaks the one-sink rule.
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {"nodes": [llm, agent, _skill_node()], "edges": [agent_llm_edge, _skill_edge("skill1", "a")]}
    assert pe.sink_node_ids(graph) == ["a"]


def test_resolve_skill_config_collects_ids_in_wiring_order() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _skill_node("s1", "id-a"), _skill_node("s2", "id-b")],
        "edges": [agent_llm_edge, _skill_edge("s1", "a"), _skill_edge("s2", "a")],
    }
    assert pe._resolve_skill_config(graph, "a") == {"skill_ids": ["id-a", "id-b"]}


def test_resolve_skill_config_dedupes_and_skips_disabled_and_unset() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    duplicate = _skill_node("s2", "id-a")
    disabled = _skill_node("s3", "id-c")
    disabled["data"]["config"]["enabled"] = False
    unset = _skill_node("s4", "")
    graph = {
        "nodes": [llm, agent, _skill_node("s1", "id-a"), duplicate, disabled, unset],
        "edges": [
            agent_llm_edge,
            _skill_edge("s1", "a"),
            _skill_edge("s2", "a"),
            _skill_edge("s3", "a"),
            _skill_edge("s4", "a"),
        ],
    }
    assert pe._resolve_skill_config(graph, "a") == {"skill_ids": ["id-a"]}


def test_resolve_skill_config_empty_with_nothing_wired() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {"nodes": [llm, agent], "edges": [agent_llm_edge]}
    assert pe._resolve_skill_config(graph, "a") == {}


def test_knowledge_connector_is_repeatable_and_uncapped() -> None:
    # Uncapped like Skill/Tool: reading a shared team bundle while writing to
    # a personal one is a normal setup, not an ambiguity to resolve.
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    b1 = _okf_bundle_node("b1", "okf-bundle-one-11111111")
    b2 = _okf_bundle_node("b2", "okf-bundle-two-22222222")
    graph = {
        "nodes": [llm, agent, b1, b2],
        "edges": [agent_llm_edge, _knowledge_edge("b1", "a"), _knowledge_edge("b2", "a")],
    }
    assert {n["id"] for n in topological_order(graph)} == {"llm", "a", "b1", "b2"}


def test_knowledge_connection_from_non_bundle_node_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _skill_node()],
        "edges": [agent_llm_edge, _knowledge_edge("skill1", "a")],
    }
    with pytest.raises(
        ProtocolValidationError, match="Knowledge connection must come from an OKF Bundle or OKF Document node"
    ):
        topological_order(graph)


def test_knowledge_connection_on_critic_gate_raises() -> None:
    llm = _llm_node()
    worker, worker_llm_edge = _agent_with_llm("w1")
    graph = {
        "nodes": [llm, worker, _node("g1", "critic_gate"), _okf_bundle_node()],
        "edges": [
            worker_llm_edge,
            _llm_edge("llm", "g1"),
            {"id": "w1-g1", "source": "w1", "target": "g1"},
            _knowledge_edge("okf1", "g1"),
        ],
    }
    with pytest.raises(ProtocolValidationError, match="Only Agent nodes can have a Tool, Memory"):
        topological_order(graph)


def test_okf_bundle_node_with_plain_outgoing_edge_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _okf_bundle_node(), _node("b", "agent")],
        "edges": [
            agent_llm_edge,
            _knowledge_edge("okf1", "a"),
            {"id": "okf1-b", "source": "okf1", "target": "b"},
        ],
    }
    with pytest.raises(ProtocolValidationError, match="OKF Bundle node .* can only connect to a node's Knowledge slot"):
        topological_order(graph)


def test_okf_bundle_node_is_not_a_sink() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _okf_bundle_node()],
        "edges": [agent_llm_edge, _knowledge_edge("okf1", "a")],
    }
    assert pe.sink_node_ids(graph) == ["a"]


def test_resolve_knowledge_config_namespaces_tool_names() -> None:
    # The whole point of the resolver: gather_tools matches on
    # "{server}.{tool}", so a bare name silently starves the agent instead of
    # erroring.
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _okf_bundle_node("b1", "okf-bundle-spine-abc12345")],
        "edges": [agent_llm_edge, _knowledge_edge("b1", "a")],
    }
    assert pe._resolve_knowledge_config(graph, "a") == {
        "server_names": ["okf-bundle-spine-abc12345"],
        "tool_names": [
            "okf-bundle-spine-abc12345.list_concepts",
            "okf-bundle-spine-abc12345.read_concept",
        ],
        "tool_descriptions": {
            "okf-bundle-spine-abc12345.list_concepts": "Knowledge source: spine.",
            "okf-bundle-spine-abc12345.read_concept": "Knowledge source: spine.",
        },
    }


def test_resolve_knowledge_config_dedupes_and_skips_disabled_and_unset() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    duplicate = _okf_bundle_node("b2", "okf-bundle-spine-abc12345")
    disabled = _okf_bundle_node("b3", "okf-bundle-other-99999999")
    disabled["data"]["config"]["enabled"] = False
    unset = _okf_bundle_node("b4", "")
    graph = {
        "nodes": [
            llm,
            agent,
            _okf_bundle_node("b1", "okf-bundle-spine-abc12345"),
            duplicate,
            disabled,
            unset,
        ],
        "edges": [
            agent_llm_edge,
            _knowledge_edge("b1", "a"),
            _knowledge_edge("b2", "a"),
            _knowledge_edge("b3", "a"),
            _knowledge_edge("b4", "a"),
        ],
    }
    assert pe._resolve_knowledge_config(graph, "a")["server_names"] == ["okf-bundle-spine-abc12345"]


def test_resolve_knowledge_config_empty_with_nothing_wired() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {"nodes": [llm, agent], "edges": [agent_llm_edge]}
    assert pe._resolve_knowledge_config(graph, "a") == {"server_names": [], "tool_names": []}


def test_okf_document_node_fills_the_knowledge_connector() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _okf_document_node()],
        "edges": [agent_llm_edge, _knowledge_edge("doc1", "a")],
    }
    assert {n["id"] for n in topological_order(graph)} == {"llm", "a", "doc1"}
    assert pe.sink_node_ids(graph) == ["a"]


def test_resolve_knowledge_config_mixes_bundles_and_documents() -> None:
    # The two node types are interchangeable on this connector: both resolve
    # to a per-directory OKF server, so an agent can hold a shared bundle and
    # one uploaded concept at once.
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [
            llm,
            agent,
            _okf_bundle_node("b1", "okf-bundle-spine-abc12345"),
            _okf_document_node("d1", "okf-doc-spinal-cord-def45678", ["read_concept"]),
        ],
        "edges": [agent_llm_edge, _knowledge_edge("b1", "a"), _knowledge_edge("d1", "a")],
    }
    assert pe._resolve_knowledge_config(graph, "a") == {
        "server_names": ["okf-bundle-spine-abc12345", "okf-doc-spinal-cord-def45678"],
        "tool_names": [
            "okf-bundle-spine-abc12345.list_concepts",
            "okf-bundle-spine-abc12345.read_concept",
            "okf-doc-spinal-cord-def45678.read_concept",
        ],
        "tool_descriptions": {
            "okf-bundle-spine-abc12345.list_concepts": "Knowledge source: spine.",
            "okf-bundle-spine-abc12345.read_concept": "Knowledge source: spine.",
            "okf-doc-spinal-cord-def45678.read_concept": "Knowledge source: Spinal cord.",
        },
    }


def test_resolve_knowledge_config_skips_disabled_document() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    disabled = _okf_document_node("d1", "okf-doc-spinal-cord-def45678")
    disabled["data"]["config"]["enabled"] = False
    graph = {
        "nodes": [llm, agent, disabled],
        "edges": [agent_llm_edge, _knowledge_edge("d1", "a")],
    }
    assert pe._resolve_knowledge_config(graph, "a") == {"server_names": [], "tool_names": []}


def test_okf_document_node_with_plain_outgoing_edge_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _okf_document_node(), _node("b", "agent")],
        "edges": [
            agent_llm_edge,
            _knowledge_edge("doc1", "a"),
            {"id": "doc1-b", "source": "doc1", "target": "b"},
        ],
    }
    with pytest.raises(
        ProtocolValidationError, match="OKF Document node .* can only connect to a node's Knowledge slot"
    ):
        topological_order(graph)


def test_multiple_execution_pattern_connections_raises() -> None:
    # Capped at one -- execution_pattern is a single value, unlike Tool
    # (repeatable) or Memory (already max-1 for a different reason).
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    p1, p2 = _pattern_node("p1"), _pattern_node("p2")
    graph = {
        "nodes": [llm, agent, p1, p2],
        "edges": [agent_llm_edge, _pattern_edge("p1", "a"), _pattern_edge("p2", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="at most one execution-pattern connection"):
        topological_order(graph)


def test_architectural_pattern_connection_from_non_pattern_source_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _node("b", "agent")],
        "edges": [agent_llm_edge, _pattern_edge("b", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="must come from an Architectural Pattern node"):
        topological_order(graph)


def test_architectural_pattern_connection_on_critic_gate_raises() -> None:
    llm = _llm_node()
    worker, worker_llm_edge = _agent_with_llm("w1")
    gate_llm_edge = _llm_edge("llm", "g1")
    pattern = _pattern_node()
    graph = {
        "nodes": [llm, worker, _node("g1", "critic_gate"), pattern],
        "edges": [
            worker_llm_edge,
            gate_llm_edge,
            {"id": "w1-g1", "source": "w1", "target": "g1"},
            _pattern_edge("pattern", "g1"),
        ],
    }
    with pytest.raises(
        ProtocolValidationError,
        match="Only Agent nodes can have a Tool, Memory, Architectural Pattern, Skill, Dataset, ",
    ):
        topological_order(graph)


def test_architectural_pattern_node_with_plain_outgoing_edge_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    pattern = _pattern_node()
    graph = {
        "nodes": [llm, agent, pattern, _node("b", "agent")],
        "edges": [agent_llm_edge, {"id": "pattern-b", "source": "pattern", "target": "b"}],
    }
    with pytest.raises(
        ProtocolValidationError,
        match="Architectural Pattern node .* can only connect to a node's Architectural Pattern slot",
    ):
        topological_order(graph)


def test_valid_llm_tool_memory_wiring_passes() -> None:
    llm = _llm_node(config={"provider": "anthropic", "model": "claude-sonnet-5"})
    agent, agent_llm_edge = _agent_with_llm("a")
    tool = _tool_node()
    memory = _memory_node()
    pattern = _pattern_node()
    graph = {
        "nodes": [llm, agent, tool, memory, pattern],
        "edges": [agent_llm_edge, _tool_edge("tool1", "a"), _memory_edge("memory", "a"), _pattern_edge("pattern", "a")],
    }
    order = [n["id"] for n in topological_order(graph)]
    assert set(order) == {"llm", "a", "tool1", "memory", "pattern"}


def test_model_connection_accepts_every_provider_node_type() -> None:
    providers = ["anthropic", "openai", "azure_foundry", "openrouter", "local"]
    agents_and_edges = [_agent_with_llm(f"a{index}", llm_id=provider) for index, provider in enumerate(providers)]
    agents = [agent for agent, _edge in agents_and_edges]
    edges = [edge for _agent, edge in agents_and_edges]
    models = [
        {"id": provider, "type": f"model_{provider}", "data": {"label": "", "config": {}}}
        for provider in providers
    ]
    graph = {
        "nodes": [*agents, *models],
        "edges": edges,
    }
    order = [n["id"] for n in topological_order(graph)]
    assert set(order) == {*(agent["id"] for agent in agents), *providers}


def test_architectural_pattern_connection_accepts_any_pattern_node_type() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    baseline_pattern = {"id": "baseline", "type": "pattern_single_agent_baseline", "data": {"label": "", "config": {}}}
    graph = {
        "nodes": [llm, agent, baseline_pattern],
        "edges": [agent_llm_edge, _pattern_edge("baseline", "a")],
    }
    order = [n["id"] for n in topological_order(graph)]
    assert set(order) == {"llm", "a", "baseline"}


# --- Model / Tool connector resolution (pure) -----------------------------------


def test_resolve_model_config_returns_connected_node_config() -> None:
    llm = _llm_node(config={"provider": "anthropic", "model": "claude-sonnet-5", "temperature": 0.5})
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {"nodes": [llm, agent], "edges": [agent_llm_edge]}
    assert pe._resolve_model_config(graph, "a") == {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "temperature": 0.5,
    }


def test_resolve_model_config_empty_when_unconnected() -> None:
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    assert pe._resolve_model_config(graph, "a") == {}


def test_resolve_dataset_configs_returns_connected_node_config() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node(dataset_name="spinal-fusion-v1")
    graph = {"nodes": [agent, dataset], "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")]}
    assert pe._resolve_dataset_configs(graph, "a") == [{"dataset_id": "d1", "dataset_name": "spinal-fusion-v1"}]


def test_resolve_dataset_configs_empty_when_unconnected() -> None:
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    assert pe._resolve_dataset_configs(graph, "a") == []


def test_resolve_dataset_configs_keeps_wiring_order_and_dedupes() -> None:
    # Two nodes naming the same registered dataset is a legal graph -- it
    # would just tell the agent to open one workspace twice, so the second is
    # dropped rather than rejected (same call _resolve_skill_config makes).
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [
            agent,
            _dataset_node("ds1", dataset_name="cohort-b", dataset_id="d2"),
            _dataset_node("ds2", dataset_name="cohort-a", dataset_id="d1"),
            _dataset_node("ds3", dataset_name="cohort-a", dataset_id="d1"),
        ],
        "edges": [
            agent_llm_edge,
            _dataset_edge("ds1", "a"),
            _dataset_edge("ds2", "a"),
            _dataset_edge("ds3", "a"),
        ],
    }
    assert [c["dataset_name"] for c in pe._resolve_dataset_configs(graph, "a")] == ["cohort-b", "cohort-a"]


def test_resolve_dataset_configs_skips_disabled_nodes() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    disabled = _dataset_node("ds2", dataset_name="cohort-b", dataset_id="d2")
    disabled["data"]["config"]["enabled"] = False
    graph = {
        "nodes": [agent, _dataset_node("ds1", dataset_name="cohort-a", dataset_id="d1"), disabled],
        "edges": [agent_llm_edge, _dataset_edge("ds1", "a"), _dataset_edge("ds2", "a")],
    }
    assert [c["dataset_name"] for c in pe._resolve_dataset_configs(graph, "a")] == ["cohort-a"]


def test_resolve_script_configs_returns_connected_node_configs_in_wiring_order() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    first = _script_node("script1", code="print('first')")
    second = _script_node("script2", code="print('second')")
    graph = {
        "nodes": [agent, first, second],
        "edges": [agent_llm_edge, _script_edge("script2", "a"), _script_edge("script1", "a")],
    }
    assert pe._resolve_script_configs(graph, "a") == [
        {"name": "scoring-script", "language": "python", "code": "print('second')", "node_id": "script2"},
        {"name": "scoring-script", "language": "python", "code": "print('first')", "node_id": "script1"},
    ]


def test_resolve_script_configs_empty_when_unconnected() -> None:
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    assert pe._resolve_script_configs(graph, "a") == []


def test_resolve_tool_config_collects_all_connected_tool_nodes() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    tool1 = _tool_node("tool1", server_name="srv-a", tool_names=["fn_a"])
    tool2 = _tool_node("tool2", server_name="srv-b", tool_names=["fn_b"])
    graph = {
        "nodes": [agent, tool1, tool2],
        "edges": [agent_llm_edge, _tool_edge("tool1", "a"), _tool_edge("tool2", "a")],
    }
    resolved = pe._resolve_tool_config(graph, "a")
    # tool_names must come back namespaced ("server.tool") -- that's the
    # shape run_tools.gather_tools matches against Motoro's registry;
    # a bare name never matches and silently strands the agent with zero
    # tools (see the regression test below).
    assert resolved == {"server_names": ["srv-a", "srv-b"], "tool_names": ["srv-a.fn_a", "srv-b.fn_b"]}


def test_resolve_tool_config_treats_a_client_tool_node_like_any_other() -> None:
    """A user-registered server (the MCP Client Tool node) is a Tool source
    like any other: same config shape, same resolution, no special case. Only
    the node type differs, and only to record where the server came from."""
    agent, agent_llm_edge = _agent_with_llm("a")
    client = _tool_node("tool1", server_name="my-search", tool_names=["search"])
    client["type"] = "mcp_client_tool"
    graph = {"nodes": [_llm_node(), agent, client], "edges": [agent_llm_edge, _tool_edge("tool1", "a")]}
    assert topological_order(graph)  # accepted on the Tool connector at all
    assert pe._resolve_tool_config(graph, "a") == {
        "server_names": ["my-search"],
        "tool_names": ["my-search.search"],
    }


def test_resolve_tool_config_empty_when_no_tool_connections() -> None:
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    assert pe._resolve_tool_config(graph, "a") == {"server_names": [], "tool_names": []}


def test_resolve_tool_config_allows_multiple_tools_from_one_server() -> None:
    """One mcp_tool node == one server connection, which can allow-list
    several of that server's tools -- not one node per tool."""
    agent, agent_llm_edge = _agent_with_llm("a")
    tool = _tool_node(server_name="srv-a", tool_names=["fn_a", "fn_b", "fn_c"])
    graph = {"nodes": [agent, tool], "edges": [agent_llm_edge, _tool_edge("tool1", "a")]}
    resolved = pe._resolve_tool_config(graph, "a")
    assert resolved == {"server_names": ["srv-a"], "tool_names": ["srv-a.fn_a", "srv-a.fn_b", "srv-a.fn_c"]}


def test_resolve_tool_config_skips_disabled_tool_node() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    tool1 = _tool_node("tool1", server_name="srv-a", tool_names=["fn_a"])
    tool1["data"]["config"]["enabled"] = False
    tool2 = _tool_node("tool2", server_name="srv-b", tool_names=["fn_b"])
    graph = {
        "nodes": [agent, tool1, tool2],
        "edges": [agent_llm_edge, _tool_edge("tool1", "a"), _tool_edge("tool2", "a")],
    }
    resolved = pe._resolve_tool_config(graph, "a")
    assert resolved == {"server_names": ["srv-b"], "tool_names": ["srv-b.fn_b"]}


def test_resolve_tool_config_namespaces_tool_names_for_gather_tools() -> None:
    """Regression test for the bug where every canvas-run agent silently got
    zero MCP tools: run_tools.gather_tools matches tool_names against
    Motoro's registry, whose entries are namespaced "server.tool"
    (MCPServerRegistry.get_all_tools). A bare tool_name never matches that,
    so the agent's LLM would see no tools at all -- no error, it just falls
    back to reporting the blocker as its final answer."""
    agent, agent_llm_edge = _agent_with_llm("a")
    tool = _tool_node(server_name="asaree-workspace", tool_names=["open_workspace", "accept_stage"])
    graph = {"nodes": [agent, tool], "edges": [agent_llm_edge, _tool_edge("tool1", "a")]}
    resolved = pe._resolve_tool_config(graph, "a")
    assert resolved["tool_names"] == ["asaree-workspace.open_workspace", "asaree-workspace.accept_stage"]


def test_resolve_tool_config_skips_tools_from_node_with_no_server_name() -> None:
    """A tool_names value can't be namespaced without a server_name to
    prefix it with, so those tools are dropped rather than smuggled through
    bare (which would silently fail the same way as the bug above)."""
    agent, agent_llm_edge = _agent_with_llm("a")
    tool = _tool_node(server_name=None, tool_names=["fn_a"])
    graph = {"nodes": [agent, tool], "edges": [agent_llm_edge, _tool_edge("tool1", "a")]}
    resolved = pe._resolve_tool_config(graph, "a")
    assert resolved == {"server_names": [], "tool_names": []}


def test_resolve_pattern_config_returns_connected_node_config() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    pattern = _pattern_node()
    pattern["data"]["config"] = {"max_iterations": 20, "include_scratchpad": False}
    graph = {"nodes": [agent, pattern], "edges": [agent_llm_edge, _pattern_edge("pattern", "a")]}
    assert pe._resolve_pattern_config(graph, "a") == {
        "execution_pattern": "reason_act",
        "pattern_params": {"reason_act": {"max_iterations": 20, "include_scratchpad": False}},
    }


def test_resolve_pattern_config_maps_baseline_slug() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    baseline = {
        "id": "baseline",
        "type": "pattern_single_agent_baseline",
        "data": {"label": "", "config": {"max_iterations": 5}},
    }
    graph = {"nodes": [agent, baseline], "edges": [agent_llm_edge, _pattern_edge("baseline", "a")]}
    assert pe._resolve_pattern_config(graph, "a") == {
        "execution_pattern": "single_agent_baseline",
        "pattern_params": {"single_agent_baseline": {"max_iterations": 5}},
    }


def test_resolve_pattern_config_empty_when_unconnected() -> None:
    # Optional connector -- an unconnected agent resolves to {}, letting
    # PatternConfig(execution_pattern=None) fall through to Motoro's
    # own "reason_act" default, not an ASAREE-side hardcoded one.
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    assert pe._resolve_pattern_config(graph, "a") == {}


def test_resolve_pattern_config_override_wins_over_wired_connector() -> None:
    """A factor bound to the agent's own data.pattern_override (via a plain
    _set_path top-level key, same as any other binding) switches the
    resolved pattern entirely -- this is how a Pattern factor varies the
    node type itself across cells, since the wired connector node alone
    can't."""
    agent, agent_llm_edge = _agent_with_llm("a")
    agent["data"]["pattern_override"] = {
        "execution_pattern": "single_agent_baseline",
        "pattern_params": {"single_agent_baseline": {"max_iterations": 3}},
    }
    pattern = _pattern_node()
    graph = {"nodes": [agent, pattern], "edges": [agent_llm_edge, _pattern_edge("pattern", "a")]}
    assert pe._resolve_pattern_config(graph, "a") == {
        "execution_pattern": "single_agent_baseline",
        "pattern_params": {"single_agent_baseline": {"max_iterations": 3}},
    }


def test_resolve_pattern_config_override_wins_when_unconnected() -> None:
    agent = {
        "id": "a",
        "type": "agent",
        "data": {"label": "", "config": {}, "pattern_override": {"execution_pattern": "reason_act"}},
    }
    graph = {"nodes": [agent], "edges": []}
    assert pe._resolve_pattern_config(graph, "a") == {"execution_pattern": "reason_act"}


async def test_run_protocol_tool_source_node_never_gets_its_own_turn(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An mcp_tool node is always a pure config source (see
    _PURE_CONFIG_SOURCE_TYPES) -- it must complete instantly with no output,
    the same way an llm/memory/pattern node does, never routed to
    _run_agent_node."""

    async def fake_run_agent_node(node, *, graph, **kwargs):
        return f"output for {node['id']}", None, None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    tool = _tool_node()
    graph = {
        "nodes": [llm, agent, tool],
        "edges": [agent_llm_edge, _tool_edge("tool1", "a")],
    }

    async with get_session() as db:
        protocol = await create_protocol(
            db, name=f"tool-source-test-{uuid.uuid4().hex}", owner_id=owner_id, graph=graph
        )
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    try:
        await pe.run_protocol(run_id)

        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "completed"
            assert fetched.node_runs["tool1"] == {"status": "completed", "output_text": None, "error": None}
            assert fetched.node_runs["a"]["output_text"] == "output for a"
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun


# --- Output Parser connector (pure) ------------------------------------------

_CONTRACT = {"name": "DCReport", "fields": [{"name": "n_rows", "type": "integer", "description": "Row count"}]}


def _parser_node(node_id: str = "p1", contract: dict | None = None, enabled: bool | None = None) -> dict:
    config: dict = {"output_contract": contract if contract is not None else _CONTRACT}
    if enabled is not None:
        config["enabled"] = enabled
    return {"id": node_id, "type": "output_parser", "data": {"label": "", "config": config}}


def _parser_edge(source: str, target: str) -> dict:
    return {
        "id": f"{source}-{target}-output_parser",
        "source": source,
        "target": target,
        "targetHandle": "output_parser",
    }


def _agent_with_parser(*, legacy: dict | None = None, enabled: bool | None = None) -> dict:
    agent, agent_llm_edge = _agent_with_llm("a")
    if legacy is not None:
        agent["data"]["config"]["output_contract"] = legacy
    return {
        "nodes": [_llm_node(), agent, _parser_node(enabled=enabled)],
        "edges": [agent_llm_edge, _parser_edge("p1", "a")],
    }


def _agent_with_parser_contract(contract: dict) -> dict:
    agent, agent_llm_edge = _agent_with_llm("a")
    return {
        "nodes": [_llm_node(), agent, _parser_node(contract=contract)],
        "edges": [agent_llm_edge, _parser_edge("p1", "a")],
    }


def test_wired_output_parser_resolves_its_contract() -> None:
    assert pe._resolve_output_contract(_agent_with_parser(), "a") == _CONTRACT


def test_no_parser_and_no_legacy_field_resolves_none() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {"nodes": [_llm_node(), agent], "edges": [agent_llm_edge]}
    assert pe._resolve_output_contract(graph, "a") is None


def test_legacy_stored_contract_still_resolves_with_no_parser_wired() -> None:
    """The permanent fallback: 8 published revisions carry this field, and
    ``POST /agents`` can still set it, so it is never dead code."""
    agent, agent_llm_edge = _agent_with_llm("a")
    agent["data"]["config"]["output_contract"] = _CONTRACT
    graph = {"nodes": [_llm_node(), agent], "edges": [agent_llm_edge]}
    assert pe._resolve_output_contract(graph, "a") == _CONTRACT
    # ...and such a graph is still a valid graph, not one that now needs fixing.
    topological_order(graph)


def test_legacy_contract_types_are_not_normalised() -> None:
    """The stored spinal contracts use ``object``/``array``/``number``, which
    Motoro's own _TYPE_MAP accepts as aliases. Nothing here may rewrite them."""
    contract = {"name": "FTEReport", "fields": [{"name": "recipe", "type": "object"}, {"name": "k", "type": "number"}]}
    agent, agent_llm_edge = _agent_with_llm("a")
    agent["data"]["config"]["output_contract"] = contract
    resolved = pe._resolve_output_contract({"nodes": [_llm_node(), agent], "edges": [agent_llm_edge]}, "a")
    assert resolved is not None
    assert [f["type"] for f in resolved["fields"]] == ["object", "number"]


def test_a_parser_whose_fields_are_all_blank_resolves_none() -> None:
    """The shape a brand-new parser node arrives in: one empty editor row. It
    is present but names nothing, so passing it on would cost a model call
    extracting a payload that cannot have a single key in it."""
    blank = {"name": "", "fields": [{"name": "", "type": "string", "description": ""}]}
    assert pe._resolve_output_contract(_agent_with_parser_contract(blank), "a") is None
    # ...and the same field spec stored the legacy way is equally empty.
    agent, agent_llm_edge = _agent_with_llm("a")
    agent["data"]["config"]["output_contract"] = blank
    assert pe._resolve_output_contract({"nodes": [_llm_node(), agent], "edges": [agent_llm_edge]}, "a") is None


def test_one_named_field_among_blanks_is_still_a_contract() -> None:
    """Half-filled is not empty -- the named row is a real declaration, and the
    prompt block and the payload model both simply skip the unnamed ones."""
    half = {"name": "S", "fields": [{"name": "n_rows", "type": "integer"}, {"name": "", "type": "string"}]}
    assert pe._resolve_output_contract(_agent_with_parser_contract(half), "a") == half


def test_disabled_output_parser_contributes_nothing() -> None:
    assert pe._resolve_output_contract(_agent_with_parser(enabled=False), "a") is None


def test_disabled_parser_does_not_fall_back_to_a_legacy_field() -> None:
    """Disabling the parser means "no extraction this run". Quietly reaching
    past it to a stored field would be a different contract than either."""
    graph = _agent_with_parser(legacy=_CONTRACT, enabled=False)
    # The graph itself is refused (below), but were it ever reached, disabling
    # must not resurrect the legacy field.
    assert pe._resolve_output_contract(graph, "a") is None


def test_multiple_output_parser_connections_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _parser_node("p1"), _parser_node("p2")],
        "edges": [agent_llm_edge, _parser_edge("p1", "a"), _parser_edge("p2", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="at most one Output Parser connection"):
        topological_order(graph)


def test_parser_plus_legacy_contract_on_one_node_raises() -> None:
    with pytest.raises(ProtocolValidationError, match="both an Output Parser connection and its own stored"):
        topological_order(_agent_with_parser(legacy=_CONTRACT))


def test_output_parser_connection_source_must_be_a_parser_node() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _memory_node("m1")],
        "edges": [agent_llm_edge, _parser_edge("m1", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="Output Parser connection must come from an Output Parser"):
        topological_order(graph)


def test_output_parser_connection_on_critic_gate_raises() -> None:
    """A critic's verdict schema is CRITIC_OUTPUT_CONTRACT, fixed by the
    executor -- the connector is deliberately agent-only."""
    llm = _llm_node()
    worker, worker_llm_edge = _agent_with_llm("w1")
    gate_llm_edge = _llm_edge("llm", "g1")
    graph = {
        "nodes": [llm, worker, _node("g1", "critic_gate"), _parser_node("p1")],
        "edges": [
            worker_llm_edge,
            gate_llm_edge,
            {"id": "w1-g1", "source": "w1", "target": "g1"},
            _parser_edge("p1", "g1"),
        ],
    }
    with pytest.raises(
        ProtocolValidationError,
        match="Only Agent nodes can have a Tool, Memory, Architectural Pattern, Skill, Dataset, ",
    ):
        topological_order(graph)


def test_output_parser_node_with_plain_outgoing_edge_raises() -> None:
    """A pure config source may only emit into its own connector -- an
    output_parser wired into the main pipeline is not a pipeline step."""
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _parser_node("p1")],
        "edges": [agent_llm_edge, {"id": "p1-a", "source": "p1", "target": "a"}],
    }
    with pytest.raises(ProtocolValidationError):
        topological_order(graph)


def test_output_parser_is_never_a_sink() -> None:
    """It's a pure config source, so it must not be mistaken for the
    pipeline's final output."""
    graph = _agent_with_parser()
    assert pe.sink_node_ids(graph) == ["a"]


# --- Coordination strategy validation (pure) ---------------------------------


def _peer_graph(*agent_ids: str) -> dict:
    """Agents chained left-to-right by plain (untyped) edges, each with its own
    LLM. That chain is both a pipeline and a peer cluster -- which one it means
    is the coordination strategy's call, which is exactly what's under test."""
    nodes: list[dict] = []
    edges: list[dict] = []
    for i, agent_id in enumerate(agent_ids):
        llm_id = f"llm-{agent_id}"
        agent, llm_edge = _agent_with_llm(agent_id, llm_id)
        nodes += [_llm_node(llm_id), agent]
        edges.append(llm_edge)
        if i:
            edges.append({"id": f"e{i}", "source": agent_ids[i - 1], "target": agent_id})
    return {"nodes": nodes, "edges": edges}


def _no_peers_graph() -> dict:
    llm = _llm_node()
    agent, llm_edge = _agent_with_llm("a")
    return {"nodes": [llm, agent], "edges": [llm_edge]}


def test_coordination_strategy_absent_is_a_noop() -> None:
    validate_coordination_strategy(None, graph=_no_peers_graph())
    validate_coordination_strategy({}, graph=_no_peers_graph())


_SEQUENTIAL = {"coordination_strategy": {"slug": "sequential"}}


def _chain_graph(*agent_ids: str) -> dict:
    """A labelled version of ``_peer_graph`` -- the sequential errors name the
    offending agents, so the labels are part of what's under test."""
    graph = _peer_graph(*agent_ids)
    for node in graph["nodes"]:
        if node["type"] == "agent":
            node["data"] = {**node["data"], "label": node["id"].upper()}
    return graph


def _add_agent(graph: dict, agent_id: str, *edges: tuple[str, str]) -> dict:
    llm_id = f"llm-{agent_id}"
    agent, llm_edge = _agent_with_llm(agent_id, llm_id)
    agent["data"] = {**agent["data"], "label": agent_id.upper()}
    graph["nodes"] += [_llm_node(llm_id), agent]
    graph["edges"].append(llm_edge)
    graph["edges"] += [{"id": f"e-{s}-{t}", "source": s, "target": t} for s, t in edges]
    return graph


def test_sequential_accepts_a_chain() -> None:
    validate_coordination_strategy(_SEQUENTIAL, graph=_no_peers_graph())  # one agent, nothing wired
    validate_coordination_strategy(_SEQUENTIAL, graph=_chain_graph("a", "b"))
    validate_coordination_strategy(_SEQUENTIAL, graph=_chain_graph("a", "b", "c"))
    assert pe.sequential_chain_order(_chain_graph("a", "b", "c")) == ["a", "b", "c"]


def test_sequential_accepts_a_graph_with_no_agents_at_all() -> None:
    # Not a runnable protocol, but "no agents" is not the chain rule's
    # complaint to make -- the every-agent-needs-an-LLM check owns that.
    validate_coordination_strategy(_SEQUENTIAL, graph={"nodes": [_llm_node()], "edges": []})


def test_sequential_rejects_a_fork() -> None:
    graph = _add_agent(_chain_graph("a", "b"), "c", ("a", "c"))
    with pytest.raises(ProtocolValidationError, match=r"'A' hands off to more than one agent \(B, C\)"):
        validate_coordination_strategy(_SEQUENTIAL, graph=graph)


def test_sequential_rejects_a_fan_in() -> None:
    graph = _add_agent(_chain_graph("a", "b"), "c", ("c", "b"))
    with pytest.raises(ProtocolValidationError, match=r"More than one agent hands off to 'B' \(A, C\)"):
        validate_coordination_strategy(_SEQUENTIAL, graph=graph)


def test_sequential_rejects_two_disjoint_chains() -> None:
    graph = _add_agent(_add_agent(_chain_graph("a", "b"), "c"), "d", ("c", "d"))
    with pytest.raises(ProtocolValidationError, match=r"2 separate agent chains, starting at A, C"):
        validate_coordination_strategy(_SEQUENTIAL, graph=graph)


def test_sequential_rejects_a_chain_with_a_detached_cycle() -> None:
    # a -> b, plus c <-> d off to one side: one head, but the pair is
    # unreachable from it, so a run would never get to them.
    graph = _add_agent(_add_agent(_chain_graph("a", "b"), "c", ("d", "c")), "d", ("c", "d"))
    with pytest.raises(ProtocolValidationError, match="cannot be reached from the start of the chain"):
        validate_coordination_strategy(_SEQUENTIAL, graph=graph)


def test_sequential_rejects_a_loop() -> None:
    graph = _cycle_peer_graph("a", "b", "c")
    with pytest.raises(ProtocolValidationError, match="nowhere to start"):
        validate_coordination_strategy(_SEQUENTIAL, graph=graph)


def test_sequential_ignores_connector_fan_in() -> None:
    """One Model node feeding every agent in the chain is the normal shape. It is
    a fan-in on the graph and must not read as one on the chain."""
    graph = _chain_graph("a", "b", "c")
    graph["nodes"] = [n for n in graph["nodes"] if n["type"] != "model_anthropic"] + [_llm_node("shared")]
    graph["edges"] = [e for e in graph["edges"] if e.get("targetHandle") != "model"]
    graph["edges"] += [_llm_edge("shared", a) for a in ("a", "b", "c")]
    validate_coordination_strategy(_SEQUENTIAL, graph=graph)


def test_sequential_ignores_non_agent_nodes_between_two_agents() -> None:
    """A critic gate sitting between two agents keeps the chain a chain: only
    ``agent -> agent`` edges count, so the gate is not a third link."""
    graph = _chain_graph("a", "b")
    graph["edges"] = [e for e in graph["edges"] if not (e["source"] == "a" and e["target"] == "b")]
    graph["nodes"].append(_node("gate", "critic_gate"))
    graph["edges"] += _edges(("a", "gate"), ("gate", "b"))
    validate_coordination_strategy(_SEQUENTIAL, graph=graph)
    # The gate is not an agent, so it is not in the handoff order either.
    assert pe.sequential_chain_order(graph) == ["a", "b"]


def test_the_chain_rule_does_not_apply_to_the_other_strategies() -> None:
    """A fork is exactly the shape ``peer_collaboration`` exists for, and the
    spinal ``critic_gate`` family is 5 agents wired through gates. Neither may
    pick up ``sequential``'s cardinality limits."""
    fork = _add_agent(_chain_graph("a", "b"), "c", ("a", "c"))
    validate_coordination_strategy({"coordination_strategy": {"slug": "peer_collaboration"}}, graph=fork)
    gated = _chain_graph("a")
    gated["nodes"].append(_node("gate", "critic_gate"))
    gated["edges"] += _edges(("a", "gate"))
    validate_coordination_strategy({"coordination_strategy": {"slug": "critic_gate"}}, graph=gated)


def test_coordination_strategy_critic_gate_requires_a_gated_pair() -> None:
    with pytest.raises(ProtocolValidationError, match="no Critic Gate node wired in"):
        validate_coordination_strategy({"coordination_strategy": {"slug": "critic_gate"}}, graph=_no_peers_graph())


def test_coordination_strategy_peer_collaboration_needs_connected_agents() -> None:
    with pytest.raises(ProtocolValidationError, match="no two Agent nodes"):
        validate_coordination_strategy(
            {"coordination_strategy": {"slug": "peer_collaboration"}}, graph=_no_peers_graph()
        )


def test_coordination_strategy_peer_collaboration_passes_with_a_peer_edge() -> None:
    validate_coordination_strategy(
        {"coordination_strategy": {"slug": "peer_collaboration"}}, graph=_peer_graph("a", "b")
    )


def test_the_conversation_starts_at_the_agent_nothing_feeds() -> None:
    assert pe.resolve_conversation_entry_id(_peer_graph("a", "b", "c")) == "a"


def test_two_equally_plausible_starting_agents_is_an_error() -> None:
    # a -> c <- b: both a and b are unfed, so there is no honest way to pick.
    graph = _peer_graph("a", "c")
    llm_b = _llm_node("llm-b")
    agent_b, llm_edge_b = _agent_with_llm("b", "llm-b")
    graph["nodes"] += [llm_b, agent_b]
    graph["edges"] += [llm_edge_b, {"id": "e-bc", "source": "b", "target": "c"}]
    with pytest.raises(ProtocolValidationError, match="more than one agent that could start"):
        pe.resolve_conversation_entry_id(graph)


def _mark_lead(graph: dict, *agent_ids: str) -> dict:
    """Set the canvas's explicit conversation-lead flag on some agent nodes."""
    for node in graph["nodes"]:
        if node["id"] in agent_ids:
            node["data"] = {**(node.get("data") or {}), "conversation_lead": True}
    return graph


def _cycle_peer_graph(*agent_ids: str) -> dict:
    """The topology the lead marker exists for: the chain's last agent wired
    back to its first, so every agent is fed and the wiring rule has no
    candidate at all to pick."""
    graph = _peer_graph(*agent_ids)
    graph["edges"].append({"id": "e-cycle", "source": agent_ids[-1], "target": agent_ids[0]})
    return graph


def test_a_marked_lead_wins_over_the_wiring() -> None:
    # a -> b -> c would derive "a"; the marker is an override, not a tiebreak.
    assert pe.resolve_conversation_entry_id(_mark_lead(_peer_graph("a", "b", "c"), "c")) == "c"


def test_a_cycle_has_no_derivable_lead() -> None:
    with pytest.raises(ProtocolValidationError, match="wired in a loop"):
        pe.resolve_conversation_entry_id(_cycle_peer_graph("a", "b", "c"))


def test_a_marked_lead_resolves_a_cycle() -> None:
    """The whole point of the marker: everyone wired to everyone is the shape
    this strategy invites, and it must not have to be broken to run."""
    assert pe.resolve_conversation_entry_id(_mark_lead(_cycle_peer_graph("a", "b", "c"), "b")) == "b"


def test_two_marked_leads_is_an_error() -> None:
    with pytest.raises(ProtocolValidationError, match="More than one agent is marked"):
        pe.resolve_conversation_entry_id(_mark_lead(_peer_graph("a", "b", "c"), "a", "c"))


def test_a_marked_lead_with_no_peers_is_an_error() -> None:
    """Marking an agent that has nobody to talk to must not win -- that would
    run a "conversation" with a single participant."""
    graph = _peer_graph("a", "b")
    llm_c = _llm_node("llm-c")
    agent_c, llm_edge_c = _agent_with_llm("c", "llm-c")
    graph["nodes"] += [llm_c, agent_c]
    graph["edges"].append(llm_edge_c)
    with pytest.raises(ProtocolValidationError, match="isn't connected to another agent"):
        pe.resolve_conversation_entry_id(_mark_lead(graph, "c"))


_PEER_SPEC = {"coordination_strategy": {"slug": "peer_collaboration"}}


def test_is_conversation_strategy() -> None:
    assert pe.is_conversation_strategy(_PEER_SPEC) is True
    assert pe.is_conversation_strategy({"coordination_strategy": {"slug": "critic_gate"}}) is False
    assert pe.is_conversation_strategy(None) is False


def test_a_cycle_is_still_rejected_for_a_pipeline() -> None:
    with pytest.raises(ProtocolValidationError, match="has a cycle"):
        pe.topological_order(_cycle_peer_graph("a", "b", "c"))


def test_a_conversation_may_contain_a_cycle() -> None:
    """The lead marker resolves the *entry agent* in a loop; this is what makes
    the same loop publishable and runnable. Every node still comes back -- the
    order is meaningless, and run_protocol's conversation branch discards it."""
    graph = _cycle_peer_graph("a", "b", "c")
    ordered = pe.topological_order(graph, require_acyclic=False)
    assert {n["id"] for n in ordered} == {n["id"] for n in graph["nodes"]}


def test_an_empty_graph_is_rejected_even_for_a_conversation() -> None:
    """require_acyclic drops one check, not all of them."""
    with pytest.raises(ProtocolValidationError, match="no nodes"):
        pe.topological_order({"nodes": [], "edges": []}, require_acyclic=False)


def test_coordination_strategy_accepts_a_marked_lead_in_a_cycle() -> None:
    """End to end over the guard both the publish endpoint and run_protocol call."""
    pe.validate_coordination_strategy(_PEER_SPEC, graph=_mark_lead(_cycle_peer_graph("a", "b", "c"), "b"))


# ----------------------------------------------------------------------
# Supervisor architecture -- roles read off the wiring
# ----------------------------------------------------------------------

_SUPERVISOR_SPEC = {"coordination_strategy": {"slug": "supervisor_architecture"}}


def _labelled(graph: dict) -> dict:
    """Uppercase labels on every agent -- the errors name agents, so the label
    is part of what's asserted."""
    for node in graph["nodes"]:
        if node["type"] == "agent":
            node["data"] = {**node["data"], "label": node["id"].upper()}
    return graph


def _supervisor_graph(*, reviewer: bool = True, workers: int = 3) -> dict:
    """The target topology the user asked ASAREE to support: one supervisor, N
    workers hanging off it, and a QC agent that sees every worker and reports
    back to the supervisor.

    Note the QC edges point INTO the reviewer from the workers and OUT of it to
    the supervisor. That direction is what makes it a reviewer rather than a
    fourth worker (see resolve_supervisor_roles).
    """
    graph = _labelled(_peer_graph("sup"))
    worker_ids = [f"w{i}" for i in range(1, workers + 1)]
    for worker_id in worker_ids:
        _add_agent(graph, worker_id, ("sup", worker_id))
    if reviewer:
        _add_agent(graph, "qc", *[(worker_id, "qc") for worker_id in worker_ids], ("qc", "sup"))
    return graph


def test_supervisor_reads_the_target_topology() -> None:
    roles = pe.resolve_supervisor_roles(_supervisor_graph())
    assert roles.supervisor == "sup"
    assert roles.workers == ("w1", "w2", "w3")
    assert roles.reviewer == "qc"
    # supervisor brief + 3 workers + review + synthesis
    assert roles.execution_budget == 6


def test_supervisor_without_a_reviewer_is_fine() -> None:
    roles = pe.resolve_supervisor_roles(_supervisor_graph(reviewer=False))
    assert roles.reviewer is None
    assert roles.execution_budget == 5


def test_supervisor_topology_passes_the_shared_guard() -> None:
    """End to end over the same function the publish endpoint and run_protocol
    call -- reading the roles *is* the validation."""
    validate_coordination_strategy(_SUPERVISOR_SPEC, graph=_supervisor_graph())


def test_supervisor_is_a_conversation_strategy() -> None:
    """Which is what suspends the acyclic check: the reviewer's report back to
    the supervisor closes a loop, and that loop is the topology, not a bug."""
    assert pe.is_conversation_strategy(_SUPERVISOR_SPEC) is True
    pe.topological_order(_supervisor_graph(), require_acyclic=False)
    with pytest.raises(ProtocolValidationError, match="has a cycle"):
        pe.topological_order(_supervisor_graph())


def test_supervisor_rejects_a_worker_wired_to_another_worker() -> None:
    graph = _supervisor_graph(reviewer=False, workers=2)
    graph["edges"].append({"id": "e-w1-w2", "source": "w1", "target": "w2"})
    with pytest.raises(ProtocolValidationError, match=r"'W1' is wired to another worker \(W2\)"):
        validate_coordination_strategy(_SUPERVISOR_SPEC, graph=graph)


def test_supervisor_rejects_two_marked_supervisors() -> None:
    graph = _mark_lead(_supervisor_graph(reviewer=False), "sup", "w1")
    with pytest.raises(ProtocolValidationError, match=r"More than one agent is marked as the supervisor \(SUP, W1\)"):
        validate_coordination_strategy(_SUPERVISOR_SPEC, graph=graph)


def test_supervisor_rejects_a_supervisor_with_no_workers() -> None:
    """Two agents, neither wired to the other: one is marked, and marking
    doesn't invent anybody to dispatch to."""
    graph = _labelled(_peer_graph("sup"))
    _add_agent(graph, "w1")
    with pytest.raises(ProtocolValidationError, match="hands off to no other agent"):
        validate_coordination_strategy(_SUPERVISOR_SPEC, graph=_mark_lead(graph, "sup"))


def test_supervisor_rejects_a_single_agent() -> None:
    with pytest.raises(ProtocolValidationError, match="fewer than two Agent nodes"):
        validate_coordination_strategy(_SUPERVISOR_SPEC, graph=_no_peers_graph())


def test_supervisor_rejects_an_ambiguous_head() -> None:
    """Two agents fanning out to the same worker: either could be the
    supervisor, so the canvas has to say which."""
    graph = _labelled(_peer_graph("sup"))
    _add_agent(graph, "w1", ("sup", "w1"))
    _add_agent(graph, "other", ("other", "w1"))
    with pytest.raises(ProtocolValidationError, match=r"more than one agent could be \(OTHER, SUP\)"):
        validate_coordination_strategy(_SUPERVISOR_SPEC, graph=graph)


def test_supervisor_rejects_a_symmetric_ring() -> None:
    """Three agents in a ring: every one dispatches to exactly one other, so
    the wiring says nothing about which is in charge. The target topology
    resolves in a loop only because the supervisor fans out wider than the
    reviewer reports back (see _supervisor_candidates)."""
    with pytest.raises(ProtocolValidationError, match=r"more than one agent could be \(A, B, C\)"):
        validate_coordination_strategy(_SUPERVISOR_SPEC, graph=_labelled(_cycle_peer_graph("a", "b", "c")))


def test_supervisor_marker_resolves_an_ambiguous_head() -> None:
    """Same graph, and the marker is how the user resolves it -- 'other' becomes
    the one leftover agent, i.e. the reviewer."""
    graph = _labelled(_peer_graph("sup"))
    _add_agent(graph, "w1", ("sup", "w1"))
    _add_agent(graph, "other", ("other", "w1"), ("other", "sup"))
    roles = pe.resolve_supervisor_roles(_mark_lead(graph, "sup"))
    assert (roles.supervisor, roles.workers, roles.reviewer) == ("sup", ("w1",), "other")


def test_supervisor_rejects_more_than_one_leftover_agent() -> None:
    """Two agents that the supervisor doesn't dispatch to can't both be the
    reviewer, and ASAREE will not guess which one it dispatches."""
    graph = _supervisor_graph(reviewer=False, workers=2)
    _add_agent(graph, "qc1", ("w1", "qc1"), ("qc1", "sup"))
    _add_agent(graph, "qc2", ("w2", "qc2"), ("qc2", "sup"))
    with pytest.raises(ProtocolValidationError, match="QC1, QC2 are neither the supervisor nor"):
        validate_coordination_strategy(_SUPERVISOR_SPEC, graph=graph)


def test_supervisor_rejects_a_reviewer_with_only_one_connection() -> None:
    """A "reviewer" hanging off one worker reviews a third of the run. It's
    almost always a mis-drawn edge, and the error says how to fix it either way."""
    graph = _supervisor_graph(reviewer=False, workers=2)
    _add_agent(graph, "qc", ("w1", "qc"))
    with pytest.raises(ProtocolValidationError, match="'QC' reviews this run but is connected to only one"):
        validate_coordination_strategy(_SUPERVISOR_SPEC, graph=graph)


def test_supervisor_roles_ignore_plumbing_between_agents() -> None:
    """A Script node between the supervisor and a worker is plumbing, not a
    role -- the handoff is a path, the same way sequential_chain_order reads it."""
    graph = _labelled(_peer_graph("sup"))
    _add_agent(graph, "w1", ("sup", "w1"))
    _add_agent(graph, "w2")
    graph["nodes"].append({"id": "script", "type": "script", "data": {"label": "Prep"}})
    graph["edges"] += [
        {"id": "e-sup-script", "source": "sup", "target": "script"},
        {"id": "e-script-w2", "source": "script", "target": "w2"},
    ]
    roles = pe.resolve_supervisor_roles(graph)
    assert roles.workers == ("w1", "w2")


def test_supervisor_workers_are_parallel_unless_opted_out() -> None:
    assert pe._supervisor_workers_run_in_parallel(None) is True
    assert pe._supervisor_workers_run_in_parallel(_SUPERVISOR_SPEC) is True
    assert pe._supervisor_workers_run_in_parallel({"coordination_strategy": {"slug": "x", "params": {}}}) is True
    # Only an explicit false opts out -- an unrelated key must not halve a run.
    assert (
        pe._supervisor_workers_run_in_parallel(
            {"coordination_strategy": {"slug": "x", "params": {"something_else": 1}}}
        )
        is True
    )
    assert (
        pe._supervisor_workers_run_in_parallel(
            {"coordination_strategy": {"slug": "x", "params": {"parallel_workers": False}}}
        )
        is False
    )


def test_peer_collaboration_is_unaffected_by_the_supervisor_rules() -> None:
    """The supervisor topology is a legal peer cluster too, and a peer mesh the
    supervisor rules reject stays legal under Peer Collaboration -- the strategy
    decides what the wiring means, which is the whole point of the dropdown."""
    validate_coordination_strategy(_PEER_SPEC, graph=_mark_lead(_supervisor_graph(), "sup"))
    mesh = _supervisor_graph(reviewer=False, workers=2)
    mesh["edges"].append({"id": "e-w1-w2", "source": "w1", "target": "w2"})
    validate_coordination_strategy(_PEER_SPEC, graph=mesh)


def test_coordination_strategy_retired_slug_raises() -> None:
    with pytest.raises(ProtocolValidationError, match="no longer offered"):
        validate_coordination_strategy(
            {"coordination_strategy": {"slug": "swarm_architecture"}}, graph=_no_peers_graph()
        )


def test_coordination_strategy_unknown_slug_raises() -> None:
    with pytest.raises(ProtocolValidationError, match="Unknown coordination strategy"):
        validate_coordination_strategy({"coordination_strategy": {"slug": "not-a-real-slug"}}, graph=_no_peers_graph())


async def test_run_protocol_rejects_retired_coordination_strategy(owner_id: uuid.UUID) -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {"nodes": [llm, agent], "edges": [agent_llm_edge]}

    async with get_session() as db:
        experiment = await create_experiment(
            db,
            name=f"coord-strategy-test-{uuid.uuid4().hex}",
            owner_id=owner_id,
            design_spec={"coordination_strategy": {"slug": "swarm_architecture"}},
        )
        experiment_id = experiment.id
        protocol = await create_protocol(
            db,
            name=f"coord-strategy-protocol-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment_id,
            graph=graph,
        )
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    try:
        await pe.run_protocol(run_id)
        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "failed"
            assert fetched.error is not None
            assert "no longer offered" in fetched.error
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
            await delete_experiment(db, experiment_id)


async def test_peer_collaboration_runs_the_graph_as_one_conversation(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same a->b canvas a sequential run would walk node-by-node instead
    starts one conversation at `a`, and `a`'s answer is the run's result. `b`
    doesn't run here because nothing asked it to -- consultation is the lead
    agent's choice, made inside its own run."""
    import asaree.services.agent_messenger as am

    ran: list[str] = []

    async def fake_run_agent_node(node, **_kwargs):
        ran.append(node["id"])
        return f"{node['id']} answered", None, None, None

    monkeypatch.setattr(am, "_run_agent_node", fake_run_agent_node)

    graph = _peer_graph("a", "b")
    async with get_session() as db:
        experiment = await create_experiment(
            db,
            name=f"peer-collab-{uuid.uuid4().hex}",
            owner_id=owner_id,
            design_spec={"coordination_strategy": {"slug": "peer_collaboration"}},
        )
        experiment_id = experiment.id
        protocol = await create_protocol(
            db,
            name=f"peer-collab-protocol-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment_id,
            graph=graph,
        )
        protocol_id = protocol.id
        run_id = (await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)).id

    try:
        await pe.run_protocol(run_id)
        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "completed"
            assert ran == ["a"]
            assert fetched.node_runs["a"]["output_text"] == "a answered"
            assert fetched.conversation["entry_agent_id"] == "a"
            assert [(m["from_agent_id"], m["to_agent_id"]) for m in fetched.conversation["messages"]] == [
                ("user", "a"),
                ("a", "user"),
            ]
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
            await delete_experiment(db, experiment_id)


async def test_supervisor_architecture_runs_every_agent_end_to_end(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contrast with the peer test above is the point: the same fan-out
    canvas leaves `b` unrun under Peer Collaboration (nothing asked it to) and
    runs every agent under Supervisor, because ASAREE dispatches the turns
    rather than offering them. `run_protocol`'s own branch is what's under test
    here -- the orchestration itself is covered in test_supervisor_architecture.
    """
    import asaree.services.agent_messenger as am

    ran: list[str] = []

    async def fake_run_agent_node(node, **_kwargs):
        ran.append(node["id"])
        return f"{node['id']} answered", None, None, None

    monkeypatch.setattr(am, "_run_agent_node", fake_run_agent_node)

    graph = _supervisor_graph(reviewer=False, workers=2)
    async with get_session() as db:
        experiment = await create_experiment(
            db,
            name=f"supervisor-{uuid.uuid4().hex}",
            owner_id=owner_id,
            design_spec=_SUPERVISOR_SPEC,
        )
        experiment_id = experiment.id
        protocol = await create_protocol(
            db,
            name=f"supervisor-protocol-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment_id,
            graph=graph,
        )
        protocol_id = protocol.id
        run_id = (await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)).id

    try:
        await pe.run_protocol(run_id)
        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "completed"
            assert sorted(ran) == ["sup", "sup", "w1", "w2"]
            # The supervisor's synthesis is the cell's result -- its node run is
            # what the write-back path scores, not whichever node a topological
            # sort happened to end on.
            assert fetched.node_runs["sup"]["output_text"] == "sup answered"
            assert {nid: r["status"] for nid, r in fetched.node_runs.items()} == {
                "sup": "completed",
                "w1": "completed",
                "w2": "completed",
            }
            assert fetched.conversation["entry_agent_id"] == "sup"
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
            await delete_experiment(db, experiment_id)


# --- validate_single_node_runnable / single-node "Play" runs -----------------


def test_validate_single_node_runnable_missing_node_raises() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {"nodes": [agent], "edges": [agent_llm_edge]}
    with pytest.raises(ProtocolValidationError, match="No such node"):
        pe.validate_single_node_runnable(graph, "does-not-exist")


def test_validate_single_node_runnable_rejects_non_agent_type() -> None:
    node = _node("g1", "critic_gate")
    graph = {"nodes": [node], "edges": []}
    with pytest.raises(ProtocolValidationError, match="Only Agent and Sub-Agent nodes"):
        pe.validate_single_node_runnable(graph, "g1")


def test_validate_single_node_runnable_rejects_a_node_with_upstream_input() -> None:
    upstream = _node("u", "agent")
    downstream, downstream_llm_edge = _agent_with_llm("d")
    graph = {"nodes": [upstream, downstream], "edges": _edges(("u", "d")) + [downstream_llm_edge]}
    with pytest.raises(ProtocolValidationError, match="upstream input"):
        pe.validate_single_node_runnable(graph, "d")


def test_validate_single_node_runnable_rejects_zero_llm_connections() -> None:
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    with pytest.raises(ProtocolValidationError, match="must have exactly one Model connection"):
        pe.validate_single_node_runnable(graph, "a")


def test_validate_single_node_runnable_rejects_llm_edge_from_wrong_node_type() -> None:
    agent = _node("a", "agent")
    not_an_llm = _node("x", "agent")
    graph = {"nodes": [agent, not_an_llm], "edges": [_llm_edge("x", "a")]}
    with pytest.raises(ProtocolValidationError, match="must come from a Model node"):
        pe.validate_single_node_runnable(graph, "a")


def test_validate_single_node_runnable_accepts_a_valid_standalone_agent() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {"nodes": [agent, _llm_node()], "edges": [agent_llm_edge]}
    assert pe.validate_single_node_runnable(graph, "a") is agent


# --- validate_conversation_entry ---------------------------------------------


def _conversation_graph(*agent_ids: str) -> dict:
    """Agents in a chain, each with its own LLM, joined by plain main edges."""
    nodes: list[dict] = [_llm_node()]
    edges: list[dict] = []
    for agent_id in agent_ids:
        agent, llm_edge = _agent_with_llm(agent_id)
        nodes.append(agent)
        edges.append(llm_edge)
    for source, target in zip(agent_ids, agent_ids[1:], strict=False):
        edges += _edges((source, target))
    return {"nodes": nodes, "edges": edges}


def test_validate_conversation_entry_rejects_a_missing_node() -> None:
    with pytest.raises(ProtocolValidationError, match="No such node"):
        pe.validate_conversation_entry(_conversation_graph("a", "b"), "nope")


def test_validate_conversation_entry_rejects_a_non_agent_node() -> None:
    graph = _conversation_graph("a", "b")
    graph["nodes"].append(_node("g1", "critic_gate"))
    with pytest.raises(ProtocolValidationError, match="Only Agent nodes"):
        pe.validate_conversation_entry(graph, "g1")


def test_validate_conversation_entry_rejects_an_agent_with_nobody_to_talk_to() -> None:
    with pytest.raises(ProtocolValidationError, match="nobody to talk to"):
        pe.validate_conversation_entry(_conversation_graph("a"), "a")


def test_validate_conversation_entry_rejects_a_peer_with_no_model() -> None:
    """A peer's own wiring is checked too: it will really run, and finding out
    mid-conversation costs the user a run they already paid for."""
    graph = _conversation_graph("a", "b")
    graph["edges"] = [e for e in graph["edges"] if e.get("target") != "b" or e.get("targetHandle") != "model"]
    with pytest.raises(ProtocolValidationError, match="exactly one Model connection"):
        pe.validate_conversation_entry(graph, "a")


def test_validate_conversation_entry_accepts_a_third_agent_on_the_canvas() -> None:
    """Three connected agents are three participants, not an error -- the
    validator asks nothing about how many peer edges the graph has."""
    graph = _conversation_graph("a", "b", "c")
    assert pe.validate_conversation_entry(graph, "b")["id"] == "b"


def test_validate_conversation_entry_ignores_an_unrelated_broken_node() -> None:
    """Scoped to the entry agent and its peers: a half-wired node in another
    corner of the same canvas has nothing to do with this conversation."""
    graph = _conversation_graph("a", "b")
    graph["nodes"].append(_node("stranded", "agent"))
    assert pe.validate_conversation_entry(graph, "a")["id"] == "a"


async def test_run_single_node_ignores_an_unrelated_broken_sibling_node(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of a narrower, per-node check: a single-node Play run
    must not fail because some OTHER node elsewhere in the same graph is
    unrelated and broken (e.g. missing its own Model connector) -- only
    topological_order's full-graph walk cares about that."""

    async def fake_run_agent_node(node, *, user_input, **_kwargs):
        return f"solo output for {node['id']} given {user_input!r}", None, None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    target, target_llm_edge = _agent_with_llm("target")
    target["data"]["config"] = {"prompt": "do the one thing", "goal": ""}
    broken_sibling = _node("broken", "agent")  # no Model connector at all

    graph = {"nodes": [target, broken_sibling, _llm_node()], "edges": [target_llm_edge]}

    async with get_session() as db:
        protocol = await create_protocol(
            db, name=f"single-node-test-{uuid.uuid4().hex}", owner_id=owner_id, graph=graph
        )
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id, target_node_id="target")
        run_id = run.id

    try:
        await pe.run_protocol(run_id)
        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "completed"
            assert fetched.node_runs.keys() == {"target"}  # the broken sibling is never touched
            assert fetched.node_runs["target"]["output_text"] == "solo output for target given 'do the one thing'"
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)


async def test_run_single_node_computes_adhoc_workspace_id_when_experiment_linked(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single-node Play run never has a real cell_label, but still gets a
    stable per-run workspace when its protocol is linked to an experiment
    (see _compute_workspace_id's own "adhoc" fallback)."""
    received_workspace_ids = []

    async def fake_run_agent_node(node, *, workspace_id=None, **_kwargs):
        received_workspace_ids.append(workspace_id)
        return f"solo output for {node['id']}", None, None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    target, target_llm_edge = _agent_with_llm("target")
    graph = {"nodes": [target, _llm_node()], "edges": [target_llm_edge]}

    async with get_session() as db:
        experiment = await create_experiment(db, name=f"single-node-ws-e2e-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db,
            name=f"single-node-ws-test-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment_id,
            graph=graph,
        )
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id, target_node_id="target")
        run_id = run.id

    try:
        await pe.run_protocol(run_id)
        assert received_workspace_ids == [f"{experiment_id}/adhoc-{run_id}"]
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
            await delete_experiment(db, experiment_id)


async def test_run_single_node_with_upstream_input_fails_cleanly(owner_id: uuid.UUID) -> None:
    upstream = _node("u", "agent")
    downstream, downstream_llm_edge = _agent_with_llm("d")
    graph = {"nodes": [upstream, downstream, _llm_node()], "edges": _edges(("u", "d")) + [downstream_llm_edge]}

    async with get_session() as db:
        protocol = await create_protocol(
            db, name=f"single-node-upstream-test-{uuid.uuid4().hex}", owner_id=owner_id, graph=graph
        )
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id, target_node_id="d")
        run_id = run.id

    try:
        await pe.run_protocol(run_id)
        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "failed"
            assert fetched.error is not None
            assert "upstream input" in fetched.error
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
