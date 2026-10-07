"""Connector-owned knowledge choices shared by design validation and test runs."""

from __future__ import annotations

import uuid
from typing import Any

from asaree.services import okf_bundles, okf_documents

KNOWLEDGE_SELECTION = "knowledge_selection"
KNOWLEDGE_TOGGLE = "knowledge_toggle"
KNOWLEDGE_FACTOR_TYPES = {KNOWLEDGE_SELECTION, KNOWLEDGE_TOGGLE}


def knowledge_id(node: dict[str, Any]) -> str | None:
    config = (node.get("data") or {}).get("config") or {}
    return config.get("bundle_id") or config.get("document_id")


def connected_knowledge_nodes(graph: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
    nodes = {node["id"]: node for node in graph.get("nodes") or []}
    return [
        nodes[edge["source"]]
        for edge in graph.get("edges") or []
        if edge.get("target") == agent_id
        and edge.get("targetHandle") == "knowledge"
        and edge.get("source") in nodes
        and nodes[edge["source"]].get("type") in {"okf_bundle", "okf_document"}
    ]


def validate_knowledge_factor_structure(
    design_spec: dict[str, Any] | None, graph: dict[str, Any], *, complete: bool = True
) -> None:
    factors = {factor.get("name"): factor for factor in (design_spec or {}).get("factors") or []}
    bound = {
        (node.get("data") or {}).get("factor_bindings", {}).get(KNOWLEDGE_SELECTION)
        for node in graph.get("nodes") or []
    }
    for name, factor in factors.items():
        if factor.get("level_type") in KNOWLEDGE_FACTOR_TYPES and name not in bound:
            raise ValueError(f"Rebind or remove knowledge factor {name!r}.")
    for node in graph.get("nodes") or []:
        data = node.get("data") or {}
        name = (data.get("factor_bindings") or {}).get(KNOWLEDGE_SELECTION)
        if not name:
            continue
        factor = factors.get(name)
        if node.get("type") not in {"agent", "sub_agent"}:
            raise ValueError("Only an Agent knowledge connector can own a knowledge factor.")
        if not factor or factor.get("level_type") not in KNOWLEDGE_FACTOR_TYPES:
            raise ValueError(f"Knowledge connector factor {name!r} must declare a knowledge factor type.")
        toggle = factor.get("level_type") == KNOWLEDGE_TOGGLE
        if data.get("knowledge_factor_mode", KNOWLEDGE_SELECTION) != factor.get("level_type"):
            raise ValueError(f"Knowledge factor {name!r}: canvas mode does not match the declared factor.")
        sources = connected_knowledge_nodes(graph, node["id"])
        ids = {knowledge_id(source) for source in sources}
        if complete and (None in ids or "" in ids):
            raise ValueError(f"Knowledge factor {name!r}: disconnect or restore unavailable knowledge.")
        if complete and len(ids) < (1 if toggle else 2):
            minimum = "one knowledge source" if toggle else "two different knowledge sources"
            raise ValueError(f"Knowledge factor {name!r}: connect at least {minimum}.")
        if any((source.get("data") or {}).get("factor_bindings") for source in sources):
            raise ValueError(f"Knowledge factor {name!r}: remove individual knowledge factor bindings first.")
        levels = factor.get("levels") or []
        if toggle:
            if (
                len(levels) != 2
                or any(not isinstance(level, list) for level in levels)
                or sorted(len(level) for level in levels) != [0, len(ids)]
                or any(level and (set(level) != ids or len(level) != len(ids)) for level in levels)
            ):
                raise ValueError(
                    f"Knowledge factor {name!r}: levels must enable every connected knowledge source or none."
                )
            continue
        if any(not isinstance(level, list) or len(level) != 1 or level[0] not in ids for level in levels):
            raise ValueError(f"Knowledge factor {name!r}: each level must name one connected knowledge source.")
        if len(levels) != len(ids) or {level[0] for level in levels} != ids:
            raise ValueError(
                f"Knowledge factor {name!r}: levels must include every connected knowledge source exactly once."
            )


async def require_available_knowledge(ids: list[str], owner_id: uuid.UUID) -> None:
    if not ids:
        return
    bundles = await okf_bundles.list_bundles(owner_id)
    documents = await okf_documents.list_documents(owner_id)
    available = {str(item.id) for item in [*bundles, *documents]}
    if any(value not in available for value in ids):
        raise ValueError("A knowledge factor references unavailable knowledge. Disconnect or restore it.")


async def validate_knowledge_factors(
    design_spec: dict[str, Any] | None, graph: dict[str, Any], owner_id: uuid.UUID
) -> None:
    validate_knowledge_factor_structure(design_spec, graph)
    ids = [
        source_id
        for factor in (design_spec or {}).get("factors") or []
        if factor.get("level_type") in KNOWLEDGE_FACTOR_TYPES
        for level in factor.get("levels") or []
        for source_id in level
    ]
    await require_available_knowledge(ids, owner_id)


async def test_knowledge_factor_values(
    graph: dict[str, Any], selections: dict[str, str] | None, owner_id: uuid.UUID, node_id: str | None = None
) -> dict[str, Any]:
    """Validate preview choices against the publication and retain run provenance."""
    selections = selections or {}
    owners = {
        node["id"]: node
        for node in graph.get("nodes") or []
        if (node.get("data") or {}).get("factor_bindings", {}).get(KNOWLEDGE_SELECTION)
        and (node_id is None or node["id"] == node_id)
    }
    if set(selections) - owners.keys():
        raise ValueError("Knowledge selection must belong to a knowledge factor in this run.")
    values: dict[str, Any] = {}
    for agent_id, node in owners.items():
        data = node.get("data") or {}
        connected = [knowledge_id(knowledge) for knowledge in connected_knowledge_nodes(graph, agent_id)]
        if any(
            (knowledge.get("data") or {}).get("factor_bindings")
            for knowledge in connected_knowledge_nodes(graph, agent_id)
        ):
            raise ValueError("Remove individual knowledge factor bindings before testing this connector factor.")
        baseline = data.get(KNOWLEDGE_SELECTION) or []
        if data.get("knowledge_factor_mode") == KNOWLEDGE_TOGGLE:
            selected = selections.get(agent_id, "all" if baseline else "none")
            if selected not in {"all", "none"}:
                raise ValueError("Select all enabled or all disabled for the test run.")
            chosen = list(dict.fromkeys(connected)) if selected == "all" else []
            await require_available_knowledge(chosen, owner_id)
            values[data["factor_bindings"][KNOWLEDGE_SELECTION]] = chosen
            continue
        selected = selections.get(agent_id, baseline[0] if baseline else None)
        if not selected or selected not in connected:
            raise ValueError("Select an available connected knowledge source for the test run.")
        await require_available_knowledge([selected], owner_id)
        values[data["factor_bindings"][KNOWLEDGE_SELECTION]] = [selected]
    return values
