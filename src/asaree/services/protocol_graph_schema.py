"""Canonical names for structural fields in persisted protocol graphs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_LEGACY_MODEL_NODE_TYPES = {
    "llm_anthropic": "model_anthropic",
    "llm_openai": "model_openai",
    "llm_azure_foundry": "model_azure_foundry",
    "llm_openrouter": "model_openrouter",
    "llm_local": "model_local",
}
_LEGACY_MODEL_HANDLES = frozenset({"ai", "llm"})


def normalize_protocol_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Return *graph* with legacy Model discriminators made canonical.

    Only schema-owned node ``type`` and edge handle fields are rewritten.
    Node ids, labels, prompts, and arbitrary config values are opaque user
    data and must never be changed by a vocabulary migration.
    """
    normalized = deepcopy(graph)
    nodes = normalized.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("type")
            if isinstance(node_type, str) and node_type in _LEGACY_MODEL_NODE_TYPES:
                node["type"] = _LEGACY_MODEL_NODE_TYPES[node_type]

    edges = normalized.get("edges")
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            for field in ("sourceHandle", "targetHandle"):
                if edge.get(field) in _LEGACY_MODEL_HANDLES:
                    edge[field] = "model"
    return normalized


__all__ = ["normalize_protocol_graph"]
