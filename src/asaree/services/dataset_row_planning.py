"""Pure planning of row executions for factorial replicate parents.

Callers remain responsible for the published graph's existing graph and
measurement guards. This module only verifies that factor substitution leaves
the resolved row driver and its ordered Agent/column bindings unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from asaree.services.dataset_row_csv import RowSource
from asaree.services.dataset_row_inputs import DatasetRowInputError, resolve_dataset_row_plan
from asaree.services.protocol_execution import apply_factor_bindings


def enumerate_row_candidates(
    *,
    parents: Sequence[dict],
    source: RowSource,
    graph: dict,
    design_spec: dict | None,
    protocol_revision_id: str,
) -> list[dict]:
    """Expand sorted replicate parents across original source rows.

    Earlier graph and measurement validation remains the caller's
    responsibility. No database access, scheduling, or input mutation occurs.
    """
    if not source.rows:
        raise DatasetRowInputError("empty_source", "row source must contain at least one data row")

    original_plan = resolve_dataset_row_plan(graph, design_spec)
    if original_plan is None:
        raise DatasetRowInputError("driver_mismatch", "published graph has no per-row Dataset driver")
    if original_plan["driver_dataset_id"] != source.dataset_id:
        raise DatasetRowInputError("driver_mismatch", "row source does not match the published graph driver")

    if not isinstance(protocol_revision_id, str) or not protocol_revision_id:
        raise DatasetRowInputError("missing_revision_id", "protocol revision id is required")

    seen: set[str] = set()
    ordered_parents = []
    required = (
        "replicate_result_id",
        "cell_id",
        "cell_label",
        "replicate_label",
        "replicate_number",
        "design_revision_id",
        "factor_values",
    )
    for parent in parents:
        if any(key not in parent for key in required):
            raise DatasetRowInputError("invalid_parent", "row parent is missing required identity or factor fields")
        identity = parent["replicate_result_id"]
        if not isinstance(identity, str) or not identity:
            raise DatasetRowInputError("invalid_parent", "replicate result id is required")
        if identity in seen:
            raise DatasetRowInputError("duplicate_parent", "replicate result ids must be unique")
        seen.add(identity)
        if not isinstance(parent["design_revision_id"], str) or not parent["design_revision_id"]:
            raise DatasetRowInputError("missing_revision_id", "design revision id is required for every parent")
        if isinstance(parent["replicate_number"], bool) or not isinstance(parent["replicate_number"], int):
            raise DatasetRowInputError("invalid_parent", "replicate number must be an integer")
        if not isinstance(parent["factor_values"], dict):
            raise DatasetRowInputError("invalid_parent", "factor values must be an object")
        ordered_parents.append(parent)

    ordered_parents.sort(key=lambda parent: (
        parent["cell_label"],
        parent["replicate_number"],
        parent["replicate_result_id"],
    ))

    candidates: list[dict[str, Any]] = []
    for parent in ordered_parents:
        applied_graph = apply_factor_bindings(graph, parent["factor_values"])
        applied_plan = resolve_dataset_row_plan(applied_graph, design_spec)
        if applied_plan is None or (
            applied_plan["driver_dataset_id"], applied_plan["bindings"]
        ) != (original_plan["driver_dataset_id"], original_plan["bindings"]):
            raise DatasetRowInputError(
                "varying_topology",
                "factor substitution must preserve the row driver and ordered Agent/column bindings",
            )

        for row_index in range(len(source.rows)):
            candidate = deepcopy(parent)
            candidate["protocol_revision_id"] = protocol_revision_id
            candidate["dataset_row"] = {
                "dataset_id": source.dataset_id,
                "raw_sha256": source.raw_sha256,
                "row_index": row_index,
            }
            candidates.append(candidate)
    return candidates
