"""Shared resource groups require identical wiring and one factor for all recipients."""

from __future__ import annotations

from typing import Any

GROUP_SPECS = {
    "skill": ({"skill"}, {"skill"}, "skill_selection"),
    "dataset": ({"dataset"}, {"dataset", "resource", "tool"}, "dataset_selection"),
    "knowledge": ({"okf_bundle", "okf_document"}, {"knowledge"}, "knowledge_selection"),
    "script": ({"script"}, {"tool"}, "script_selection"),
    "tool": ({"mcp_tool", "mcp_scikit_learn", "mcp_client_tool"}, {"tool"}, "tool_selection"),
    "sub_agent": ({"sub_agent"}, {"sub_agents"}, "sub_agent_selection"),
    "pattern": ({"pattern_reason_act", "pattern_single_agent_baseline"}, {"architectural_pattern"}, "pattern_override"),
}


def validate_shared_factor_groups(graph: dict[str, Any], kind: str) -> None:
    types, handles, path = GROUP_SPECS[kind]
    nodes = {node["id"]: node for node in graph.get("nodes") or []}
    edges = graph.get("edges") or []
    factors: dict[str, list[dict[str, Any]]] = {}
    for node in nodes.values():
        name = ((node.get("data") or {}).get("factor_bindings") or {}).get(path)
        if name:
            factors.setdefault(name, []).append(node)

    def recipients(source: str) -> set[str]:
        return {
            edge["target"]
            for edge in edges
            if edge.get("source") == source and edge.get("targetHandle") in handles and edge.get("target") in nodes
        }

    for name, owners in factors.items():
        owner_ids = {node["id"] for node in owners}
        members = {
            edge["source"]
            for edge in edges
            if edge.get("target") in owner_ids
            and edge.get("targetHandle") in handles
            and edge.get("source") in nodes
            and nodes[edge["source"]].get("type") in types
        }
        if not members or any(node.get("type") not in {"agent", "sub_agent"} for node in owners):
            raise ValueError(f"Shared {kind} factor {name!r}: restore its group connections or remove the factor.")
        expected = recipients(next(iter(members)))
        all_members = {
            edge["source"]
            for edge in edges
            if edge.get("target") in expected
            and edge.get("targetHandle") in handles
            and edge.get("source") in nodes
            and nodes[edge["source"]].get("type") in types
        }
        if kind != "pattern" and any(recipients(member) != expected for member in all_members):
            raise ValueError(
                f"Shared {kind} factor {name!r}: every node in the group must connect to exactly the same Agents. "
                "Restore matching connections or remove the factor."
            )
        if expected != owner_ids:
            raise ValueError(
                f"Shared {kind} factor {name!r} must apply to every Agent connected to its group. "
                "Restore the connections or remove and recreate the factor."
            )
        baseline = (owners[0].get("data") or {}).get(path)
        mode = (owners[0].get("data") or {}).get(f"{kind}_factor_mode", f"{kind}_selection")
        if any(
            (owner.get("data") or {}).get(path) != baseline
            or (owner.get("data") or {}).get(f"{kind}_factor_mode", f"{kind}_selection") != mode
            for owner in owners
        ):
            raise ValueError(f"Shared {kind} factor {name!r}: all recipients must use the same default level and mode.")


def shared_test_selections(
    graph: dict[str, Any], selections: dict[str, str] | None, kind: str, node_id: str | None = None
) -> dict[str, str]:
    """A choice for any recipient applies once to the whole shared factor."""
    validate_shared_factor_groups(graph, kind)
    path = GROUP_SPECS[kind][2]
    selections = selections or {}
    owners = {
        node["id"]: node
        for node in graph.get("nodes") or []
        if ((node.get("data") or {}).get("factor_bindings") or {}).get(path)
        and (node_id is None or node["id"] == node_id)
    }
    if set(selections) - owners.keys():
        raise ValueError(f"{kind.capitalize()} selection must belong to a factor in this run.")
    choices: dict[str, str] = {}
    for owner_id, choice in selections.items():
        name = owners[owner_id]["data"]["factor_bindings"][path]
        if name in choices and choices[name] != choice:
            raise ValueError(f"Shared factor {name!r} cannot select different levels for different Agents.")
        choices[name] = choice
    return {
        owner_id: choices[node["data"]["factor_bindings"][path]]
        for owner_id, node in owners.items()
        if node["data"]["factor_bindings"][path] in choices
    }
