"""Canonical names for structural fields in persisted protocol graphs."""

from __future__ import annotations

import hashlib
import json
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


def functional_protocol_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical execution-relevant definition of *graph*.

    Canvas layout and React Flow bookkeeping remain useful in the autosaved
    draft, but they do not change what production executes. Node ids are
    retained because prompt references and edges address them; edge ids are
    omitted because execution identifies an edge by its endpoints and handles.
    Sorting makes equivalent JSON arrays produce the same fingerprint.
    """
    normalized = normalize_protocol_graph(graph)
    nodes = normalized.get("nodes")
    edges = normalized.get("edges")

    functional_nodes = [
        {
            "id": node.get("id"),
            "type": node.get("type"),
            "data": deepcopy(node.get("data")),
        }
        for node in nodes or []
        if isinstance(node, dict)
    ]
    functional_edges = [
        {
            "source": edge.get("source"),
            "target": edge.get("target"),
            "sourceHandle": edge.get("sourceHandle"),
            "targetHandle": edge.get("targetHandle"),
        }
        for edge in edges or []
        if isinstance(edge, dict)
    ]

    def canonical_json(value: dict[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    functional_nodes.sort(key=canonical_json)
    functional_edges.sort(key=canonical_json)
    return {"nodes": functional_nodes, "edges": functional_edges}


def functional_protocol_graph_hash(graph: dict[str, Any]) -> str:
    """Return a stable SHA-256 fingerprint of the functional canvas."""
    encoded = json.dumps(
        functional_protocol_graph(graph),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["functional_protocol_graph", "functional_protocol_graph_hash", "normalize_protocol_graph"]
