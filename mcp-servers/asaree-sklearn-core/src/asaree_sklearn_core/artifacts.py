"""Replayable transform artifacts and the engineered-feature recipe engine.

Lifted verbatim (behaviour-preserving) from the monolith's ``session.py`` — but
WITHOUT the in-memory ``Session`` store or its module-level singleton. That
per-process, ``dataset_id``-keyed session is exactly the shared mutable state
the core extraction retires (issue #1456): a split server family must resolve
"the current dataset" from the on-disk workspace HEAD, not a process-local dict.

What remains here is pure and stateless: dataclasses that each carry an
``.apply(X)`` replaying a transform whose statistics were frozen at fit time on
the TRAIN fold, so a held-out split is transformed without recomputing anything.
That frozen-then-applied contract is the leakage-safety invariant (issue #1456).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
import pandas as pd


def _new_id() -> str:
    return str(uuid.uuid4())[:8]


def _to_num(s: Any) -> pd.Series:
    """Coerce a column to a numeric Series, non-castable values -> NaN."""
    return cast("pd.Series", pd.to_numeric(s, errors="coerce"))


@dataclass
class DatasetArtifact:
    dataset_id: str
    X_train: pd.DataFrame
    X_test: pd.DataFrame
    y_train: pd.Series
    y_test: pd.Series
    feature_names: list[str]
    target_name: str
    task: str  # "classification" | "regression"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreprocessorArtifact:
    preprocessor_id: str
    pipeline: Any  # sklearn Pipeline
    source_dataset_id: str
    feature_names_in: list[str]
    feature_names_out: list[str]
    recipe: list[dict[str, Any]]


@dataclass
class SelectorArtifact:
    selector_id: str
    selected_features: list[str]
    importances: list[dict[str, Any]]  # [{feature, score, rank}]
    method: str
    source_dataset_id: str


@dataclass
class ModelArtifact:
    model_id: str
    estimator: Any  # fitted sklearn estimator
    algorithm: str
    hyperparams: dict[str, Any]
    cv_results: dict[str, Any]
    source_dataset_id: str
    preprocessor_id: str | None
    selector_id: str | None
    feature_names: list[str]


# ---------------------------------------------------------------------------
# Data-cleaning + feature-engineering artifacts (DC / FTE stages).
#
# Each carries an .apply(X) that replays the fitted transform on an arbitrary
# split. Fits happen on the training fold only; apply() uses frozen state, so a
# held-out split is transformed without recomputing any statistic — this is what
# lets run_model_script reconstruct the exact fit-time matrix on the test fold.
# ---------------------------------------------------------------------------


@dataclass
class DomainFixerArtifact:
    """Coerces type/domain violations to NaN per fitted per-column rules."""

    domain_fixer_id: str
    rules: dict[str, dict[str, Any]]  # col -> {type, allowed?, min?, max?, nonneg?}
    feature_names: list[str]
    source_dataset_id: str

    def apply(self, X: pd.DataFrame) -> pd.DataFrame:
        X = X.copy()
        for col, rule in self.rules.items():
            if col not in X.columns:
                continue
            if rule.get("type") == "numeric":
                s = _to_num(X[col])
                if rule.get("nonneg"):
                    s = s.where(s >= 0)
                if rule.get("min") is not None:
                    s = s.where(s >= rule["min"])
                if rule.get("max") is not None:
                    s = s.where(s <= rule["max"])
                X[col] = s
            elif rule.get("type") == "categorical":
                allowed = rule.get("allowed")
                if allowed:
                    X[col] = X[col].where(X[col].isin(list(allowed)))
        return X


@dataclass
class ImputerArtifact:
    """Fills NaNs with per-column values frozen from the training fold."""

    imputer_id: str
    fill_values: dict[str, Any]  # col -> fill value
    strategies: dict[str, str]  # col -> "mode" | "mean" | "median"
    feature_names: list[str]
    source_dataset_id: str

    def apply(self, X: pd.DataFrame) -> pd.DataFrame:
        X = X.copy()
        for col, val in self.fill_values.items():
            if col in X.columns:
                X[col] = X[col].fillna(val)
        return X


@dataclass
class FeatureRecipeArtifact:
    """Replayable engineered-feature recipe; statistic-bearing ops are frozen."""

    feature_recipe_id: str
    entries: list[dict[str, Any]]  # [{name, op, inputs, params (frozen)}]
    feature_names_in: list[str]
    feature_names_out: list[str]  # original survivors + engineered, deterministic
    source_dataset_id: str

    def apply(self, X: pd.DataFrame) -> pd.DataFrame:
        X = X.copy()
        for entry in self.entries:
            X[entry["name"]] = apply_recipe_entry(X, entry)
        # Deterministic column order; tolerate columns absent on an odd split.
        cols = [c for c in self.feature_names_out if c in X.columns]
        return cast("pd.DataFrame", X[cols])


def _present(X: pd.DataFrame, inputs: list[str]) -> list[str]:
    return [c for c in inputs if c in X.columns]


def _num_df(X: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Coerce a column block to numeric (non-castable -> NaN), row-op ready."""
    return X[cols].apply(_to_num) if cols else X[cols]


