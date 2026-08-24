"""The logistic-regression pipeline: preprocessing, coefficients, cross-validation.

Everything here backs the two DECLARATIVE tools (``fit_logistic_regression``,
``cross_validate_logistic_regression``), which is a deliberate departure from
the script-execution tools next door. Logistic regression on a tabular file has
one standard-of-care recipe -- impute, scale the numerics, one-hot the
categoricals, fit -- and asking a model to re-derive that recipe as fresh
sklearn source on every call buys nothing but new ways to get it wrong
(unscaled features silently wrecking a penalized fit, an unseen category
raising at predict time, a threshold tuned on the test split). Exposing the
recipe's *decisions* as typed arguments keeps the interesting choices in the
caller's hands and the mechanical ones here.

``run_logistic_regression_script`` remains for the cases this can't express.
"""

from __future__ import annotations

import inspect
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from scikit_learn_mcp import scoring


class SpecError(Exception):
    """Raised when the requested model spec can't be built (bad penalty/solver/etc.)."""


# Which solvers can actually optimize each penalty. sklearn raises a terse
# ValueError for a mismatch; catching it here lets the message name the
# alternatives instead.
_PENALTY_SOLVERS: dict[str, tuple[str, ...]] = {
    "l2": ("lbfgs", "liblinear", "newton-cg", "newton-cholesky", "sag", "saga"),
    "l1": ("liblinear", "saga"),
    "elasticnet": ("saga",),
    "none": ("lbfgs", "newton-cg", "newton-cholesky", "sag", "saga"),
}
# Chosen when solver="auto", so an l1 request doesn't fail on the l2 default.
_DEFAULT_SOLVER = {"l2": "lbfgs", "l1": "saga", "elasticnet": "saga", "none": "lbfgs"}
_NUMERIC_IMPUTE = ("mean", "median", "most_frequent")

# sklearn 1.8 deprecated `penalty=` in favour of expressing the same thing as a
# point on the l1/l2 continuum (`l1_ratio`), and removes it in 1.10. Detected by
# reading the installed signature rather than parsing __version__, because the
# default value IS the deprecation marker -- there's no version arithmetic to get
# wrong. `penalty` stays the tool-facing argument either way: it's the name the
# caller (and every logistic-regression tutorial) knows.
_PENALTY_DEPRECATED = (
    inspect.signature(LogisticRegression.__init__).parameters["penalty"].default == "deprecated"
)
_PENALTY_AS_RATIO = {"l2": 0.0, "l1": 1.0}


def _penalty_kwargs(penalty: str, C: float, l1_ratio: float) -> dict[str, Any]:  # noqa: N803 -- sklearn's name
    """The estimator arguments that express *penalty* on the installed sklearn."""
    if not _PENALTY_DEPRECATED:
        return {
            "penalty": None if penalty == "none" else penalty,
            "C": C,
            "l1_ratio": l1_ratio if penalty == "elasticnet" else None,
        }
    if penalty == "none":
        # No penalty is the limit of an infinitely weak one; C is its inverse.
        return {"C": np.inf, "l1_ratio": 0.0}
    return {"C": C, "l1_ratio": _PENALTY_AS_RATIO.get(penalty, l1_ratio)}


def column_roles(features: pd.DataFrame, max_categories: int) -> dict[str, list[str]]:
    """Sort feature columns into numeric / one-hot / dropped.

    High-cardinality object columns are dropped rather than one-hot encoded: an
    ID, a free-text note or a timestamp string would otherwise expand into
    thousands of columns that are perfectly predictive on train and useless on
    test. They're reported back by name so the caller can see what was left out
    and encode it deliberately if it mattered.
    """
    numeric = [c for c in features.columns if pd.api.types.is_numeric_dtype(features[c])]
    categorical, dropped = [], []
    for col in features.columns:
        if col in numeric:
            continue
        (categorical if features[col].nunique(dropna=True) <= max_categories else dropped).append(col)
    return {
        "numeric": [str(c) for c in numeric],
        "categorical": [str(c) for c in categorical],
        "dropped_high_cardinality": [str(c) for c in dropped],
    }


def resolve_solver(penalty: str, solver: str) -> tuple[str, str]:
    """Validate the (penalty, solver) pair, filling in a default solver."""
    penalty = (penalty or "l2").lower()
    if penalty not in _PENALTY_SOLVERS:
        raise SpecError(f"penalty must be one of {sorted(_PENALTY_SOLVERS)}, got {penalty!r}")
    solver = (solver or "auto").lower()
    if solver == "auto":
        return penalty, _DEFAULT_SOLVER[penalty]
    if solver not in _PENALTY_SOLVERS[penalty]:
        raise SpecError(
            f"solver {solver!r} cannot fit penalty {penalty!r}; "
            f"use one of {list(_PENALTY_SOLVERS[penalty])} or 'auto'"
        )
    return penalty, solver


