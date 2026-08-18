"""Flattens FactorialCellResult rows into a downloadable CSV.

One row per cell; one column per factor_values/metric_values key seen across
ALL cells -- unlike the Cells table UI's own pickMetricColumns, which caps
displayed metric columns at 4 for on-screen readability (frontend/src/pages/
CLAUDE.md), a CSV has no such density constraint, so every key is included.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from typing import Any


def cells_that_ran(cells: Sequence[Any]) -> list[Any]:
    """Cells nothing has touched yet -- no run_id, no metric_values -- are
    the "queued" placeholders generate_design_cells materializes up front for
    the whole factorial grid before any of them have actually run (same "has
    this cell run" rule list_experiment_trials' own status derivation uses).
    Excluded from the CSV export: a queued cell is an all-blank row past the
    factor columns, not a result."""
    return [c for c in cells if c.run_id is not None or c.metric_values]


def cells_to_csv(cells: Sequence[Any]) -> str:
    """*cells* -- anything with ``cell_label``/``run_id``/``workspace_id``/
    ``factor_values``/``metric_values`` attributes (a ``FactorialCellResult``
    in practice). Column order: the three scalar fields, then every
    factor_values key (alphabetical), then every metric_values key
    (alphabetical) -- stable regardless of which cell happened to be first."""
    factor_keys: list[str] = []
    metric_keys: list[str] = []
    seen_factor: set[str] = set()
    seen_metric: set[str] = set()
    for cell in cells:
        for key in cell.factor_values or {}:
            if key not in seen_factor:
                seen_factor.add(key)
                factor_keys.append(key)
        for key in cell.metric_values or {}:
            if key not in seen_metric:
                seen_metric.add(key)
                metric_keys.append(key)
    factor_keys.sort()
    metric_keys.sort()

    fieldnames = ["cell_label", "run_id", "workspace_id", *factor_keys, *metric_keys]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for cell in cells:
        row: dict[str, Any] = {
            "cell_label": cell.cell_label,
            "run_id": str(cell.run_id) if cell.run_id else "",
            "workspace_id": cell.workspace_id or "",
        }
        row.update(cell.factor_values or {})
        row.update(cell.metric_values or {})
        writer.writerow(row)
    return buf.getvalue()


__all__ = ["cells_that_ran", "cells_to_csv"]
