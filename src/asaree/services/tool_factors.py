"""Connector-owned tool choices shared by design validation and test runs."""

from __future__ import annotations

import uuid
from typing import Any

from motoro.services import mcp_service

TOOL_SELECTION = "tool_selection"
TOOL_TOGGLE = "tool_toggle"
TOOL_FACTOR_TYPES = {TOOL_SELECTION, TOOL_TOGGLE}
MCP_TOOL_NODE_TYPES = {"mcp_tool", "mcp_scikit_learn", "mcp_client_tool"}


def connected_tool_nodes(graph: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
    nodes = {node["id"]: node for node in graph.get("nodes") or []}
    return [
        nodes[edge["source"]]
        for edge in graph.get("edges") or []
        if edge.get("target") == agent_id
        and edge.get("targetHandle") == "tool"
        and edge.get("source") in nodes
        and nodes[edge["source"]].get("type") in MCP_TOOL_NODE_TYPES
    ]


def validate_tool_factor_structure(
    design_spec: dict[str, Any] | None, graph: dict[str, Any], *, complete: bool = True
) -> None:
    factors = {factor.get("name"): factor for factor in (design_spec or {}).get("factors") or []}
    bound = {
        (node.get("data") or {}).get("factor_bindings", {}).get(TOOL_SELECTION) for node in graph.get("nodes") or []
    }
    for name, factor in factors.items():
        if factor.get("level_type") in TOOL_FACTOR_TYPES and name not in bound:
            raise ValueError(f"Rebind or remove tool factor {name!r}.")
    for node in graph.get("nodes") or []:
        data = node.get("data") or {}
        name = (data.get("factor_bindings") or {}).get(TOOL_SELECTION)
        if not name:
            continue
        factor = factors.get(name)
        if node.get("type") not in {"agent", "sub_agent"}:
            raise ValueError("Only an Agent tool connector can own a tool factor.")
        if not factor or factor.get("level_type") not in TOOL_FACTOR_TYPES:
            raise ValueError(f"Tool connector factor {name!r} must declare a tool factor type.")
        toggle = factor.get("level_type") == TOOL_TOGGLE
        if data.get("tool_factor_mode", TOOL_SELECTION) != factor.get("level_type"):
            raise ValueError(f"Tool factor {name!r}: canvas mode does not match the declared factor.")
        tools = connected_tool_nodes(graph, node["id"])
        ids = {tool["id"] for tool in tools}
        if complete and (None in ids or "" in ids):
            raise ValueError(f"Tool factor {name!r}: disconnect or restore unavailable tools.")
        if complete and len(ids) < (1 if toggle else 2):
            minimum = "one tool" if toggle else "two different tools"
            raise ValueError(f"Tool factor {name!r}: connect at least {minimum}.")
        if any((tool.get("data") or {}).get("factor_bindings") for tool in tools):
            raise ValueError(f"Tool factor {name!r}: remove individual tool factor bindings first.")
        levels = factor.get("levels") or []
        if toggle:
            if (
                len(levels) != 2
                or any(not isinstance(level, list) for level in levels)
                or sorted(len(level) for level in levels) != [0, len(ids)]
                or any(level and (set(level) != ids or len(level) != len(ids)) for level in levels)
            ):
                raise ValueError(f"Tool factor {name!r}: levels must enable every connected tool or none.")
            continue
        if any(not isinstance(level, list) or len(level) != 1 or level[0] not in ids for level in levels):
            raise ValueError(f"Tool factor {name!r}: each level must name one connected tool.")
        if len(levels) != len(ids) or {level[0] for level in levels} != ids:
            raise ValueError(f"Tool factor {name!r}: levels must include every connected tool exactly once.")


async def require_available_tools(nodes: list[dict[str, Any]], owner_id: uuid.UUID) -> None:
    for node in nodes:
        config = (node.get("data") or {}).get("config") or {}
        try:
            server = await mcp_service.get_server(uuid.UUID(config.get("server_id")))
        except (ValueError, TypeError, AttributeError):
            server = None
        if server is None or not (server.owner_id == owner_id or server.is_system):
            raise ValueError("A tool factor references an unavailable server. Disconnect or restore it.")
        available = {tool["name"] for tool in (server.capabilities or {}).get("tools", [])}
        if not config.get("tool_names") or any(name not in available for name in config["tool_names"]):
            raise ValueError("A tool factor has an empty or unavailable allow-list. Configure its allowed tools.")


async def validate_tool_factors(design_spec: dict[str, Any] | None, graph: dict[str, Any], owner_id: uuid.UUID) -> None:
    validate_tool_factor_structure(design_spec, graph)
    nodes = [
        tool
        for node in graph.get("nodes") or []
        if ((node.get("data") or {}).get("factor_bindings") or {}).get(TOOL_SELECTION)
        for tool in connected_tool_nodes(graph, node["id"])
    ]
    await require_available_tools(nodes, owner_id)


async def test_tool_factor_values(
    graph: dict[str, Any], selections: dict[str, str] | None, owner_id: uuid.UUID, node_id: str | None = None
) -> dict[str, Any]:
    """Validate preview choices against the publication and retain run provenance."""
    selections = selections or {}
    owners = {
        node["id"]: node
        for node in graph.get("nodes") or []
        if (node.get("data") or {}).get("factor_bindings", {}).get(TOOL_SELECTION)
        and (node_id is None or node["id"] == node_id)
    }
    if set(selections) - owners.keys():
        raise ValueError("Tool selection must belong to a tool factor in this run.")
    values: dict[str, Any] = {}
    for agent_id, node in owners.items():
        data = node.get("data") or {}
        connected = [tool["id"] for tool in connected_tool_nodes(graph, agent_id)]
        if any((tool.get("data") or {}).get("factor_bindings") for tool in connected_tool_nodes(graph, agent_id)):
            raise ValueError("Remove individual tool factor bindings before testing this connector factor.")
        baseline = data.get(TOOL_SELECTION) or []
        if data.get("tool_factor_mode") == TOOL_TOGGLE:
            selected = selections.get(agent_id, "all" if baseline else "none")
            if selected not in {"all", "none"}:
                raise ValueError("Select all enabled or all disabled for the test run.")
            chosen = list(dict.fromkeys(connected)) if selected == "all" else []
            await require_available_tools(
                [tool for tool in connected_tool_nodes(graph, agent_id) if tool["id"] in chosen], owner_id
            )
            values[data["factor_bindings"][TOOL_SELECTION]] = chosen
            continue
        selected = selections.get(agent_id, baseline[0] if baseline else None)
        if not selected or selected not in connected:
            raise ValueError("Select an available connected tool for the test run.")
        await require_available_tools(
            [tool for tool in connected_tool_nodes(graph, agent_id) if tool["id"] == selected], owner_id
        )
        values[data["factor_bindings"][TOOL_SELECTION]] = [selected]
    return values
