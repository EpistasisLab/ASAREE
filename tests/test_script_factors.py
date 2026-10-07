"""Script treatments control availability without changing Tool treatments."""

import copy
import uuid
from unittest.mock import AsyncMock

import pytest

from asaree.services.design_generation import DesignValidationError, generate_design_cells, get_design_impact
from asaree.services.factor_bindings import validate_factor_bindings
from asaree.services.protocol_execution import (
    _ambient_meta_for,
    _resolve_script_configs,
    _resolve_script_tool_config,
    _resolve_tool_config,
    apply_factor_bindings,
)
from asaree.services.script_factors import test_script_factor_values as preview_values
from asaree.services.script_factors import validate_script_factors, validate_tool_step_script_factors


def script_graph(mode="script_selection"):
    ids = ["a", "b"]
    graph = {
        "nodes": [
            {
                "id": "agent",
                "type": "agent",
                "data": {
                    "factor_bindings": {"script_selection": "Scripts"},
                    "script_factor_mode": mode,
                    "script_selection": ids if mode == "script_toggle" else ["a"],
                },
            },
            *[
                {
                    "id": id,
                    "type": "script",
                    "data": {
                        "config": {
                            "name": id,
                            "language": "python",
                            "code": f"print('{id}')",
                            "enabled": False,
                        }
                    },
                }
                for id in ids
            ],
            {
                "id": "mcp",
                "type": "mcp_tool",
                "data": {
                    "config": {
                        "server_name": "search",
                        "tool_names": ["fetch"],
                    }
                },
            },
        ],
        "edges": [{"source": id, "target": "agent", "targetHandle": "tool"} for id in [*ids, "mcp"]],
    }
    spec = {
        "factors": [
            {
                "name": "Scripts",
                "level_type": mode,
                "levels": [ids, []] if mode == "script_toggle" else [[id] for id in ids],
            }
        ]
    }
    return graph, spec


@pytest.mark.parametrize("mode", ["script_selection", "script_toggle"])
def test_selection_overrides_disabled_nodes_and_leaves_mcp_tools_unchanged(mode):
    graph, spec = script_graph(mode)
    original = copy.deepcopy(graph)
    validate_factor_bindings(spec, graph)
    for chosen in (["b"], ["a", "b"], []):
        patched = apply_factor_bindings(graph, {"Scripts": chosen})
        assert [config["node_id"] for config in _resolve_script_configs(patched, "agent")] == chosen
        assert bool(_resolve_script_tool_config(patched, "agent")["tool_names"]) == bool(chosen)
        assert _resolve_tool_config(patched, "agent") == {"server_names": ["search"], "tool_names": ["search.fetch"]}
    assert graph == original


def test_individual_off_removes_script_from_resolution_and_shared_agent():
    graph, _ = script_graph()
    graph["nodes"][0]["data"] = {}
    graph["nodes"].append({"id": "other", "type": "agent", "data": {}})
    graph["edges"].append({"source": "a", "target": "other", "targetHandle": "tool"})
    graph["nodes"][1]["data"]["factor_bindings"] = {"config.enabled": "On"}
    assert _resolve_script_configs(apply_factor_bindings(graph, {"On": False}), "agent") == []
    assert _resolve_script_configs(apply_factor_bindings(graph, {"On": False}), "other") == []
    assert _resolve_script_configs(apply_factor_bindings(graph, {"On": True}), "other")[0]["node_id"] == "a"


def test_agent_selection_does_not_enable_shared_script_for_other_agent():
    graph, _ = script_graph()
    graph["nodes"].append({"id": "other", "type": "agent", "data": {}})
    graph["edges"].append({"source": "a", "target": "other", "targetHandle": "tool"})
    patched = apply_factor_bindings(graph, {"Scripts": ["a"]})
    assert len(_resolve_script_configs(patched, "agent")) == 1
    assert _resolve_script_configs(patched, "other") == []


def test_off_removes_materialized_source_from_execution_context(monkeypatch):
    graph, _ = script_graph("script_toggle")
    materialize = []
    monkeypatch.setattr(
        "asaree.services.protocol_execution._materialize_script",
        lambda workspace, id, code: materialize.append(id) or f"/tmp/{id}.py",
    )
    off = apply_factor_bindings(graph, {"Scripts": []})
    assert "script_paths" not in _ambient_meta_for(off, "agent", script_workspace_id="workspace")
    assert materialize == []
    on = apply_factor_bindings(graph, {"Scripts": ["b"]})
    assert _ambient_meta_for(on, "agent", script_workspace_id="workspace")["script_paths"][0]["id"] == "b"
    assert materialize == ["b"]


