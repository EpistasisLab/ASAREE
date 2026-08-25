"""What a caller needs to know about a file before it can model it.

``describe_dataset`` used to answer "what columns are in here", which is enough
for a human who already knows the dataset and not enough for an agent handed a
path and a one-line instruction. Everything below exists to turn the questions
such an agent has to answer next -- which column is the outcome? is this binary
or multiclass? is there an id column that would leak across a random split? is
there a date to split on instead? -- from guesses into readings.

These are SUGGESTIONS, always labelled with the evidence behind them, never
applied automatically. A column named ``patient_id`` is very likely a grouping
key and might be a legitimate feature; that call belongs to the caller, and the
job here is to make sure the caller knows the column is there.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Name fragments that mark a column as bookkeeping rather than measurement.
# A heuristic, and stated as one wherever it's used -- combined with
# cardinality, it's right often enough to be worth surfacing and cheap enough
# to ignore when it isn't.
_ID_HINTS = ("id", "uuid", "guid", "key", "index", "subject", "patient", "participant", "record", "sample")
_GROUP_HINTS = ("subject", "patient", "participant", "site", "center", "centre", "cluster", "group", "session", "user")
_TIME_HINTS = ("date", "time", "timestamp", "year", "month", "day", "visit", "epoch", "period")
_MAX_LEVELS = 5
# A grouping key has MANY groups with few rows each. Without this floor, a
# three-level ``site`` column over 400 rows reads as an entity id when it is
# plainly just a categorical feature -- and recommending a grouped split for it
# would throw away most of the training data for nothing.
_MIN_GROUPS = 10
_MIN_ROWS_PER_GROUP = 1.5


def _kind(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    return "categorical"


def _looks_like(name: str, hints: tuple[str, ...]) -> bool:
    lowered = str(name).lower()
    parts = set(lowered.replace("-", "_").replace(".", "_").split("_"))
    return any(hint in parts or lowered.endswith(hint) or lowered.startswith(hint) for hint in hints)


def _is_group_like(name: str, n_unique: int, n_rows: int) -> bool:
    """Whether *name* looks like an entity id whose rows must not straddle a split."""
    return (
        _looks_like(name, _GROUP_HINTS)
        and _MIN_GROUPS <= n_unique < n_rows
        and n_rows / max(n_unique, 1) >= _MIN_ROWS_PER_GROUP
    )


def likely_group_columns(frame: pd.DataFrame) -> list[str]:
    """Entity-id-looking columns, for warning about a split nobody asked to group.

    Used by the split audit, which has to raise the possibility that a random
    split leaks *even when the caller never mentioned a group column* -- that
    being exactly the case where they don't know to.
    """
    n_rows = len(frame)
    return [
        str(name)
        for name in frame.columns
        if _is_group_like(str(name), int(frame[name].nunique(dropna=True)), n_rows)
    ]


def _parses_as_dates(series: pd.Series) -> bool:
    """Whether an object column is really a date column wearing strings."""
    sample = series.dropna().head(200)
    if sample.empty:
        return False
    try:
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    except (ValueError, TypeError):
        # Mixed-format inference gives up on some object columns rather than
        # coercing; "not a date column" is the right reading of that.
        return False
    return bool(parsed.notna().mean() > 0.95)


def column_report(frame: pd.DataFrame, max_columns: int) -> tuple[list[dict[str, Any]], int]:
    """Per-column dtype, missingness, cardinality and a compact value summary."""
    head = frame.iloc[:, :max_columns]
    n_rows = max(len(frame), 1)
    columns = []
    for name in head.columns:
        series = head[name]
        kind = _kind(series)
        entry: dict[str, Any] = {
            "name": str(name),
            "dtype": str(series.dtype),
            "kind": kind,
            "n_missing": int(series.isna().sum()),
            "pct_missing": round(float(series.isna().mean()) * 100, 2),
            "n_unique": int(series.nunique(dropna=True)),
        }
        if kind in {"numeric", "boolean"}:
            numeric = pd.to_numeric(series, errors="coerce")
            if numeric.notna().any():
                entry["min"] = _round(numeric.min())
                entry["max"] = _round(numeric.max())
                entry["mean"] = _round(numeric.mean())
                entry["std"] = _round(numeric.std())
        elif kind == "categorical":
            counts = series.value_counts(dropna=True).head(_MAX_LEVELS)
            entry["top_values"] = [{"value": str(v), "n": int(n)} for v, n in counts.items()]
            if entry["n_unique"] > _MAX_LEVELS:
                entry["top_values_truncated"] = True
            if _parses_as_dates(series):
                entry["parses_as_dates"] = True
        if entry["n_unique"] <= 1:
            entry["constant"] = True
        if entry["n_unique"] == len(frame) and len(frame) > 1:
            entry["unique_per_row"] = True
        entry["rows_per_distinct_value"] = round(n_rows / max(entry["n_unique"], 1), 2)
        columns.append(entry)
    return columns, max(0, int(frame.shape[1]) - int(head.shape[1]))


def _round(value: Any) -> float | None:
    value = float(value)
    return round(value, 6) if np.isfinite(value) else None


def infer_task_type(target: pd.Series) -> str:
    """'binary', 'multiclass' or 'regression' for *target*.

    Cardinality, not dtype: a 0/1 outcome stored as int64 is a classification
    target and ``is_numeric_dtype`` would happily call it a regression one.
    """
    n_unique = int(target.nunique(dropna=True))
    if n_unique <= 1:
        return "degenerate"
    if n_unique == 2:
        return "binary"
    if not pd.api.types.is_numeric_dtype(target) or pd.api.types.is_bool_dtype(target):
        return "multiclass"
    # A small number of distinct integers is a graded outcome far more often
    # than it is a continuous measurement worth regressing on.
    integral = target.dropna().mod(1).eq(0).all()
    return "multiclass" if integral and n_unique <= 10 else "regression"


def target_summary(target: pd.Series, target_column: str) -> dict[str, Any]:
    """Task type, class balance and the AUC-relevant caveats for one column."""
    task_type = infer_task_type(target)
    summary: dict[str, Any] = {
        "target_column": target_column,
        "inferred_task_type": task_type,
        "n_missing": int(target.isna().sum()),
    }
    notes: list[str] = []
    if summary["n_missing"]:
        notes.append(f"{summary['n_missing']} row(s) have no target value and cannot be used")

    if task_type == "regression":
        numeric = pd.to_numeric(target, errors="coerce")
        summary["distribution"] = {
            "min": _round(numeric.min()),
            "max": _round(numeric.max()),
            "mean": _round(numeric.mean()),
            "std": _round(numeric.std()),
        }
        notes.append(
            "continuous target -- ROC-AUC does not apply; use run_logistic_regression_script "
            "with task_type='regression'"
        )
    elif task_type == "degenerate":
        notes.append("the target has a single distinct value -- nothing to predict")
    else:
        counts = target.value_counts(dropna=True)
        summary["classes"] = [str(c) for c in sorted(counts.index.tolist())]
        summary["class_distribution"] = {str(k): int(v) for k, v in counts.items()}
        summary["imbalance_ratio"] = round(float(counts.max() / max(counts.min(), 1)), 2)
        if task_type == "binary":
            minority = counts.idxmin()
            summary["minority_class"] = str(minority)
            summary["minority_rate"] = round(float(counts.min() / counts.sum()), 4)
            # The PR-AUC floor, and the number that decides whether accuracy is
            # a meaningful headline at all.
            notes.append(
                f"positive-class prevalence sets the PR-AUC baseline; accuracy is misleading below ~10% "
                f"(this target's minority class is {summary['minority_rate'] * 100:.1f}%)"
            )
        if summary["imbalance_ratio"] >= 4:
            notes.append(
                "classes are imbalanced -- consider class_weight='balanced' and report PR-AUC alongside ROC-AUC"
            )
        if int(counts.min()) < 30:
            notes.append(f"the rarest class has only {int(counts.min())} rows -- every metric will be noisy")
    summary["notes"] = notes
    return summary


def suggestions(frame: pd.DataFrame, columns: list[dict[str, Any]]) -> dict[str, Any]:
    """Candidate targets, id/group columns and time columns, each with its evidence."""
    n_rows = len(frame)
    targets = [
        {
            "column": c["name"],
            "task_type": infer_task_type(frame[c["name"]]),
            "n_unique": c["n_unique"],
            "why": "binary outcome" if c["n_unique"] == 2 else f"{c['n_unique']} distinct values",
        }
        for c in columns
        if 2 <= c["n_unique"] <= 10 and not c.get("unique_per_row") and c["pct_missing"] < 50
    ]
    # Binary first: a one-line "run logistic regression on this" almost always
    # means the two-class column, and ordering the list is cheaper than making
    # the caller work that out.
    targets.sort(key=lambda t: (t["task_type"] != "binary", t["n_unique"]))

    identifiers = [
        {"column": c["name"], "why": "one distinct value per row"}
        for c in columns
        if c.get("unique_per_row") or (c["n_unique"] > 0.9 * n_rows and _looks_like(c["name"], _ID_HINTS))
    ]
    groups = [
        {
            "column": c["name"],
            "n_groups": c["n_unique"],
            "rows_per_group": c["rows_per_distinct_value"],
            "why": (
                f"{c['n_unique']} repeated values ({c['rows_per_distinct_value']} rows each) and an "
                "identifier-like name -- a random split would put the same entity in train and test"
            ),
        }
        for c in columns
        if _is_group_like(c["name"], c["n_unique"], n_rows)
    ]
    times = [
        {
            "column": c["name"],
            "why": "datetime dtype" if c["kind"] == "datetime" else
            ("parses as dates" if c.get("parses_as_dates") else "date-like name"),
        }
        for c in columns
        if c["kind"] == "datetime"
        or c.get("parses_as_dates")
        or (_looks_like(c["name"], _TIME_HINTS) and c["n_unique"] > 2)
    ]
    constants = [c["name"] for c in columns if c.get("constant")]
    leaky = [c["name"] for c in columns if c["pct_missing"] > 50]

    recommended = "random"
    reason = "no grouping or time column detected -- rows look independent"
    if groups:
        recommended = "group"
        reason = f"{groups[0]['column']!r} repeats across rows; a random split would leak entities across it"
    elif times:
        recommended = "time"
        reason = f"{times[0]['column']!r} looks temporal; a random split would train on the future"

    return {
        "candidate_targets": targets[:10],
        "candidate_id_columns": identifiers,
        "candidate_group_columns": groups,
        "candidate_time_columns": times,
        "constant_columns": constants,
        "mostly_missing_columns": leaky,
        "recommended_split": {"strategy": recommended, "why": reason},
        "note": (
            "heuristics from column names and cardinality, not domain knowledge -- confirm before relying on them"
        ),
    }