# Comparison operators shared by the threshold / count_threshold ops and the
# server's up-front validation (single source of truth). Both word and symbol
# spellings are accepted; a NaN operand always compares False.
COMPARE_OPS: dict[str, Any] = {
    "gt": lambda a, b: a > b, ">": lambda a, b: a > b,
    "ge": lambda a, b: a >= b, ">=": lambda a, b: a >= b,
    "lt": lambda a, b: a < b, "<": lambda a, b: a < b,
    "le": lambda a, b: a <= b, "<=": lambda a, b: a <= b,
    "eq": lambda a, b: a == b, "==": lambda a, b: a == b,
    "ne": lambda a, b: a != b, "!=": lambda a, b: a != b,
}


def _compare(a: Any, op: str, value: Any) -> Any:
    try:
        fn = COMPARE_OPS[op]
    except KeyError:
        raise ValueError(
            f"unknown comparison op {op!r}; use one of {sorted(COMPARE_OPS)}"
        )
    return fn(a, value)


# The engineered-feature ops apply_recipe_entry understands — the single source
# of truth for both the runtime dispatch below and the server's up-front recipe
# validation (so the agent is told the exact vocabulary instead of discovering it
# via a cryptic "unknown recipe op" at materialize time).
RECIPE_OPS = frozenset(
    {
        # --- meta-feature / composite construction (across a column group) ---
        "count_nonzero",
        "count_equal",
        "count_threshold",
        "count_missing",
        "weighted_sum",
        "ratio",
        "sum",
        "mean",
        "min",
        "max",
        "range",
        "std",
        "n_distinct",
        "which_max",
        "and",
        "or",
        "xor",
        "nor",
        "multiply",
        # --- single-column transforms ---
        "threshold",
        "is_missing",
        "log1p",
        "sqrt",
        "square",
        "abs",
        # --- statistic-bearing (frozen on train, replayed on test) ---
        "bin",
        "group_agg",
        "frequency_encode",
    }
)


