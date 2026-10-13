"""Tool connector selections preserve pinned servers and per-node allow-lists."""

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from asaree.services.design_generation import get_design_impact
from asaree.services.factor_bindings import validate_factor_bindings
from asaree.services.protocol_execution import _resolve_tool_config, apply_factor_bindings
from asaree.services.tool_factors import test_tool_factor_values as preview_values
from asaree.services.tool_factors import validate_tool_factors


def tool_graph(mode="tool_selection"):
    ids = ["tool-a", "tool-b"]
    graph = {
        "nodes": [
            {
                "id": "agent",
                "type": "agent",
                "data": {
                    "factor_bindings": {"tool_selection": "Tools"},
                    "tool_selection": ids if mode == "tool_toggle" else [ids[0]],
                    "tool_factor_mode": mode,
                },
            },
            *[
                {
                    "id": id,
                    "type": kind,
                    "data": {
                        "config": {
                            "server_id": str(uuid.uuid4()),
                            "server_name": id,
                            "tool_names": ["search", "fetch"],
                            "enabled": False,
                        }
                    },
                }
            for id, kind in zip(ids, ["mcp_tool", "mcp_client_tool"], strict=True)
            ],
            {"id": "script", "type": "script", "data": {"config": {"code": "print(1)"}}},
        ],
        "edges": [{"source": id, "target": "agent", "targetHandle": "tool"} for id in [*ids, "script"]],
    }
    spec = {
        "factors": [
            {
                "name": "Tools",
                "level_type": mode,
                "levels": [ids, []] if mode == "tool_toggle" else [[id] for id in ids],
            }
        ]
    }
    return graph, spec, ids


@pytest.mark.parametrize("mode", ["tool_selection", "tool_toggle"])
def test_cell_selection_overrides_disabled_nodes_and_preserves_allow_lists(mode):
    graph, spec, ids = tool_graph(mode)
    original = copy.deepcopy(graph)
    validate_factor_bindings(spec, graph)
    for chosen in ([ids[1]], ids, []):
        resolved = _resolve_tool_config(apply_factor_bindings(graph, {"Tools": chosen}), "agent")
        assert resolved == {
            "server_names": chosen,
            "tool_names": [f"{id}.{name}" for id in chosen for name in ["search", "fetch"]],
        }
    assert graph == original


def test_shared_node_selection_is_scoped_to_its_agent():
    graph, _, ids = tool_graph()
    graph["nodes"].append({"id": "other", "type": "agent", "data": {}})
    graph["edges"].append({"source": ids[0], "target": "other", "targetHandle": "tool"})
    patched = apply_factor_bindings(graph, {"Tools": [ids[0]]})
    assert _resolve_tool_config(patched, "agent")["server_names"] == [ids[0]]
    assert _resolve_tool_config(patched, "other")["server_names"] == []


@pytest.mark.parametrize("enabled", [True, False])
def test_individual_enabled_and_allow_list_factors_still_work(enabled):
    graph, _, ids = tool_graph()
    graph["nodes"][0]["data"] = {}
    graph["nodes"][1]["data"]["factor_bindings"] = {"config.enabled": "On", "config.tool_names": "Allowed"}
    resolved = _resolve_tool_config(apply_factor_bindings(graph, {"On": enabled, "Allowed": ["fetch"]}), "agent")
    assert resolved["tool_names"] == ([f"{ids[0]}.fetch"] if enabled else [])


@pytest.mark.parametrize("change", ["one", "missing", "duplicate", "foreign", "conflict", "mode"])
def test_invalid_or_conflicting_design_is_rejected(change):
    graph, spec, ids = tool_graph()
    factor = spec["factors"][0]
    if change == "one":
        graph["edges"].pop(1)
        factor["levels"] = [[ids[0]]]
    elif change == "missing":
        factor["levels"].pop()
    elif change == "duplicate":
        factor["levels"] = [[ids[0]], [ids[0]]]
    elif change == "foreign":
        factor["levels"] = [[ids[0]], ["script"]]
    elif change == "mode":
        graph["nodes"][0]["data"]["tool_factor_mode"] = "tool_toggle"
    else:
        graph["nodes"][1]["data"]["factor_bindings"] = {"config.tool_names": "Allowed"}
    with pytest.raises(ValueError):
        validate_factor_bindings(spec, graph)


