"""Flattens replicate-result rows into a downloadable CSV.

One row per replicate; one column per factor_values/metric_values key seen across
all replicates -- unlike the Cells table UI's own pickMetricColumns, which caps
displayed metric columns at 4 for on-screen readability (frontend/src/pages/
CLAUDE.md), a CSV has no such density constraint, so every key is included.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Sequence
from typing import Any


def replicates_that_ran(replicates: Sequence[Any]) -> list[Any]:
    """Replicates nothing has touched yet -- no run_id, no metric_values -- are
    the "queued" placeholders generate_design_cells materializes up front for
    the whole factorial grid before any of them have actually run (same "has
    this cell run" rule list_experiment_trials' own status derivation uses).
    Excluded from the CSV export: a queued cell is an all-blank row past the
    factor columns, not a result."""
    return [replicate for replicate in replicates if replicate.run_id is not None or replicate.metric_values]


def replicates_to_csv(replicates: Sequence[Any]) -> str:
    """*replicates* -- anything with ``replicate_label``/``run_id``/``workspace_id``/
    ``factor_values``/``metric_values`` attributes (a ``FactorialReplicateResult``
    in practice). Column order: the three scalar fields, then every
    factor_values key (alphabetical), then every metric_values key
    (alphabetical) -- stable regardless of which cell happened to be first."""
    factor_keys: list[str] = []
    metric_keys: list[str] = []
    seen_factor: set[str] = set()
    seen_metric: set[str] = set()
    for replicate in replicates:
        for key in replicate.factor_values or {}:
            if key not in seen_factor:
                seen_factor.add(key)
                factor_keys.append(key)
        for key in replicate.metric_values or {}:
            if key not in seen_metric:
                seen_metric.add(key)
                metric_keys.append(key)
    factor_keys.sort()
    metric_keys.sort()

    fieldnames = ["replicate_label", "run_id", "workspace_id", *factor_keys, *metric_keys]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for replicate in replicates:
        row: dict[str, Any] = {
            "replicate_label": replicate.replicate_label,
            "run_id": str(replicate.run_id) if replicate.run_id else "",
            "workspace_id": replicate.workspace_id or "",
        }
        row.update(replicate.factor_values or {})
        row.update(replicate.metric_values or {})
        writer.writerow(row)
    return buf.getvalue()


def _column_identifier(value: str, *, fallback: str) -> str:
    """A stable, analysis-friendly CSV column fragment."""
    identifier = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return identifier or fallback


def _factor_level_key(value: Any) -> str:
    """A type-preserving, deterministic representation of a JSON factor level."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _natural_label_key(label: str) -> tuple[str, int, str]:
    """Keep generated labels in human order: level2 before level10."""
    match = re.fullmatch(r"(.*?)(\d+)", label.casefold())
    return (match.group(1), int(match.group(2)), label) if match else (label.casefold(), -1, label)


def _unique_column_name(base: str, used: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _declared_level_labels(design_spec: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    """Map a persisted factor value to its user-facing analysis label.

    Old designs have no labels yet. They use a short ``level1``-style
    fallback here, so raw prompts and configuration JSON never leave the
    execution data as a CSV header or categorical value.
    """
    labels_by_factor: dict[str, dict[str, str]] = {}
    factors = design_spec.get("factors") if isinstance(design_spec, dict) else None
    if not isinstance(factors, list):
        return labels_by_factor
    for factor in factors:
        if not isinstance(factor, dict):
            continue
        name, levels = factor.get("name"), factor.get("levels")
        if not isinstance(name, str) or not isinstance(levels, list):
            continue
        defaults = [f"level{index}" for index in range(1, len(levels) + 1)]
        supplied = factor.get("level_labels")
        labels = (
            [
                label.strip() if isinstance(label, str) and label.strip() else defaults[index]
                for index, label in enumerate(supplied)
            ]
            if isinstance(supplied, list) and len(supplied) == len(levels)
            else defaults
        )
        labels_by_factor[name] = {_factor_level_key(level): label for level, label in zip(levels, labels, strict=True)}
    return labels_by_factor


def _factor_columns(
    rows: Sequence[dict[str, Any]], *, reserved: Sequence[str], design_spec: dict[str, Any] | None = None
) -> list[tuple[str, str, str, dict[str, str] | None]]:
    """Derive analysis-ready columns from the dynamic factor JSON.

    Boolean factors become one 0/1 ``<factor>_enabled`` column. Numeric
    factors stay numeric. Categorical, text, and structured configuration
    factors use one categorical column containing their short level labels.
    This preserves all levels without leaking full prompt/configuration
    values; downstream statistics tools can choose their own contrast coding.
    """
    discovered_factor_keys = {key for row in rows for key in (row.get("factor_values") or {})}
    declared_factors = design_spec.get("factors") if isinstance(design_spec, dict) else None
    declared_level_counts = {
        factor["name"]: len(factor["levels"])
        for factor in (declared_factors or [])
        if isinstance(factor, dict)
        and isinstance(factor.get("name"), str)
        and isinstance(factor.get("levels"), list)
        and factor["name"] in discovered_factor_keys
    }
    observed_level_counts = {
        key: len(
            {
                _factor_level_key(factors[key])
                for row in rows
                if isinstance((factors := row.get("factor_values")), dict) and key in factors
            }
        )
        for key in discovered_factor_keys
    }
    # A factor with fewer declared treatments comes first, putting the most
    # variable/high-cardinality treatment columns on the right. Declaration
    # order breaks ties, then a name keeps legacy/externally reported factors
    # stable.
    declared_order = {name: index for index, name in enumerate(declared_level_counts)}

    def factor_sort_key(key: str) -> tuple[int, int, str]:
        return (
            declared_level_counts.get(key, observed_level_counts[key]),
            declared_order.get(key, len(declared_order)),
            key,
        )

    factor_keys = sorted(
        discovered_factor_keys,
        key=factor_sort_key,
    )
    used = set(reserved)
    labels_by_factor = _declared_level_labels(design_spec)
    columns: list[tuple[str, str, str, dict[str, str] | None]] = []
    for factor_key in factor_keys:
        values = [
            factors[factor_key]
            for row in rows
            if isinstance((factors := row.get("factor_values")), dict) and factor_key in factors
        ]
        factor_name = _column_identifier(factor_key, fallback="factor")
        if values and all(isinstance(value, bool) for value in values):
            suffix = factor_name if factor_name.endswith("_enabled") else f"{factor_name}_enabled"
            columns.append((_unique_column_name(suffix, used), factor_key, "boolean", None))
        elif values and all(isinstance(value, int | float) and not isinstance(value, bool) for value in values):
            columns.append((_unique_column_name(factor_name, used), factor_key, "numeric", None))
        else:
            levels = sorted({_factor_level_key(value) for value in values})
            declared = labels_by_factor.get(factor_key, {})
            labels = {level: declared.get(level, f"level{index}") for index, level in enumerate(levels, start=1)}
            columns.append((_unique_column_name(factor_name, used), factor_key, "categorical", labels))
    return columns


_RESULT_ID_FIELDS = [
    "cell_label",
    "replicate_number",
]

_RESULT_RUNTIME_METRIC_FIELDS = [
    "duration_seconds",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost_usd",
]

_RESULT_METADATA_FIELDS = [
    "status",
    "obsolete",
    "run_id",
    "protocol_revision_id",
    "updated_at",
    "error",
]

_RESULT_FIXED_FIELDS = [*_RESULT_ID_FIELDS, *_RESULT_RUNTIME_METRIC_FIELDS, *_RESULT_METADATA_FIELDS]


def _result_csv_layout(
    rows: Sequence[dict[str, Any]], design_spec: dict[str, Any] | None = None
) -> tuple[list[tuple[str, str, str, dict[str, str] | None]], list[str]]:
    # Runtime metrics are already present in the fixed execution columns.
    # Avoid duplicate CSV headers while retaining every non-telemetry score.
    metric_keys = sorted(
        {key for row in rows for key in (row.get("metric_values") or {}) if key not in _RESULT_FIXED_FIELDS}
    )
    factor_columns = _factor_columns(
        rows, reserved=[*_RESULT_FIXED_FIELDS, *metric_keys], design_spec=design_spec
    )
    return factor_columns, metric_keys


def result_rows_schema(
    rows: Sequence[dict[str, Any]],
    metric_types: dict[str, str] | None = None,
    metric_aggregations: dict[str, str] | None = None,
    design_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Machine-readable companion metadata for a Results analysis CSV."""
    factor_columns, metric_keys = _result_csv_layout(rows, design_spec)
    factor_metadata = []
    for column_name, factor_key, kind, labels in factor_columns:
        column: dict[str, Any] = {
            "name": column_name,
            "role": "factor",
            "source_factor": factor_key,
            "encoding": kind,
        }
        if kind == "categorical":
            column["value_type"] = "string"
            column["levels"] = list((labels or {}).values())
        factor_metadata.append(column)
    return {
        "schema_version": 3,
        "row_unit": "replicate",
        "columns": [
            *({"name": field, "role": "metadata"} for field in _RESULT_ID_FIELDS),
            *factor_metadata,
            *({"name": field, "role": "metric"} for field in _RESULT_RUNTIME_METRIC_FIELDS),
            *(
                {
                    "name": key,
                    "role": "outcome",
                    "value_type": (metric_types or {}).get(key, "number"),
                    "cell_aggregation": (metric_aggregations or {}).get(key, "mean"),
                }
                for key in metric_keys
            ),
            *({"name": field, "role": "metadata"} for field in _RESULT_METADATA_FIELDS),
        ],
    }


def result_rows_to_csv(rows: Sequence[dict[str, Any]], design_spec: dict[str, Any] | None = None) -> str:
    """Export the enriched Results response as an analysis-ready CSV.

    Unlike :func:`replicates_to_csv`, this receives the read-only Results
    projection.  That lets a CSV include selected runtime metrics without
    pretending those execution facts were manually persisted score values.
    Boolean and numeric factors are projected directly; categorical values
    become their short persisted level labels rather than raw prompt or
    configuration payloads.
    """
    factor_columns, metric_keys = _result_csv_layout(rows, design_spec)
    fields = [
        *_RESULT_ID_FIELDS,
        *(column[0] for column in factor_columns),
        *_RESULT_RUNTIME_METRIC_FIELDS,
        *metric_keys,
        *_RESULT_METADATA_FIELDS,
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()

    def row_sort_key(source: dict[str, Any]) -> tuple[Any, ...]:
        factors = source.get("factor_values") or {}
        factor_keys: list[Any] = []
        for _column_name, factor_key, kind, labels in factor_columns:
            value = factors.get(factor_key)
            if kind == "categorical":
                label = (labels or {}).get(_factor_level_key(value), "")
                factor_keys.append(_natural_label_key(label))
            elif kind == "boolean":
                factor_keys.append(int(bool(value)))
            elif kind == "numeric":
                factor_keys.append(value if isinstance(value, int | float) else float("-inf"))
        replicate_number = source.get("replicate_number")
        return (*factor_keys, replicate_number if isinstance(replicate_number, int) else 0)

    for source in sorted(rows, key=row_sort_key):
        metrics = {
            key: int(value) if isinstance(value, bool) else value
            for key, value in (source.get("metric_values") or {}).items()
        }
        factors = source.get("factor_values") or {}
        row = {key: source.get(key, metrics.get(key, "")) for key in _RESULT_FIXED_FIELDS}
        for column_name, factor_key, kind, labels in factor_columns:
            if factor_key not in factors:
                continue
            value = factors[factor_key]
            if kind == "boolean":
                row[column_name] = int(value)
            elif kind == "numeric":
                row[column_name] = value
            else:
                row[column_name] = (labels or {}).get(_factor_level_key(value), "")
        row.update({key: metrics.get(key, "") for key in metric_keys})
        writer.writerow(row)
    return buf.getvalue()


__all__ = ["replicates_that_ran", "replicates_to_csv", "result_rows_schema", "result_rows_to_csv"]
