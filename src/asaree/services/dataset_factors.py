"""Connector-owned dataset choices shared by design validation and test runs."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from asaree.services.datasets import get_dataset

DATASET_SELECTION = "dataset_selection"
DATASET_TOGGLE = "dataset_toggle"
DATASET_FACTOR_TYPES = {DATASET_SELECTION, DATASET_TOGGLE}


def connected_dataset_nodes(graph: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
    nodes = {node["id"]: node for node in graph.get("nodes") or []}
    return [
        nodes[edge["source"]]
        for edge in graph.get("edges") or []
        if edge.get("target") == agent_id
        and edge.get("targetHandle") in {"dataset", "resource", "tool"}
        and edge.get("source") in nodes
        and nodes[edge["source"]].get("type") == "dataset"
    ]


def validate_dataset_factor_structure(
    design_spec: dict[str, Any] | None, graph: dict[str, Any], *, complete: bool = True
) -> None:
    factors = {factor.get("name"): factor for factor in (design_spec or {}).get("factors") or []}
    bound = {
        (node.get("data") or {}).get("factor_bindings", {}).get(DATASET_SELECTION) for node in graph.get("nodes") or []
    }
    for name, factor in factors.items():
        if factor.get("level_type") in DATASET_FACTOR_TYPES and name not in bound:
            raise ValueError(f"Rebind or remove dataset factor {name!r}.")
    for node in graph.get("nodes") or []:
        data = node.get("data") or {}
        name = (data.get("factor_bindings") or {}).get(DATASET_SELECTION)
        if not name:
            continue
        factor = factors.get(name)
        if node.get("type") not in {"agent", "sub_agent"}:
            raise ValueError("Only an Agent dataset connector can own a dataset factor.")
        if not factor or factor.get("level_type") not in DATASET_FACTOR_TYPES:
            raise ValueError(f"Dataset connector factor {name!r} must declare a dataset factor type.")
        toggle = factor.get("level_type") == DATASET_TOGGLE
        if data.get("dataset_factor_mode", DATASET_SELECTION) != factor.get("level_type"):
            raise ValueError(f"Dataset factor {name!r}: canvas mode does not match the declared factor.")
        datasets = connected_dataset_nodes(graph, node["id"])
        if any(
            edge.get("target") == node["id"]
            and edge.get("source") in {dataset["id"] for dataset in datasets}
            and (edge.get("data") or {}).get("dataset_input", {}).get("mode") == "per_row"
            for edge in graph.get("edges") or []
        ):
            raise ValueError(f"Dataset factor {name!r}: use Whole dataset inputs before factorizing this connector.")
        ids = {(dataset.get("data") or {}).get("config", {}).get("dataset_id") for dataset in datasets}
        if complete and (None in ids or "" in ids):
            raise ValueError(f"Dataset factor {name!r}: disconnect or restore unavailable datasets.")
        if complete and len(ids) < (1 if toggle else 2):
            minimum = "one dataset" if toggle else "two different datasets"
            raise ValueError(f"Dataset factor {name!r}: connect at least {minimum}.")
        if any((dataset.get("data") or {}).get("factor_bindings") for dataset in datasets):
            raise ValueError(f"Dataset factor {name!r}: remove individual dataset factor bindings first.")
        levels = factor.get("levels") or []
        if toggle:
            if (
                len(levels) != 2
                or any(not isinstance(level, list) for level in levels)
                or sorted(len(level) for level in levels) != [0, len(ids)]
                or any(level and (set(level) != ids or len(level) != len(ids)) for level in levels)
            ):
                raise ValueError(f"Dataset factor {name!r}: levels must enable every connected dataset or none.")
            continue
        if any(not isinstance(level, list) or len(level) != 1 or level[0] not in ids for level in levels):
            raise ValueError(f"Dataset factor {name!r}: each level must name one connected dataset.")
        if len(levels) != len(ids) or {level[0] for level in levels} != ids:
            raise ValueError(f"Dataset factor {name!r}: levels must include every connected dataset exactly once.")


async def require_available_datasets(ids: list[str], owner_id: uuid.UUID, db: AsyncSession) -> None:
    for dataset_id in dict.fromkeys(ids):
        try:
            dataset = await get_dataset(db, uuid.UUID(dataset_id))
        except (ValueError, TypeError, AttributeError):
            dataset = None
        if dataset is None or dataset.owner_id != owner_id:
            raise ValueError("A dataset factor references an unavailable dataset. Disconnect or restore it.")


async def validate_dataset_factors(
    design_spec: dict[str, Any] | None, graph: dict[str, Any], owner_id: uuid.UUID, db: AsyncSession
) -> None:
    validate_dataset_factor_structure(design_spec, graph)
    ids = [
        dataset_id
        for factor in (design_spec or {}).get("factors") or []
        if factor.get("level_type") in DATASET_FACTOR_TYPES
        for level in factor.get("levels") or []
        for dataset_id in level
    ]
    await require_available_datasets(ids, owner_id, db)


async def test_dataset_factor_values(
    graph: dict[str, Any],
    selections: dict[str, str] | None,
    owner_id: uuid.UUID,
    db: AsyncSession,
    node_id: str | None = None,
) -> dict[str, Any]:
    """Validate preview choices against the publication and retain run provenance."""
    selections = selections or {}
    owners = {
        node["id"]: node
        for node in graph.get("nodes") or []
        if (node.get("data") or {}).get("factor_bindings", {}).get(DATASET_SELECTION)
        and (node_id is None or node["id"] == node_id)
    }
    if set(selections) - owners.keys():
        raise ValueError("Dataset selection must belong to a dataset factor in this run.")
    values: dict[str, Any] = {}
    for agent_id, node in owners.items():
        data = node.get("data") or {}
        connected = [
            (dataset.get("data") or {}).get("config", {}).get("dataset_id")
            for dataset in connected_dataset_nodes(graph, agent_id)
        ]
        if any(
            (dataset.get("data") or {}).get("factor_bindings") for dataset in connected_dataset_nodes(graph, agent_id)
        ):
            raise ValueError("Remove individual dataset factor bindings before testing this connector factor.")
        baseline = data.get(DATASET_SELECTION) or []
        if data.get("dataset_factor_mode") == DATASET_TOGGLE:
            selected = selections.get(agent_id, "all" if baseline else "none")
            if selected not in {"all", "none"}:
                raise ValueError("Select all enabled or all disabled for the test run.")
            chosen = list(dict.fromkeys(connected)) if selected == "all" else []
            await require_available_datasets(chosen, owner_id, db)
            values[data["factor_bindings"][DATASET_SELECTION]] = chosen
            continue
        selected = selections.get(agent_id, baseline[0] if baseline else None)
        if not selected or selected not in connected:
            raise ValueError("Select an available connected dataset for the test run.")
        await require_available_datasets([selected], owner_id, db)
        values[data["factor_bindings"][DATASET_SELECTION]] = [selected]
    return values
