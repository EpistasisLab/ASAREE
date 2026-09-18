"""Small, producer-neutral helpers for reading protocol graphs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def node_map(graph: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    nodes = graph.get("nodes")
    if not isinstance(nodes, Sequence) or isinstance(nodes, str):
        return {}
    return {
        str(node.get("id")): node for node in nodes if isinstance(node, Mapping) and isinstance(node.get("id"), str)
    }


def directly_connected_tool_pair(
    graph: Mapping[str, Any], agent_node_id: str, tool_node_id: str
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, bool]:
    nodes = node_map(graph)
    edges = graph.get("edges")
    connected = bool(
        isinstance(edges, Sequence)
        and not isinstance(edges, str)
        and any(
            isinstance(edge, Mapping)
            and edge.get("source") == tool_node_id
            and edge.get("target") == agent_node_id
            and edge.get("targetHandle") == "tool"
            for edge in edges
        )
    )
    return nodes.get(agent_node_id), nodes.get(tool_node_id), connected


__all__ = ["directly_connected_tool_pair", "node_map"]
