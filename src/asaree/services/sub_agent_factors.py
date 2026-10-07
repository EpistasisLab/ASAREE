"""Connector-owned Sub-Agent choices shared by design validation and test runs."""

from __future__ import annotations

import uuid
from typing import Any

SUB_AGENT_SELECTION = "sub_agent_selection"
SUB_AGENT_TOGGLE = "sub_agent_toggle"
SUB_AGENT_FACTOR_TYPES = {SUB_AGENT_SELECTION, SUB_AGENT_TOGGLE}


def connected_sub_agent_nodes(graph: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
    nodes = {node["id"]: node for node in graph.get("nodes") or []}
    return [
        nodes[edge["source"]]
        for edge in graph.get("edges") or []
        if edge.get("target") == agent_id
        and edge.get("targetHandle") == "sub_agents"
        and edge.get("source") in nodes
        and nodes[edge["source"]].get("type") == "sub_agent"
    ]


def validate_sub_agent_factor_structure(
    design_spec: dict[str, Any] | None, graph: dict[str, Any], *, complete: bool = True
) -> None:
    factors = {factor.get("name"): factor for factor in (design_spec or {}).get("factors") or []}
    bound = {
        (node.get("data") or {}).get("factor_bindings", {}).get(SUB_AGENT_SELECTION)
        for node in graph.get("nodes") or []
    }
    for name, factor in factors.items():
        if factor.get("level_type") in SUB_AGENT_FACTOR_TYPES and name not in bound:
            raise ValueError(f"Rebind or remove sub-agent factor {name!r}.")
    for node in graph.get("nodes") or []:
        data = node.get("data") or {}
        name = (data.get("factor_bindings") or {}).get(SUB_AGENT_SELECTION)
        if not name:
            continue
        factor = factors.get(name)
        if node.get("type") != "agent":
            raise ValueError("Only an Agent Sub-Agents connector can own a sub-agent factor.")
        if not factor or factor.get("level_type") not in SUB_AGENT_FACTOR_TYPES:
            raise ValueError(f"Sub-Agent connector factor {name!r} must declare a sub-agent factor type.")
        toggle = factor.get("level_type") == SUB_AGENT_TOGGLE
        if data.get("sub_agent_factor_mode", SUB_AGENT_SELECTION) != factor.get("level_type"):
            raise ValueError(f"Sub-Agent factor {name!r}: canvas mode does not match the declared factor.")
        sub_agents = connected_sub_agent_nodes(graph, node["id"])
        ids = {sub_agent["id"] for sub_agent in sub_agents}
        if complete and (None in ids or "" in ids):
            raise ValueError(f"Sub-Agent factor {name!r}: disconnect or restore unavailable sub-agents.")
        if complete and len(ids) < (1 if toggle else 2):
            minimum = "one sub-agent" if toggle else "two different sub-agents"
            raise ValueError(f"Sub-Agent factor {name!r}: connect at least {minimum}.")
        if any(((sub_agent.get("data") or {}).get("factor_bindings") or {}).get("active") for sub_agent in sub_agents):
            raise ValueError(f"Sub-Agent factor {name!r}: remove individual sub-agent on/off factor bindings first.")
        levels = factor.get("levels") or []
        if toggle:
            if (
                len(levels) != 2
                or any(not isinstance(level, list) for level in levels)
                or sorted(len(level) for level in levels) != [0, len(ids)]
                or any(level and (set(level) != ids or len(level) != len(ids)) for level in levels)
            ):
                raise ValueError(f"Sub-Agent factor {name!r}: levels must enable every connected sub-agent or none.")
            continue
        if any(not isinstance(level, list) or len(level) != 1 or level[0] not in ids for level in levels):
            raise ValueError(f"Sub-Agent factor {name!r}: each level must name one connected sub-agent.")
        if len(levels) != len(ids) or {level[0] for level in levels} != ids:
            raise ValueError(f"Sub-Agent factor {name!r}: levels must include every connected sub-agent exactly once.")


async def test_sub_agent_factor_values(
    graph: dict[str, Any], selections: dict[str, str] | None, owner_id: uuid.UUID, node_id: str | None = None
) -> dict[str, Any]:
    """Validate preview choices against the publication and retain run provenance."""
    selections = selections or {}
    owners = {
        node["id"]: node
        for node in graph.get("nodes") or []
        if (node.get("data") or {}).get("factor_bindings", {}).get(SUB_AGENT_SELECTION)
        and (node_id is None or node["id"] == node_id)
    }
    if set(selections) - owners.keys():
        raise ValueError("Sub-Agent selection must belong to a sub-agent factor in this run.")
    values: dict[str, Any] = {}
    for agent_id, node in owners.items():
        data = node.get("data") or {}
        connected = [sub_agent["id"] for sub_agent in connected_sub_agent_nodes(graph, agent_id)]
        if any(
            ((sub_agent.get("data") or {}).get("factor_bindings") or {}).get("active")
            for sub_agent in connected_sub_agent_nodes(graph, agent_id)
        ):
            raise ValueError("Remove individual sub-agent on/off factor bindings before testing this connector factor.")
        baseline = data.get(SUB_AGENT_SELECTION) or []
        if data.get("sub_agent_factor_mode") == SUB_AGENT_TOGGLE:
            selected = selections.get(agent_id, "all" if baseline else "none")
            if selected not in {"all", "none"}:
                raise ValueError("Select all enabled or all disabled for the test run.")
            chosen = list(dict.fromkeys(connected)) if selected == "all" else []
            values[data["factor_bindings"][SUB_AGENT_SELECTION]] = chosen
            continue
        selected = selections.get(agent_id, baseline[0] if baseline else None)
        if not selected or selected not in connected:
            raise ValueError("Select an available connected sub-agent for the test run.")
        values[data["factor_bindings"][SUB_AGENT_SELECTION]] = [selected]
    return values