def apply_recipe_entry(X: pd.DataFrame, entry: dict[str, Any]) -> pd.Series:
    """Materialize one engineered column from a (frozen) recipe entry.

    Row-wise ops are stateless. Statistic-bearing ops (``bin``, ``group_agg``)
    read their statistic from ``entry['params']`` — frozen at fit time on the
    training fold — and never recompute it, so train and test stay aligned.
    """
    op = entry["op"]
    inputs = entry.get("inputs", [])
    params = entry.get("params", {})
    present = _present(X, inputs)
    out: Any

    if op == "count_nonzero":
        out = (X[present].fillna(0) != 0).sum(axis=1)
    elif op == "count_equal":
        out = (X[present] == params.get("value")).sum(axis=1)
    elif op == "ratio":
        out = _to_num(X[inputs[0]]) / _to_num(X[inputs[1]]).replace(0, np.nan)
    elif op == "sum":
        out = X[present].sum(axis=1)
    elif op == "mean":
        out = X[present].mean(axis=1)
    elif op == "min":
        out = X[present].min(axis=1)
    elif op == "max":
        out = X[present].max(axis=1)
    elif op == "range":
        out = X[present].max(axis=1) - X[present].min(axis=1)
    elif op in ("and", "or", "xor", "nor"):
        count = (X[present].fillna(0) != 0).sum(axis=1)
        if op == "and":
            out = (count == len(present)).astype(int)
        elif op == "or":
            out = (count > 0).astype(int)
        elif op == "xor":
            out = (count % 2).astype(int)
        else:  # nor
            out = (count == 0).astype(int)
    elif op == "multiply":
        result: pd.Series | None = None
        for col in present:
            series = _to_num(X[col])
            result = series if result is None else result * series
        out = result if result is not None else pd.Series(np.nan, index=X.index)
    elif op == "bin":
        values = _to_num(X[inputs[0]])
        out = pd.Series(
            np.digitize(values.to_numpy(), bins=params["edges"]), index=X.index
        )
    elif op == "group_agg":
        group_col = params.get("group_col", inputs[0])
        out = X[group_col].map(params["group_map"]).fillna(params.get("fallback"))
    # --- meta-feature / composite construction ---------------------------------
    elif op == "count_threshold":
        num = _num_df(X, present)
        out = _compare(num, params.get("op", "gt"), params.get("value", 0)).sum(axis=1)
    elif op == "count_missing":
        out = X[present].isna().sum(axis=1)
    elif op == "weighted_sum":
        weights = params.get("weights") or []
        out = pd.Series(0.0, index=X.index)
        for col, w in zip(inputs, weights):
            if col in X.columns:
                out = out + _to_num(X[col]).fillna(0) * float(w)
    elif op == "std":
        out = _num_df(X, present).std(axis=1)
    elif op == "n_distinct":
        out = X[present].nunique(axis=1) if present else pd.Series(0, index=X.index)
    elif op == "which_max":
        num = _num_df(X, present)
        if num.shape[1] == 0:
            out = pd.Series(np.nan, index=X.index)
        else:
            names = np.array(list(num.columns))
            picked = names[num.fillna(-np.inf).to_numpy().argmax(axis=1)]
            out = pd.Series(picked, index=X.index).where(~num.isna().all(axis=1))
    # --- single-column transforms ----------------------------------------------
    elif op == "threshold":
        s = _to_num(X[inputs[0]])
        out = _compare(s, params.get("op", "gt"), params.get("value", 0)).astype(int)
    elif op == "is_missing":
        out = X[inputs[0]].isna().astype(int)
    elif op == "log1p":
        s = _to_num(X[inputs[0]])
        out = np.log1p(s.where(s > -1))
    elif op == "sqrt":
        s = _to_num(X[inputs[0]])
        out = np.sqrt(s.where(s >= 0))
    elif op == "square":
        s = _to_num(X[inputs[0]])
        out = s * s
    elif op == "abs":
        out = _to_num(X[inputs[0]]).abs()
    # --- statistic-bearing (frozen on train) -----------------------------------
    elif op == "frequency_encode":
        out = X[inputs[0]].astype(str).map(params.get("freq_map", {})).fillna(
            params.get("fallback", 0.0)
        )
    else:
        raise ValueError(
            f"unknown recipe op: {op!r}; valid ops are {sorted(RECIPE_OPS)}"
        )
    return cast("pd.Series", out)


def new_dataset_id() -> str:
    return "ds_" + _new_id()


def new_preprocessor_id() -> str:
    return "pre_" + _new_id()


def new_selector_id() -> str:
    return "sel_" + _new_id()


def new_model_id() -> str:
    return "mdl_" + _new_id()


def new_domain_fixer_id() -> str:
    return "df_" + _new_id()


def new_imputer_id() -> str:
    return "imp_" + _new_id()


def new_recipe_id() -> str:
    return "rec_" + _new_id()