def make_pipeline_factory(
    features: pd.DataFrame,
    *,
    penalty: str = "l2",
    C: float = 1.0,  # noqa: N803 -- sklearn's name for the inverse regularization strength
    solver: str = "auto",
    class_weight: str = "",
    max_iter: int = 1000,
    l1_ratio: float = 0.5,
    scale: bool = True,
    numeric_impute: str = "median",
    max_categories: int = 20,
    random_seed: int = 42,
) -> tuple[Any, dict[str, Any]]:
    """Return (factory, spec): a zero-arg pipeline builder plus what it will build.

    A *factory* rather than a fitted pipeline because cross-validation needs a
    fresh, never-fitted estimator per fold -- refitting one instance would leak
    the previous fold's imputation statistics and scaler means into the next.
    """
    penalty, solver = resolve_solver(penalty, solver)
    if numeric_impute not in _NUMERIC_IMPUTE:
        raise SpecError(f"numeric_impute must be one of {list(_NUMERIC_IMPUTE)}, got {numeric_impute!r}")
    if C <= 0:
        raise SpecError(f"C must be > 0, got {C}")
    if penalty == "elasticnet" and not 0.0 <= l1_ratio <= 1.0:
        raise SpecError(f"l1_ratio must be between 0 and 1, got {l1_ratio}")

    roles = column_roles(features, max_categories)
    if not roles["numeric"] and not roles["categorical"]:
        raise SpecError(
            "no usable feature columns: every non-numeric column exceeded "
            f"max_categories={max_categories} ({roles['dropped_high_cardinality']})"
        )

    def factory() -> Pipeline:
        transformers = []
        if roles["numeric"]:
            steps: list[tuple[str, Any]] = [("impute", SimpleImputer(strategy=numeric_impute))]
            if scale:
                steps.append(("scale", StandardScaler()))
            transformers.append(("num", Pipeline(steps), roles["numeric"]))
        if roles["categorical"]:
            transformers.append(
                (
                    "cat",
                    Pipeline(
                        [
                            ("impute", SimpleImputer(strategy="most_frequent")),
                            # handle_unknown='ignore' so a category present only
                            # in the test split encodes as all-zeros instead of
                            # raising after the model is already fitted.
                            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                        ]
                    ),
                    roles["categorical"],
                )
            )
        clf = LogisticRegression(
            **_penalty_kwargs(penalty, C, l1_ratio),
            solver=solver,
            class_weight=class_weight or None,
            max_iter=max_iter,
            random_state=random_seed,
        )
        return Pipeline([("pre", ColumnTransformer(transformers, remainder="drop")), ("clf", clf)])

    spec = {
        "penalty": penalty,
        "C": float(C),
        "solver": solver,
        "class_weight": class_weight or None,
        "max_iter": int(max_iter),
        "l1_ratio": float(l1_ratio) if penalty == "elasticnet" else None,
        "scale_numeric": bool(scale),
        "numeric_impute": numeric_impute,
        "max_categories": int(max_categories),
        **roles,
    }
    return factory, spec


def coefficients(pipe: Pipeline, *, scaled: bool, top_k: int = 25) -> dict[str, Any]:
    """Fitted coefficients and odds ratios, largest |coef| first.

    The reason to reach for logistic regression over a boosted tree is that the
    model is readable, so the readout is returned by default rather than left
    for the caller to dig out of the estimator. Odds ratios are only meaningful
    per *encoded* feature (a one-hot level, a standardized numeric), so the
    encoded name is what's reported -- and whether the numerics were
    standardized changes what "per unit" means, hence the note.
    """
    pre, clf = pipe.named_steps["pre"], pipe.named_steps["clf"]
    names = [str(n) for n in pre.get_feature_names_out()]
    coef = np.atleast_2d(np.asarray(clf.coef_, dtype=float))
    intercept = np.atleast_1d(np.asarray(clf.intercept_, dtype=float))
    note = (
        "numeric coefficients are per 1 standard deviation (features were standardized)"
        if scaled
        else "numeric coefficients are per 1 unit of the raw feature"
    )

    if coef.shape[0] == 1:  # binary
        return {"intercept": scoring.finite(intercept[0]), "note": note, "terms": _terms(names, coef[0], top_k)}
    return {
        "note": note,
        "per_class": [
            {
                "class": str(cls),
                "intercept": scoring.finite(intercept[i]),
                "terms": _terms(names, coef[i], top_k),
            }
            for i, cls in enumerate(clf.classes_)
        ],
    }


def _terms(names: list[str], weights: np.ndarray, top_k: int) -> list[dict[str, Any]]:
    order = np.argsort(-np.abs(weights))[: max(1, top_k)]
    return [
        {
            "feature": names[i],
            "coef": scoring.finite(weights[i]),
            "odds_ratio": scoring.odds_ratio(weights[i]),
        }
        for i in order
    ]


