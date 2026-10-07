"""Connector-owned script choices shared by design validation and test runs."""

from __future__ import annotations

from typing import Any

SCRIPT_SELECTION = "script_selection"
SCRIPT_TOGGLE = "script_toggle"
SCRIPT_FACTOR_TYPES = {SCRIPT_SELECTION, SCRIPT_TOGGLE}
SCRIPT_NODE_TYPES = {"script"}


def connected_script_nodes(graph: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
    nodes = {node["id"]: node for node in graph.get("nodes") or []}
    return [
        nodes[edge["source"]]
        for edge in graph.get("edges") or []
        if edge.get("target") == agent_id
        and edge.get("targetHandle") == "tool"
        and edge.get("source") in nodes
        and nodes[edge["source"]].get("type") in SCRIPT_NODE_TYPES
    ]


def validate_script_factor_structure(
    design_spec: dict[str, Any] | None, graph: dict[str, Any], *, complete: bool = True
) -> None:
    factors = {factor.get("name"): factor for factor in (design_spec or {}).get("factors") or []}
    for script in graph.get("nodes") or []:
        if script.get("type") != "script":
            continue
        bindings = (script.get("data") or {}).get("factor_bindings") or {}
        if "config" in bindings and any(path.startswith("config.") for path in bindings):
            raise ValueError("Script variants cannot be combined with individual Script field factors.")
    bound = {
        (node.get("data") or {}).get("factor_bindings", {}).get(SCRIPT_SELECTION) for node in graph.get("nodes") or []
    }
    for name, factor in factors.items():
        if factor.get("level_type") in SCRIPT_FACTOR_TYPES and name not in bound:
            raise ValueError(f"Rebind or remove script factor {name!r}.")
    for node in graph.get("nodes") or []:
        data = node.get("data") or {}
        name = (data.get("factor_bindings") or {}).get(SCRIPT_SELECTION)
        if not name:
            continue
        factor = factors.get(name)
        if node.get("type") not in {"agent", "sub_agent"}:
            raise ValueError("Only an Agent script connector can own a script factor.")
        if not factor or factor.get("level_type") not in SCRIPT_FACTOR_TYPES:
            raise ValueError(f"Script connector factor {name!r} must declare a script factor type.")
        toggle = factor.get("level_type") == SCRIPT_TOGGLE
        if data.get("script_factor_mode", SCRIPT_SELECTION) != factor.get("level_type"):
            raise ValueError(f"Script factor {name!r}: canvas mode does not match the declared factor.")
        scripts = connected_script_nodes(graph, node["id"])
        ids = {script["id"] for script in scripts}
        if complete and (None in ids or "" in ids):
            raise ValueError(f"Script factor {name!r}: disconnect or restore unavailable scripts.")
        if complete and len(ids) < (1 if toggle else 2):
            minimum = "one script" if toggle else "two different scripts"
            raise ValueError(f"Script factor {name!r}: connect at least {minimum}.")
        if any((script.get("data") or {}).get("factor_bindings") for script in scripts):
            raise ValueError(f"Script factor {name!r}: remove individual script factor bindings first.")
        levels = factor.get("levels") or []
        if toggle:
            if (
                len(levels) != 2
                or any(not isinstance(level, list) for level in levels)
                or sorted(len(level) for level in levels) != [0, len(ids)]
                or any(level and (set(level) != ids or len(level) != len(ids)) for level in levels)
            ):
                raise ValueError(f"Script factor {name!r}: levels must enable every connected script or none.")
            continue
        if any(not isinstance(level, list) or len(level) != 1 or level[0] not in ids for level in levels):
            raise ValueError(f"Script factor {name!r}: each level must name one connected script.")
        if len(levels) != len(ids) or {level[0] for level in levels} != ids:
            raise ValueError(f"Script factor {name!r}: levels must include every connected script exactly once.")


def require_available_scripts(nodes: list[dict[str, Any]]) -> None:
    if any(not str(((node.get("data") or {}).get("config") or {}).get("code") or "").strip() for node in nodes):
        raise ValueError("A script factor references a Script without code. Configure or disconnect it.")


def validate_script_factors(design_spec: dict[str, Any] | None, graph: dict[str, Any]) -> None:
    validate_script_factor_structure(design_spec, graph)
    nodes = [
        script
        for node in graph.get("nodes") or []
        if ((node.get("data") or {}).get("factor_bindings") or {}).get(SCRIPT_SELECTION)
        for script in connected_script_nodes(graph, node["id"])
    ]
    require_available_scripts(nodes)


async def test_script_factor_values(
    graph: dict[str, Any], selections: dict[str, str] | None, node_id: str | None = None
) -> dict[str, Any]:
    """Validate preview choices against the publication and retain run provenance."""
    selections = selections or {}
    owners = {
        node["id"]: node
        for node in graph.get("nodes") or []
        if (node.get("data") or {}).get("factor_bindings", {}).get(SCRIPT_SELECTION)
        and (node_id is None or node["id"] == node_id)
    }
    if set(selections) - owners.keys():
        raise ValueError("Script selection must belong to a script factor in this run.")
    values: dict[str, Any] = {}
    for agent_id, node in owners.items():
        data = node.get("data") or {}
        connected = [script["id"] for script in connected_script_nodes(graph, agent_id)]
        if any((script.get("data") or {}).get("factor_bindings") for script in connected_script_nodes(graph, agent_id)):
            raise ValueError("Remove individual script factor bindings before testing this connector factor.")
        baseline = data.get(SCRIPT_SELECTION) or []
        if data.get("script_factor_mode") == SCRIPT_TOGGLE:
            selected = selections.get(agent_id, "all" if baseline else "none")
            if selected not in {"all", "none"}:
                raise ValueError("Select all enabled or all disabled for the test run.")
            chosen = list(dict.fromkeys(connected)) if selected == "all" else []
            require_available_scripts(
                [script for script in connected_script_nodes(graph, agent_id) if script["id"] in chosen]
            )
            values[data["factor_bindings"][SCRIPT_SELECTION]] = chosen
            continue
        selected = selections.get(agent_id, baseline[0] if baseline else None)
        if not selected or selected not in connected:
            raise ValueError("Select an available connected script for the test run.")
        require_available_scripts(
            [script for script in connected_script_nodes(graph, agent_id) if script["id"] == selected]
        )
        values[data["factor_bindings"][SCRIPT_SELECTION]] = [selected]
    return values


def validate_tool_step_script_factors(design_spec: dict[str, Any] | None, graph: dict[str, Any]) -> None:
    """Every treatment must supply code when an active Tool Step requires it."""
    factors = {factor.get("name"): factor for factor in (design_spec or {}).get("factors") or []}
    for step in graph.get("nodes") or []:
        data = step.get("data") or {}
        if step.get("type") != "tool_step" or data.get("active") is False:
            continue
        arguments = (data.get("config") or {}).get("arguments") or {}
        if not any(isinstance(arg, dict) and arg.get("source") == "script_code" for arg in arguments.values()):
            continue
        scripts = connected_script_nodes(graph, step["id"])
        label = data.get("label") or step["id"]
        if not scripts:
            raise ValueError(f"Tool Step {label!r} requires script_code: connect a Script with code.")
        for script in scripts:
            script_data = script.get("data") or {}
            config = script_data.get("config") or {}
            bindings = script_data.get("factor_bindings") or {}
            config_factor = factors.get(bindings.get("config"))
            variants = (config_factor.get("levels") or []) if config_factor else [config]
            enabled_factor = factors.get(bindings.get("config.enabled"))
            if enabled_factor and any(level is not True for level in enabled_factor.get("levels") or []):
                raise ValueError(f"Tool Step {label!r} requires script_code: its Script factor cannot include off.")
            for variant in variants:
                if (
                    not isinstance(variant, dict)
                    or (not enabled_factor and variant.get("enabled") is False)
                    or not str(variant.get("code") or "").strip()
                ):
                    raise ValueError(
                        f"Tool Step {label!r} requires script_code: every Script variant needs enabled code."
                    )
