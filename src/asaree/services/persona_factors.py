"""Connector-owned choices of persona node snapshots, independent of live library records."""

from __future__ import annotations

import uuid
from typing import Any

from asaree.services.shared_factors import shared_test_selections, validate_shared_factor_groups

PERSONA_SELECTION = "persona_selection"
PERSONA_TOGGLE = "persona_toggle"
PERSONA_FACTOR_TYPES = {PERSONA_SELECTION, PERSONA_TOGGLE}


def connected_persona_nodes(graph: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
    nodes = {node["id"]: node for node in graph.get("nodes") or []}
    return [
        nodes[edge["source"]]
        for edge in graph.get("edges") or []
        if edge.get("target") == agent_id
        and edge.get("targetHandle") == "persona"
        and edge.get("source") in nodes
        and nodes[edge["source"]].get("type") == "persona"
    ]


def validate_persona_factor_structure(
    design_spec: dict[str, Any] | None, graph: dict[str, Any], *, complete: bool = True
) -> None:
    validate_shared_factor_groups(graph, "persona")
    factors = {factor.get("name"): factor for factor in (design_spec or {}).get("factors") or []}
    bound = {
        (node.get("data") or {}).get("factor_bindings", {}).get(PERSONA_SELECTION) for node in graph.get("nodes") or []
    }
    for name, factor in factors.items():
        if factor.get("level_type") in PERSONA_FACTOR_TYPES and name not in bound:
            raise ValueError(f"Rebind or remove persona factor {name!r}.")
    for node in graph.get("nodes") or []:
        data = node.get("data") or {}
        name = (data.get("factor_bindings") or {}).get(PERSONA_SELECTION)
        if not name:
            continue
        factor = factors.get(name)
        if node.get("type") not in {"agent", "sub_agent"}:
            raise ValueError("Only an Agent persona connector can own a persona factor.")
        if not factor or factor.get("level_type") not in PERSONA_FACTOR_TYPES:
            raise ValueError(f"Persona connector factor {name!r} must declare a persona factor type.")
        toggle = factor.get("level_type") == PERSONA_TOGGLE
        if data.get("persona_factor_mode", PERSONA_SELECTION) != factor.get("level_type"):
            raise ValueError(f"Persona factor {name!r}: canvas mode does not match the declared factor.")
        personas = connected_persona_nodes(graph, node["id"])
        ids = {persona["id"] for persona in personas}
        if complete and (None in ids or "" in ids):
            raise ValueError(f"Persona factor {name!r}: disconnect or restore unavailable personas.")
        if complete and len(ids) < (1 if toggle else 2):
            minimum = "one persona" if toggle else "two different personas"
            raise ValueError(f"Persona factor {name!r}: connect at least {minimum}.")
        if any((persona.get("data") or {}).get("factor_bindings") for persona in personas):
            raise ValueError(f"Persona factor {name!r}: remove individual persona factor bindings first.")
        levels = factor.get("levels") or []
        if toggle:
            if (
                len(levels) != 2
                or any(not isinstance(level, list) for level in levels)
                or sorted(len(level) for level in levels) != [0, len(ids)]
                or any(level and (set(level) != ids or len(level) != len(ids)) for level in levels)
            ):
                raise ValueError(f"Persona factor {name!r}: levels must enable every connected persona or none.")
            continue
        if any(not isinstance(level, list) or len(level) != 1 or level[0] not in ids for level in levels):
            raise ValueError(f"Persona factor {name!r}: each level must name one connected persona.")
        if len(levels) != len(ids) or {level[0] for level in levels} != ids:
            raise ValueError(f"Persona factor {name!r}: levels must include every connected persona exactly once.")


async def validate_persona_factors(
    design_spec: dict[str, Any] | None, graph: dict[str, Any], owner_id: uuid.UUID
) -> None:
    validate_persona_factor_structure(design_spec, graph)


async def test_persona_factor_values(
    graph: dict[str, Any], selections: dict[str, str] | None, owner_id: uuid.UUID, node_id: str | None = None
) -> dict[str, Any]:
    """Validate preview choices against the publication and retain run provenance."""
    selections = shared_test_selections(graph, selections, "persona", node_id)
    owners = {
        node["id"]: node
        for node in graph.get("nodes") or []
        if (node.get("data") or {}).get("factor_bindings", {}).get(PERSONA_SELECTION)
        and (node_id is None or node["id"] == node_id)
    }
    if set(selections) - owners.keys():
        raise ValueError("Persona selection must belong to a persona factor in this run.")
    values: dict[str, Any] = {}
    for agent_id, node in owners.items():
        data = node.get("data") or {}
        personas = connected_persona_nodes(graph, agent_id)
        connected = [persona["id"] for persona in personas]
        if any((persona.get("data") or {}).get("factor_bindings") for persona in personas):
            raise ValueError("Remove individual persona factor bindings before testing this connector factor.")
        baseline = data.get(PERSONA_SELECTION) or []
        if data.get("persona_factor_mode") == PERSONA_TOGGLE:
            selected = selections.get(agent_id, "all" if baseline else "none")
            if selected not in {"all", "none"}:
                raise ValueError("Select all enabled or all disabled for the test run.")
            chosen = list(dict.fromkeys(connected)) if selected == "all" else []
            values[data["factor_bindings"][PERSONA_SELECTION]] = chosen
            continue
        selected = selections.get(agent_id, baseline[0] if baseline else None)
        if not selected or selected not in connected:
            raise ValueError("Select an available connected persona for the test run.")
        values[data["factor_bindings"][PERSONA_SELECTION]] = [selected]
    return values