def usable_n_splits(y: pd.Series, requested: int, groups: pd.Series | None = None) -> int:
    """Clamp *requested* to what the rarest class -- and the groups -- can support.

    Clamped rather than rejected: an agent asking for 10-fold CV on a dataset
    whose rarest class has 6 rows wants cross-validation, not a lecture, and
    the realized fold count is reported back alongside the numbers.
    """
    if requested < 2:
        raise SpecError(f"n_splits must be at least 2, got {requested}")
    rarest = int(y.value_counts().min())
    if rarest < 2:
        raise SpecError("cross-validation needs at least 2 rows in every class")
    limit = min(requested, rarest)
    if groups is not None:
        n_groups = int(groups.nunique())
        if n_groups < 2:
            raise SpecError("cross-validation needs at least 2 distinct groups")
        limit = min(limit, n_groups)
    return limit


def out_of_fold_proba(
    factory: Any,
    features: pd.DataFrame,
    y: pd.Series,
    folds: list[tuple[np.ndarray, np.ndarray]],
) -> tuple[np.ndarray, list[Any], list[Pipeline]]:
    """Out-of-fold probabilities over *folds*, columns ordered by sorted class.

    Every row gets a prediction from a model that never saw it, which is what
    makes these safe to tune a threshold or a hyperparameter on, and what makes
    the pooled AUC an honest one. Per-fold ``predict_proba`` columns are
    realigned to the global class order, since a fold whose training side
    missed a rare class emits fewer of them.
    """
    classes = sorted(np.unique(y).tolist())
    oof = np.zeros((len(y), len(classes)), dtype=float)
    fitted: list[Pipeline] = []
    for train_idx, test_idx in folds:
        pipe = factory()
        pipe.fit(features.iloc[train_idx], y.iloc[train_idx])
        proba = np.asarray(pipe.predict_proba(features.iloc[test_idx]), dtype=float)
        position = {cls: j for j, cls in enumerate(pipe.named_steps["clf"].classes_)}
        for j, cls in enumerate(classes):
            if cls in position:
                oof[test_idx, j] = proba[:, position[cls]]
        fitted.append(pipe)
    return oof, classes, fitted


# The grid ``tune_logistic_regression`` searches when the caller supplies none.
# Deliberately small: it spans four orders of magnitude of regularization and
# the balanced/unbalanced choice, which is where nearly all of the achievable
# AUC on a clean tabular file lives. A bigger default grid mostly buys variance
# -- picking the winner of sixty near-identical CV scores is how you overfit
# the validation folds.
DEFAULT_GRID: dict[str, list[Any]] = {
    "C": [0.01, 0.1, 1.0, 10.0],
    "penalty": ["l2"],
    "class_weight": ["", "balanced"],
}
_GRID_KEYS = ("C", "penalty", "solver", "class_weight", "l1_ratio", "scale", "numeric_impute", "max_iter")
_MAX_GRID = 60


def expand_grid(grid: dict[str, list[Any]], keys: tuple[str, ...] = _GRID_KEYS) -> list[dict[str, Any]]:
    """Every combination in *grid*, as a list of keyword overrides.

    *keys* is the estimator's tunable argument list -- the caller passes its own
    (see forest.GRID_KEYS), since the expansion itself is estimator-agnostic.
    """
    unknown = sorted(set(grid) - set(keys))
    if unknown:
        raise SpecError(f"unknown grid key(s) {unknown}; tunable arguments are {list(keys)}")
    combos: list[dict[str, Any]] = [{}]
    for key in sorted(grid):
        values = grid[key]
        if not isinstance(values, list) or not values:
            raise SpecError(f"grid entry {key!r} must be a non-empty list, got {values!r}")
        combos = [{**combo, key: value} for combo in combos for value in values]
    if len(combos) > _MAX_GRID:
        raise SpecError(
            f"grid expands to {len(combos)} candidates (limit {_MAX_GRID}); "
            "each one is fit once per fold, so narrow it before searching"
        )
    return combos


def positive_column(classes: list[Any], positive_label: str) -> tuple[Any, int]:
    """The (label, column index) treated as the positive class for a binary target."""
    if positive_label == "":
        return classes[-1], len(classes) - 1
    try:
        label = type(classes[0])(positive_label)
    except (TypeError, ValueError) as e:
        raise SpecError(f"positive_label {positive_label!r} is not coercible to the target's dtype: {e}") from e
    if label not in classes:
        raise SpecError(f"positive_label {positive_label!r} is not one of the target's classes {classes}")
    return label, classes.index(label)


def resolve_threshold(rule: str, y: np.ndarray, proba: np.ndarray) -> tuple[float, str]:
    """Turn the ``threshold`` argument into a number, tuning on TRAIN-side labels.

    *y*/*proba* must be training out-of-fold predictions -- see
    :func:`scoring.best_threshold`.
    """
    rule = (rule or "0.5").strip().lower()
    try:
        value = float(rule)
    except ValueError:
        if rule not in {"youden", "f1"}:
            raise SpecError(
                f"threshold must be a number in (0, 1), 'youden' or 'f1', got {rule!r}"
            ) from None
        return scoring.best_threshold(y, proba, rule), rule
    if not 0.0 < value < 1.0:
        raise SpecError(f"threshold must be strictly between 0 and 1, got {value}")
    return value, "fixed"
