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


def _unique_column_name(base: str, used: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _design_matrix_columns(
    rows: Sequence[dict[str, Any]], *, reserved: Sequence[str]
) -> list[tuple[str, str, str, str | None]]:
    """Derive numeric design columns from the dynamic factor JSON.

    Boolean factors become one 0/1 ``<factor>_enabled`` column. Numeric
    factors stay numeric. Everything else, including structured configuration
    factors, is treatment-coded into k-1 0/1 columns; the first canonical
    level is the reference category, avoiding a rank-deficient matrix when a
    statistics package includes an intercept.
    """
    factor_keys = sorted({key for row in rows for key in (row.get("factor_values") or {})})
    used = set(reserved)
    columns: list[tuple[str, str, str, str | None]] = []
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
            for level in levels[1:]:
                level_name = _column_identifier(level.strip('"'), fallback="level")
                columns.append(
                    (_unique_column_name(f"{factor_name}_{level_name}", used), factor_key, "categorical", level)
                )
    return columns


_RESULT_FIXED_FIELDS = [
    "replicate_label",
    "replicate_number",
    "cell_label",
    "status",
    "obsolete",
    "run_id",
    "protocol_revision_id",
    "updated_at",
    "duration_seconds",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost_usd",
    "error",
]


def _result_csv_layout(
    rows: Sequence[dict[str, Any]],
) -> tuple[list[str], list[tuple[str, str, str, str | None]], list[str]]:
    # Runtime metrics are already present in the fixed execution columns.
    # Avoid duplicate CSV headers while retaining every non-telemetry score.
    metric_keys = sorted(
        {key for row in rows for key in (row.get("metric_values") or {}) if key not in _RESULT_FIXED_FIELDS}
    )
    factor_columns = _design_matrix_columns(rows, reserved=[*_RESULT_FIXED_FIELDS, *metric_keys])
    return _RESULT_FIXED_FIELDS, factor_columns, metric_keys


def result_rows_schema(
    rows: Sequence[dict[str, Any]],
    metric_types: dict[str, str] | None = None,
    metric_aggregations: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Machine-readable companion metadata for a Results design-matrix CSV."""
    fixed_fields, factor_columns, metric_keys = _result_csv_layout(rows)
    factor_levels: dict[str, list[str]] = {}
    for key in {column[1] for column in factor_columns}:
        values = []
        for row in rows:
            factors = row.get("factor_values")
            if isinstance(factors, dict) and key in factors:
                values.append(_factor_level_key(factors[key]))
        factor_levels[key] = sorted(set(values))

    factor_metadata = []
    for column_name, factor_key, kind, level in factor_columns:
        column: dict[str, Any] = {
            "name": column_name,
            "role": "factor",
            "source_factor": factor_key,
            "encoding": kind,
        }
        if kind == "categorical" and level:
            column["level"] = json.loads(level)
            column["reference_level"] = json.loads(factor_levels[factor_key][0])
        factor_metadata.append(column)
    return {
        "schema_version": 1,
        "row_unit": "replicate",
        "columns": [
            *({"name": field, "role": "metadata"} for field in fixed_fields),
            *factor_metadata,
            *(
                {
                    "name": key,
                    "role": "outcome",
                    "value_type": (metric_types or {}).get(key, "number"),
                    "cell_aggregation": (metric_aggregations or {}).get(key, "mean"),
                }
                for key in metric_keys
            ),
        ],
    }


def result_rows_to_csv(rows: Sequence[dict[str, Any]]) -> str:
    """Export the enriched Results response as an analysis-ready CSV.

    Unlike :func:`replicates_to_csv`, this receives the read-only Results
    projection.  That lets a CSV include selected runtime metrics without
    pretending those execution facts were manually persisted score values.
    Factor JSON is projected to a numeric design matrix rather than exported
    as mixed boolean/string/configuration cells.
    """
    fixed_fields, factor_columns, metric_keys = _result_csv_layout(rows)
    fields = [*fixed_fields, *(column[0] for column in factor_columns), *metric_keys]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for source in rows:
        metrics = {
            key: int(value) if isinstance(value, bool) else value
            for key, value in (source.get("metric_values") or {}).items()
        }
        factors = source.get("factor_values") or {}
        row = {key: source.get(key, metrics.get(key, "")) for key in fixed_fields}
        for column_name, factor_key, kind, level in factor_columns:
            if factor_key not in factors:
                continue
            value = factors[factor_key]
            if kind == "boolean":
                row[column_name] = int(value)
            elif kind == "numeric":
                row[column_name] = value
            else:
                row[column_name] = int(_factor_level_key(value) == level)
        row.update({key: metrics.get(key, "") for key in metric_keys})
        writer.writerow(row)
    return buf.getvalue()


__all__ = ["replicates_that_ran", "replicates_to_csv", "result_rows_schema", "result_rows_to_csv"]
