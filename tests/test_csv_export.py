from __future__ import annotations

import csv
import io
from types import SimpleNamespace

from asaree.services.csv_export import cells_to_csv


def _cell(
    cell_label: str,
    *,
    run_id: str | None = None,
    workspace_id: str | None = None,
    factor_values: dict | None = None,
    metric_values: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        cell_label=cell_label,
        run_id=run_id,
        workspace_id=workspace_id,
        factor_values=factor_values,
        metric_values=metric_values,
    )


def _parse(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def test_cells_to_csv_empty_list_still_has_a_header() -> None:
    text = cells_to_csv([])
    assert text.strip() == "cell_label,run_id,workspace_id"


def test_cells_to_csv_one_row_per_cell_with_factor_and_metric_columns() -> None:
    cells = [
        _cell(
            "cell-a",
            run_id="11111111-1111-1111-1111-111111111111",
            workspace_id="exp/cell-a",
            factor_values={"effort": "medium"},
            metric_values={"average_precision": 0.53, "roc_auc": 0.75},
        ),
    ]
    rows = _parse(cells_to_csv(cells))
    assert len(rows) == 1
    assert rows[0]["cell_label"] == "cell-a"
    assert rows[0]["run_id"] == "11111111-1111-1111-1111-111111111111"
    assert rows[0]["workspace_id"] == "exp/cell-a"
    assert rows[0]["effort"] == "medium"
    assert rows[0]["average_precision"] == "0.53"
    assert rows[0]["roc_auc"] == "0.75"


def test_cells_to_csv_columns_are_the_union_across_all_cells() -> None:
    # Cell 1 has no metrics yet (not scored); cell 2 does. Both still get a
    # column for every key seen anywhere -- cell 1's metric cells are empty.
    cells = [
        _cell("cell-a", factor_values={"effort": "medium"}, metric_values=None),
        _cell("cell-b", factor_values={"critic": True}, metric_values={"f1": 0.4}),
    ]
    rows = _parse(cells_to_csv(cells))
    header = rows[0].keys() if rows else []
    assert set(header) == {"cell_label", "run_id", "workspace_id", "effort", "critic", "f1"}
    assert rows[0]["f1"] == ""
    assert rows[0]["critic"] == ""
    assert rows[1]["effort"] == ""


def test_cells_to_csv_missing_run_id_and_workspace_id_are_blank() -> None:
    cells = [_cell("cell-a")]
    rows = _parse(cells_to_csv(cells))
    assert rows[0]["run_id"] == ""
    assert rows[0]["workspace_id"] == ""


def test_cells_to_csv_column_order_is_scalars_then_sorted_factors_then_sorted_metrics() -> None:
    cells = [
        _cell(
            "cell-a",
            factor_values={"zeta": 1, "alpha": 2},
            metric_values={"zeta_metric": 1, "alpha_metric": 2},
        ),
    ]
    text = cells_to_csv(cells)
    header = text.splitlines()[0].split(",")
    assert header == ["cell_label", "run_id", "workspace_id", "alpha", "zeta", "alpha_metric", "zeta_metric"]
