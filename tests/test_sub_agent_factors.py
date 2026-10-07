"""Sub-Agent factors preserve worker configurations and scope delegation."""

import copy
import uuid

import pytest

from asaree.services.factor_bindings import validate_factor_bindings
from asaree.services.protocol_execution import (
    ProtocolValidationError,
    _sub_agent_ids,
    apply_factor_bindings,
    topological_order,
)
from asaree.services.sub_agent_factors import test_sub_agent_factor_values as preview_values


def worker_graph():
    graph = {
        "nodes": [
            {
                "id": "parent",
                "type": "agent",
                "data": {"factor_bindings": {"sub_agent_selection": "Workers"}, "sub_agent_selection": ["a"]},
            },
            *[
                {"id": id, "type": "sub_agent", "data": {"active": False, "config": {"prompt": id}}}
                for id in ["a", "b"]
            ],
        ],
        "edges": [{"source": id, "target": "parent", "targetHandle": "sub_agents"} for id in ["a", "b"]],
    }
    spec = {"factors": [{"name": "Workers", "level_type": "sub_agent_selection", "levels": [["a"], ["b"]]}]}
    return graph, spec


def test_selection_preserves_draft_and_overrides_disabled_workers():
    graph, spec = worker_graph()
    original = copy.deepcopy(graph)
    validate_factor_bindings(spec, graph)
    patched = apply_factor_bindings(graph, {"Workers": ["b"]})
    assert _sub_agent_ids(patched, "parent") == ["b"]
    assert patched["nodes"][1]["data"]["active"] is False
    assert patched["nodes"][2]["data"]["active"] is True
    assert patched["nodes"][2]["data"]["config"] == {"prompt": "b"}
    assert graph == original


def test_individual_on_off_only_controls_its_worker():
    graph, _ = worker_graph()
    graph["nodes"][0]["data"] = {}
    graph["nodes"][1]["data"]["factor_bindings"] = {"active": "Enabled"}
    assert _sub_agent_ids(apply_factor_bindings(graph, {"Enabled": True}), "parent") == ["a"]
    assert _sub_agent_ids(apply_factor_bindings(graph, {"Enabled": False}), "parent") == []


@pytest.mark.parametrize("selection", [["a", "b"], []])
def test_toggle_includes_all_disabled_workers_or_none(selection):
    graph, spec = worker_graph()
    graph["nodes"][0]["data"].update(sub_agent_factor_mode="sub_agent_toggle", sub_agent_selection=[])
    spec["factors"][0].update(level_type="sub_agent_toggle", levels=[["a", "b"], []])
    validate_factor_bindings(spec, graph)
    assert _sub_agent_ids(apply_factor_bindings(graph, {"Workers": selection}), "parent") == selection


def test_prompt_factor_coexists_but_individual_active_factor_conflicts():
    graph, spec = worker_graph()
    graph["nodes"][1]["data"]["factor_bindings"] = {"config.prompt": "Prompt"}
    spec["factors"].append({"name": "Prompt", "level_type": "text", "levels": ["a", "other"]})
    validate_factor_bindings(spec, graph)
    graph["nodes"][1]["data"]["factor_bindings"]["active"] = "Enabled"
    with pytest.raises(ValueError, match="individual sub-agent on/off"):
        validate_factor_bindings(spec, graph)


@pytest.mark.parametrize("levels", [[["a"], ["a"]], [["foreign"], ["b"]], [["a"]]])
def test_selection_rejects_duplicate_foreign_or_missing_workers(levels):
    graph, spec = worker_graph()
    spec["factors"][0]["levels"] = levels
    with pytest.raises(ValueError):
        validate_factor_bindings(spec, graph)


@pytest.mark.asyncio
async def test_preview_choices_and_node_scope():
    graph, _ = worker_graph()
    owner = uuid.uuid4()
    assert await preview_values(graph, None, owner) == {"Workers": ["a"]}
    assert await preview_values(graph, {"parent": "b"}, owner) == {"Workers": ["b"]}
    with pytest.raises(ValueError, match="belong"):
        await preview_values(graph, {"parent": "b"}, owner, "a")
    with pytest.raises(ValueError, match="connected"):
        await preview_values(graph, {"parent": "foreign"}, owner)
    graph["nodes"][0]["data"]["sub_agent_factor_mode"] = "sub_agent_toggle"
    assert await preview_values(graph, {"parent": "all"}, owner) == {"Workers": ["a", "b"]}
    assert await preview_values(graph, {"parent": "none"}, owner) == {"Workers": []}


def test_selected_disabled_worker_requires_a_model_at_validation():
    graph, _ = worker_graph()
    graph["nodes"].append({"id": "model", "type": "model_openai", "data": {"config": {}}})
    graph["edges"].append({"source": "model", "target": "parent", "targetHandle": "model"})
    with pytest.raises(ProtocolValidationError, match="exactly one Model"):
        topological_order(graph)
