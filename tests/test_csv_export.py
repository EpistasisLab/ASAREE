from __future__ import annotations

import csv
import io
from types import SimpleNamespace

from asaree.services.csv_export import replicates_that_ran, replicates_to_csv


def _replicate(
    replicate_label: str,
    *,
    run_id: str | None = None,
    workspace_id: str | None = None,
    factor_values: dict | None = None,
    metric_values: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        replicate_label=replicate_label,
        run_id=run_id,
        workspace_id=workspace_id,
        factor_values=factor_values,
        metric_values=metric_values,
    )


def _parse(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def test_replicates_to_csv_empty_list_still_has_a_header() -> None:
    text = replicates_to_csv([])
    assert text.strip() == "replicate_label,run_id,workspace_id"


def test_replicates_to_csv_one_row_per_replicate_with_factor_and_metric_columns() -> None:
    replicates = [
        _replicate(
            "cell-a",
            run_id="11111111-1111-1111-1111-111111111111",
            workspace_id="exp/cell-a",
            factor_values={"effort": "medium"},
            metric_values={"average_precision": 0.53, "roc_auc": 0.75},
        ),
    ]
    rows = _parse(replicates_to_csv(replicates))
    assert len(rows) == 1
    assert rows[0]["replicate_label"] == "cell-a"
    assert rows[0]["run_id"] == "11111111-1111-1111-1111-111111111111"
    assert rows[0]["workspace_id"] == "exp/cell-a"
    assert rows[0]["effort"] == "medium"
    assert rows[0]["average_precision"] == "0.53"
    assert rows[0]["roc_auc"] == "0.75"


def test_replicates_to_csv_columns_are_the_union_across_all_replicates() -> None:
    # Cell 1 has no metrics yet (not scored); cell 2 does. Both still get a
    # column for every key seen anywhere -- cell 1's metric cells are empty.
    replicates = [
        _replicate("cell-a", factor_values={"effort": "medium"}, metric_values=None),
        _replicate("cell-b", factor_values={"critic": True}, metric_values={"f1": 0.4}),
    ]
    rows = _parse(replicates_to_csv(replicates))
    header = rows[0].keys() if rows else []
    assert set(header) == {"replicate_label", "run_id", "workspace_id", "effort", "critic", "f1"}
    assert rows[0]["f1"] == ""
    assert rows[0]["critic"] == ""
    assert rows[1]["effort"] == ""


def test_replicates_to_csv_missing_run_id_and_workspace_id_are_blank() -> None:
    replicates = [_replicate("cell-a")]
    rows = _parse(replicates_to_csv(replicates))
    assert rows[0]["run_id"] == ""
    assert rows[0]["workspace_id"] == ""


def test_replicates_to_csv_column_order_is_scalars_then_sorted_factors_then_sorted_metrics() -> None:
    replicates = [
        _replicate(
            "cell-a",
            factor_values={"zeta": 1, "alpha": 2},
            metric_values={"zeta_metric": 1, "alpha_metric": 2},
        ),
    ]
    text = replicates_to_csv(replicates)
    header = text.splitlines()[0].split(",")
    assert header == ["replicate_label", "run_id", "workspace_id", "alpha", "zeta", "alpha_metric", "zeta_metric"]


# --- replicates_that_ran ------------------------------------------------


def test_replicates_that_ran_excludes_untouched_queued_replicates() -> None:
    # generate_design_cells materializes one row per combination up front,
    # for the whole factorial grid, long before any of them run.
    queued = _replicate("cell-queued")
    ran = _replicate("cell-ran", run_id="11111111-1111-1111-1111-111111111111")
    assert replicates_that_ran([queued, ran]) == [ran]


def test_replicates_that_ran_includes_metrics_without_run_id() -> None:
    # A cell scored directly (e.g. upserted by a notebook), never through a
    # ProtocolRun at all, still counts as "ran" -- same rule
    # list_experiment_trials uses to call this "completed" rather than "queued".
    scored_externally = _replicate("cell-scored", metric_values={"accuracy": 0.9})
    assert replicates_that_ran([scored_externally]) == [scored_externally]


def test_replicates_that_ran_includes_a_failed_run_with_no_metrics_yet() -> None:
    # A cell whose run_id is set but never produced metrics (e.g. it failed
    # before scoring) still "ran" -- excluding it would silently drop a real
    # attempt, not just an unstarted placeholder.
    failed = _replicate("cell-failed", run_id="11111111-1111-1111-1111-111111111111", metric_values=None)
    assert replicates_that_ran([failed]) == [failed]