def test_script_variants_replace_code_description_and_name():
    graph, _ = script_graph()
    graph["nodes"][0]["data"] = {}
    graph["nodes"][1]["data"]["factor_bindings"] = {"config": "Variants"}
    variant = {"name": "new", "description": "new algorithm", "language": "python", "code": "print(2)"}
    assert _resolve_script_configs(apply_factor_bindings(graph, {"Variants": variant}), "agent") == [
        {**variant, "node_id": "a"}
    ]


@pytest.mark.parametrize("change", ["missing", "duplicate", "foreign", "conflict", "empty-code"])
def test_invalid_or_incomplete_script_factors_block_generation(change):
    graph, spec = script_graph()
    if change == "missing":
        spec["factors"][0]["levels"].pop()
    elif change == "duplicate":
        spec["factors"][0]["levels"] = [["a"], ["a"]]
    elif change == "foreign":
        spec["factors"][0]["levels"] = [["a"], ["mcp"]]
    elif change == "conflict":
        graph["nodes"][1]["data"]["factor_bindings"] = {"config": "Variants"}
    else:
        graph["nodes"][1]["data"]["config"]["code"] = ""
    with pytest.raises(ValueError):
        validate_script_factors(spec, graph)


@pytest.mark.asyncio
async def test_preview_is_scoped_and_all_none_follow_baseline():
    graph, _ = script_graph()
    assert await preview_values(graph, None) == {"Scripts": ["a"]}
    assert await preview_values(graph, {"agent": "b"}, "agent") == {"Scripts": ["b"]}
    with pytest.raises(ValueError, match="belong"):
        await preview_values(graph, {"other": "a"}, "agent")
    graph["nodes"][0]["data"].update(script_factor_mode="script_toggle", script_selection=[])
    assert await preview_values(graph, None) == {"Scripts": []}
    assert await preview_values(graph, {"agent": "all"}) == {"Scripts": ["a", "b"]}


@pytest.mark.parametrize("change", ["off", "empty-code", "disabled-variant", "empty-variant"])
def test_required_tool_step_rejects_invalid_script_treatments(change):
    config = {"name": "score", "language": "python", "code": "print(1)"}
    script_data = {"config": config, "factor_bindings": {}}
    graph = {
        "nodes": [
            {
                "id": "step",
                "type": "tool_step",
                "data": {
                    "config": {
                        "arguments": {"code": {"source": "script_code"}},
                    }
                },
            },
            {"id": "script", "type": "script", "data": script_data},
        ],
        "edges": [{"source": "script", "target": "step", "targetHandle": "tool"}],
    }
    spec = {"factors": []}
    if change == "off":
        script_data["factor_bindings"] = {"config.enabled": "On"}
        spec["factors"] = [{"name": "On", "level_type": "boolean", "levels": [False, True]}]
    elif change == "empty-code":
        config["code"] = ""
    else:
        script_data["factor_bindings"] = {"config": "Variants"}
        variant = {**config, "enabled": False} if change == "disabled-variant" else {**config, "code": ""}
        spec["factors"] = [{"name": "Variants", "level_type": "script_config", "levels": [config, variant]}]
    with pytest.raises(ValueError, match="requires script_code"):
        validate_tool_step_script_factors(spec, graph)
    graph["nodes"][0]["data"]["config"]["arguments"] = {}
    validate_tool_step_script_factors(spec, graph)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,levels", [("script_selection", []), ("script_toggle", [[], []])])
async def test_empty_design_impact_is_readable(monkeypatch, mode, levels):
    monkeypatch.setattr("asaree.services.design_generation.get_current_revision", AsyncMock(return_value=None))
    impact = await get_design_impact(
        AsyncMock(),
        experiment_id=uuid.uuid4(),
        design_spec={
            "factors": [{"name": "Scripts", "level_type": mode, "levels": levels}],
        },
    )
    assert impact.proposed_cell_count == 0


@pytest.mark.asyncio
async def test_generation_rejects_required_script_off_before_creating_cells():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    graph = {
        "nodes": [
            {"id": "step", "type": "tool_step", "data": {"config": {"arguments": {"code": {"source": "script_code"}}}}},
            {
                "id": "script",
                "type": "script",
                "data": {"config": {"code": "print(1)"}, "factor_bindings": {"config.enabled": "On"}},
            },
        ],
        "edges": [{"source": "script", "target": "step", "targetHandle": "tool"}],
    }
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(owner_id=uuid.uuid4())
    db.execute.return_value = result
    db.scalar.return_value = SimpleNamespace(graph=graph)
    with pytest.raises(DesignValidationError, match="cannot include off"):
        await generate_design_cells(
            db, experiment_id=uuid.uuid4(), factors=[{"name": "On", "level_type": "boolean", "levels": [False, True]}]
        )
