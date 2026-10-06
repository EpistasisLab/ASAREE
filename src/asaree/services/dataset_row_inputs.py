"""Validation and normalization for Dataset-to-Agent edge input settings."""

from __future__ import annotations

import uuid
from collections import defaultdict
from contextlib import suppress
from typing import Any

_MISSING = object()


class DatasetRowInputError(ValueError):
    """A Dataset input configuration that does not match the edge contract."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"dataset_row.{code}: {message}")


def normalize_dataset_input(value: object = _MISSING) -> dict[str, str | list[str]]:
    """Return a fresh canonical Dataset edge input configuration.

    An omitted configuration keeps the historical whole-dataset behavior.
    Explicit JSON null is malformed and must be distinguished from omission.
    """
    if value is _MISSING:
        return {"mode": "whole_dataset"}
    if not isinstance(value, dict):
        raise DatasetRowInputError("invalid_configuration", "configuration must be an object")

    unknown_fields = set(value) - {"mode", "columns"}
    if unknown_fields:
        raise DatasetRowInputError("invalid_configuration", "configuration contains unknown fields")

    mode = value.get("mode")
    if mode == "whole_dataset":
        if "columns" not in value or value["columns"] == []:
            return {"mode": "whole_dataset"}
        raise DatasetRowInputError("invalid_columns", "whole_dataset does not accept columns")

    if mode == "per_row":
        columns = value.get("columns")
        if not isinstance(columns, list) or not columns:
            raise DatasetRowInputError("invalid_columns", "per_row requires a non-empty columns array")
        if any(not isinstance(column, str) or not column.strip() for column in columns):
            raise DatasetRowInputError("invalid_columns", "columns must be non-empty, non-whitespace strings")
        if len(set(columns)) != len(columns):
            raise DatasetRowInputError("invalid_columns", "columns must be distinct")
        return {"mode": "per_row", "columns": list(columns)}

    raise DatasetRowInputError("invalid_mode", "mode must be 'whole_dataset' or 'per_row'")


_DATASET_NODE_TYPES = frozenset({"dataset"})
_AGENT_NODE_TYPES = frozenset({"agent"})
_DATASET_HANDLES = frozenset({"dataset", "resource", "tool"})


def resolve_dataset_row_plan(graph: dict[str, Any], design_spec: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Resolve and validate the Dataset-to-Agent inputs that drive cell rows.

    This is deliberately graph-only: it neither looks up registered datasets nor
    changes the supplied graph. Registration identity is checked at runtime.
    """
    nodes = {str(node.get("id")): node for node in graph.get("nodes") or [] if node.get("id") is not None}
    bindings: list[dict[str, Any]] = []
    drivers: set[str] = set()
    whole_by_id: dict[str, list[str]] = defaultdict(list)
    whole_by_name: dict[str, list[str]] = defaultdict(list)

    # dataset_input is a reserved edge contract; reject it even where the edge
    # would otherwise be ignored by type, handle, or enabled state.
    for edge in graph.get("edges") or []:
        edge_data = edge.get("data") or {}
        if "dataset_input" not in edge_data:
            continue
        source = nodes.get(str(edge.get("source")))
        target = nodes.get(str(edge.get("target")))
        valid_source = source is not None and source.get("type") in _DATASET_NODE_TYPES
        valid_target = target is not None and target.get("type") in _AGENT_NODE_TYPES
        if not valid_source or not valid_target:
            raise DatasetRowInputError(
                "misplaced_configuration",
                "dataset_input is only valid on a Dataset-to-Agent edge",
            )
        if edge.get("targetHandle") not in _DATASET_HANDLES:
            raise DatasetRowInputError("misplaced_configuration", "dataset_input requires a Dataset connector handle")

    for edge in graph.get("edges") or []:
        source = nodes.get(str(edge.get("source")))
        target = nodes.get(str(edge.get("target")))
        if (
            source is None
            or source.get("type") not in _DATASET_NODE_TYPES
            or target is None
            or target.get("type") not in _AGENT_NODE_TYPES
        ):
            continue
        if edge.get("targetHandle") not in _DATASET_HANDLES:
            continue

        edge_data = edge.get("data") or {}
        if edge_data.get("factor_bindings"):
            raise DatasetRowInputError("unsupported_edge_factor", "dataset_input edges cannot have factor bindings")
        config = (source.get("data") or {}).get("config", _MISSING)
        # Validate attached configuration even for disabled source nodes.
        normalized = normalize_dataset_input(edge_data.get("dataset_input", _MISSING))
        if config is _MISSING or not isinstance(config, dict):
            raise DatasetRowInputError("invalid_dataset_config", "Dataset node configuration must be an object")
        enabled = config.get("enabled", True)
        if not isinstance(enabled, bool):
            raise DatasetRowInputError("invalid_dataset_config", "Dataset enabled must be a boolean")
        dataset_name = config.get("dataset_name")
        dataset_id = config.get("dataset_id")
        if dataset_name is not None and not isinstance(dataset_name, str):
            raise DatasetRowInputError("invalid_dataset_config", "dataset_name must be a string")

        if normalized["mode"] == "per_row":
            if not isinstance(dataset_id, str):
                raise DatasetRowInputError(
                    "invalid_dataset_id",
                    "per_row requires a registered dataset UUID in dataset_id",
                )
            try:
                driver_id = str(uuid.UUID(dataset_id))
            except (ValueError, AttributeError) as exc:
                raise DatasetRowInputError("invalid_dataset_id", "per_row dataset_id must be a valid UUID") from exc
            if enabled:
                # A factor on any part of config can replace/alter the driver.
                data = source.get("data") or {}
                for field_path in data.get("factor_bindings") or {}:
                    if field_path == "config" or field_path.startswith("config."):
                        raise DatasetRowInputError(
                            "factorized_driver",
                            "a row-driving Dataset configuration cannot be factor-bound",
                        )
                drivers.add(driver_id)
                bindings.append(
                    {
                        "edge_id": str(edge.get("id", "")),
                        "dataset_node_id": str(source.get("id", "")),
                        "agent_node_id": str(target.get("id", "")),
                        "columns": list(normalized["columns"]),
                        "_dataset_id": driver_id,
                    }
                )
        elif enabled:
            if dataset_id is not None:
                with suppress(ValueError, TypeError, AttributeError):
                    whole_by_id[str(uuid.UUID(dataset_id))].append(str(source.get("id", "")))
            if isinstance(dataset_name, str):
                whole_by_name[dataset_name].append(str(source.get("id", "")))

    if len(drivers) > 1:
        raise DatasetRowInputError("multiple_drivers", "per_row inputs must use one registered dataset")
    if not drivers:
        return None
    driver_id = next(iter(drivers))

    driver_nodes = {binding["dataset_node_id"] for binding in bindings}
    for node_id in driver_nodes:
        data = nodes[node_id].get("data") or {}
        for field_path in data.get("factor_bindings") or {}:
            if field_path == "config" or field_path.startswith("config."):
                raise DatasetRowInputError(
                    "factorized_driver",
                    "a row-driving Dataset configuration cannot be factor-bound",
                )
    if whole_by_id.get(driver_id):
        raise DatasetRowInputError("mixed_driver_modes", "one dataset cannot use both per_row and whole_dataset inputs")

    # Legacy whole-dataset configs can be name-only. In this pure resolver the
    # exact matching name is the only identity signal available.
    driver_names = {
        (nodes[binding["dataset_node_id"]].get("data") or {}).get("config", {}).get("dataset_name")
        for binding in bindings
    }
    if any(name and name in whole_by_name for name in driver_names):
        raise DatasetRowInputError("mixed_driver_modes", "one dataset cannot use both per_row and whole_dataset inputs")

    columns_by_agent: dict[str, list[str]] = {}
    for binding in bindings:
        agent_id = binding["agent_node_id"]
        columns = binding["columns"]
        if agent_id in columns_by_agent and columns_by_agent[agent_id] != columns:
            raise DatasetRowInputError(
                "conflicting_agent_columns",
                "duplicate row inputs to one Agent must select identical columns",
            )
        columns_by_agent[agent_id] = columns
    return {
        "driver_dataset_id": driver_id,
        "bindings": [{key: value for key, value in binding.items() if key != "_dataset_id"} for binding in bindings],
    }