@pytest.mark.parametrize(
    "levels", [[["tool-a"], []], [["tool-a", "tool-b", "tool-a"], []], [["tool-a", "tool-b"], ["tool-a", "tool-b"]]]
)
def test_toggle_requires_all_connected_nodes_or_none(levels):
    graph, spec, _ = tool_graph("tool_toggle")
    spec["factors"][0]["levels"] = levels
    with pytest.raises(ValueError, match="every connected tool or none"):
        validate_factor_bindings(spec, graph)


@pytest.mark.asyncio
async def test_preview_defaults_choices_and_availability(monkeypatch):
    graph, spec, ids = tool_graph()
    owner = uuid.uuid4()
    server = SimpleNamespace(
        owner_id=owner, is_system=False, capabilities={"tools": [{"name": "search"}, {"name": "fetch"}]}
    )
    get = AsyncMock(return_value=server)
    monkeypatch.setattr("asaree.services.tool_factors.mcp_service.get_server", get)
    assert await preview_values(graph, None, owner) == {"Tools": [ids[0]]}
    assert await preview_values(graph, {"agent": ids[1]}, owner, "agent") == {"Tools": [ids[1]]}
    await validate_tool_factors(spec, graph, owner)
    with pytest.raises(ValueError, match="belong"):
        await preview_values(graph, {"other": ids[0]}, owner)
    with pytest.raises(ValueError, match="connected"):
        await preview_values(graph, {"agent": "script"}, owner)
    get.return_value = None
    with pytest.raises(ValueError, match="unavailable"):
        await preview_values(graph, None, owner)


@pytest.mark.asyncio
async def test_toggle_none_does_not_require_servers_and_all_checks_every_server(monkeypatch):
    graph, spec, ids = tool_graph("tool_toggle")
    owner = uuid.uuid4()
    get = AsyncMock(return_value=None)
    monkeypatch.setattr("asaree.services.tool_factors.mcp_service.get_server", get)
    assert await preview_values(graph, {"agent": "none"}, owner) == {"Tools": []}
    get.assert_not_awaited()
    with pytest.raises(ValueError, match="unavailable"):
        await preview_values(graph, {"agent": "all"}, owner)
    with pytest.raises(ValueError, match="all enabled"):
        await preview_values(graph, {"agent": ids[0]}, owner)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["other-owner", "empty", "unknown-tool"])
async def test_unavailable_server_or_allow_list_is_rejected(monkeypatch, invalid):
    graph, spec, _ = tool_graph()
    owner = uuid.uuid4()
    server = SimpleNamespace(
        owner_id=uuid.uuid4() if invalid == "other-owner" else owner,
        is_system=False,
        capabilities={"tools": [{"name": "search"}, {"name": "fetch"}]},
    )
    if invalid == "empty":
        graph["nodes"][1]["data"]["config"]["tool_names"] = []
    elif invalid == "unknown-tool":
        graph["nodes"][1]["data"]["config"]["tool_names"] = ["deleted"]
    monkeypatch.setattr("asaree.services.tool_factors.mcp_service.get_server", AsyncMock(return_value=server))
    with pytest.raises(ValueError):
        await validate_tool_factors(spec, graph, owner)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,levels", [("tool_selection", []), ("tool_toggle", [[], []])])
async def test_incomplete_design_has_zero_impact(monkeypatch, mode, levels):
    monkeypatch.setattr("asaree.services.design_generation.get_current_revision", AsyncMock(return_value=None))
    impact = await get_design_impact(
        AsyncMock(),
        experiment_id=uuid.uuid4(),
        design_spec={"factors": [{"name": "Tools", "level_type": mode, "levels": levels}]},
    )
    assert impact.proposed_cell_count == 0
