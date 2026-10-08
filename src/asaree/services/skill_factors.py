"""Connector-owned skill choices shared by design validation and test runs."""

from __future__ import annotations

import uuid
from typing import Any

from motoro.services import skill_service

from asaree.services.shared_factors import shared_test_selections, validate_shared_factor_groups

SKILL_SELECTION = "skill_selection"
SKILL_TOGGLE = "skill_toggle"
SKILL_FACTOR_TYPES = {SKILL_SELECTION, SKILL_TOGGLE}


def connected_skill_nodes(graph: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
    nodes = {node["id"]: node for node in graph.get("nodes") or []}
    return [
        nodes[edge["source"]]
        for edge in graph.get("edges") or []
        if edge.get("target") == agent_id
        and edge.get("targetHandle") == "skill"
        and edge.get("source") in nodes
        and nodes[edge["source"]].get("type") == "skill"
    ]


def validate_skill_factor_structure(
    design_spec: dict[str, Any] | None, graph: dict[str, Any], *, complete: bool = True
) -> None:
    validate_shared_factor_groups(graph, "skill")
    factors = {factor.get("name"): factor for factor in (design_spec or {}).get("factors") or []}
    bound = {
        (node.get("data") or {}).get("factor_bindings", {}).get(SKILL_SELECTION) for node in graph.get("nodes") or []
    }
    for name, factor in factors.items():
        if factor.get("level_type") in SKILL_FACTOR_TYPES and name not in bound:
            raise ValueError(f"Rebind or remove skill factor {name!r}.")
    for node in graph.get("nodes") or []:
        data = node.get("data") or {}
        name = (data.get("factor_bindings") or {}).get(SKILL_SELECTION)
        if not name:
            continue
        factor = factors.get(name)
        if node.get("type") not in {"agent", "sub_agent"}:
            raise ValueError("Only an Agent skill connector can own a skill factor.")
        if not factor or factor.get("level_type") not in SKILL_FACTOR_TYPES:
            raise ValueError(f"Skill connector factor {name!r} must declare a skill factor type.")
        toggle = factor.get("level_type") == SKILL_TOGGLE
        if data.get("skill_factor_mode", SKILL_SELECTION) != factor.get("level_type"):
            raise ValueError(f"Skill factor {name!r}: canvas mode does not match the declared factor.")
        skills = connected_skill_nodes(graph, node["id"])
        ids = {(skill.get("data") or {}).get("config", {}).get("skill_id") for skill in skills}
        if complete and (None in ids or "" in ids):
            raise ValueError(f"Skill factor {name!r}: disconnect or restore unavailable skills.")
        if complete and len(ids) < (1 if toggle else 2):
            minimum = "one skill" if toggle else "two different skills"
            raise ValueError(f"Skill factor {name!r}: connect at least {minimum}.")
        if any((skill.get("data") or {}).get("factor_bindings") for skill in skills):
            raise ValueError(f"Skill factor {name!r}: remove individual skill factor bindings first.")
        levels = factor.get("levels") or []
        if toggle:
            if (
                len(levels) != 2
                or any(not isinstance(level, list) for level in levels)
                or sorted(len(level) for level in levels) != [0, len(ids)]
                or any(level and (set(level) != ids or len(level) != len(ids)) for level in levels)
            ):
                raise ValueError(f"Skill factor {name!r}: levels must enable every connected skill or none.")
            continue
        if any(not isinstance(level, list) or len(level) != 1 or level[0] not in ids for level in levels):
            raise ValueError(f"Skill factor {name!r}: each level must name one connected skill.")
        if len(levels) != len(ids) or {level[0] for level in levels} != ids:
            raise ValueError(f"Skill factor {name!r}: levels must include every connected skill exactly once.")


async def require_available_skills(ids: list[str], owner_id: uuid.UUID) -> None:
    for skill_id in dict.fromkeys(ids):
        try:
            skill = await skill_service.get_skill(uuid.UUID(skill_id))
        except (ValueError, TypeError, AttributeError):
            skill = None
        if skill is None or not (skill.owner_id == owner_id or skill.is_system):
            raise ValueError("A skill factor references an unavailable skill. Disconnect or restore it.")


async def validate_skill_factors(
    design_spec: dict[str, Any] | None, graph: dict[str, Any], owner_id: uuid.UUID
) -> None:
    validate_skill_factor_structure(design_spec, graph)
    ids = [
        skill_id
        for factor in (design_spec or {}).get("factors") or []
        if factor.get("level_type") in SKILL_FACTOR_TYPES
        for level in factor.get("levels") or []
        for skill_id in level
    ]
    await require_available_skills(ids, owner_id)


async def test_skill_factor_values(
    graph: dict[str, Any], selections: dict[str, str] | None, owner_id: uuid.UUID, node_id: str | None = None
) -> dict[str, Any]:
    """Validate preview choices against the publication and retain run provenance."""
    selections = shared_test_selections(graph, selections, "skill", node_id)
    owners = {
        node["id"]: node
        for node in graph.get("nodes") or []
        if (node.get("data") or {}).get("factor_bindings", {}).get(SKILL_SELECTION)
        and (node_id is None or node["id"] == node_id)
    }
    if set(selections) - owners.keys():
        raise ValueError("Skill selection must belong to a skill factor in this run.")
    values: dict[str, Any] = {}
    for agent_id, node in owners.items():
        data = node.get("data") or {}
        connected = [
            (skill.get("data") or {}).get("config", {}).get("skill_id")
            for skill in connected_skill_nodes(graph, agent_id)
        ]
        if any((skill.get("data") or {}).get("factor_bindings") for skill in connected_skill_nodes(graph, agent_id)):
            raise ValueError("Remove individual skill factor bindings before testing this connector factor.")
        baseline = data.get(SKILL_SELECTION) or []
        if data.get("skill_factor_mode") == SKILL_TOGGLE:
            selected = selections.get(agent_id, "all" if baseline else "none")
            if selected not in {"all", "none"}:
                raise ValueError("Select all enabled or all disabled for the test run.")
            chosen = list(dict.fromkeys(connected)) if selected == "all" else []
            await require_available_skills(chosen, owner_id)
            values[data["factor_bindings"][SKILL_SELECTION]] = chosen
            continue
        selected = selections.get(agent_id, baseline[0] if baseline else None)
        if not selected or selected not in connected:
            raise ValueError("Select an available connected skill for the test run.")
        await require_available_skills([selected], owner_id)
        values[data["factor_bindings"][SKILL_SELECTION]] = [selected]
    return values
