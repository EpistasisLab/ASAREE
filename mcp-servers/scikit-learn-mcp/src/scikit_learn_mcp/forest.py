"""The random-forest pipeline: preprocessing, importances, out-of-bag scoring.

The nonlinear counterpart to :mod:`logistic`, and declarative for the same
reason: a random forest on a tabular file has one standard recipe (impute,
one-hot the categoricals, fit an ensemble of trees), and re-emitting that recipe
as fresh sklearn source on every call only adds ways to get it wrong.

Two things differ from the logistic pipeline, both because of what a tree is:

* **Numerics are not scaled.** A tree splits on thresholds, so it is invariant
  to any monotone rescaling of a feature -- standardizing costs a step and
  changes nothing. There is deliberately no ``scale`` argument to get wrong.
* **A fitted forest reports out-of-bag predictions for free.** Each bootstrapped
  tree leaves roughly a third of the rows out, so every training row can be
  scored by the trees that never drew it. That is a genuine held-out estimate
  over the *whole* training split at no extra fits, and it's reported alongside
  the holdout metrics whenever ``bootstrap`` is on.

Feature importances come back by default for the same reason logistic returns
coefficients -- but impurity importance is a training-data statistic biased
toward high-cardinality and continuous features, so the caveat travels with the
numbers and a permutation alternative is one argument away.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from scikit_learn_mcp import scoring

# Estimator-agnostic plumbing that happens to live in logistic.py because it was
# written there first: none of it touches LogisticRegression, all of it works off
# a pipeline whose classifier step is named "clf". Re-exported here so the
# random-forest tools read as `forest.<helper>` rather than reaching across.
from scikit_learn_mcp.logistic import (  # noqa: F401
    SpecError,
    column_roles,
    expand_grid,
    out_of_fold_proba,
    positive_column,
    resolve_threshold,
    usable_n_splits,
)

_CRITERIA = ("gini", "entropy", "log_loss")
_CLASS_WEIGHTS = ("", "balanced", "balanced_subsample")
_NUMERIC_IMPUTE = ("mean", "median", "most_frequent")

# The grid ``tune_random_forest`` searches when the caller supplies none.
# Small on purpose, and not a sweep of ``n_estimators``: more trees monotonically
# reduce variance and never overfit, so the tree count is a compute decision
# rather than a tunable one. What actually moves the AUC is how decorrelated the
# trees are (``max_features``), how far they may memorize (``min_samples_leaf``),
# and whether the rare class is reweighted.
DEFAULT_GRID: dict[str, list[Any]] = {
    "max_features": ["sqrt", 0.5],
    "min_samples_leaf": [1, 5],
    "class_weight": ["", "balanced"],
}
GRID_KEYS = (
    "n_estimators",
    "criterion",
    "max_depth",
    "min_samples_split",
    "min_samples_leaf",
    "max_features",
    "bootstrap",
    "class_weight",
    "numeric_impute",
)


def resolve_max_features(value: Any) -> Any:
    """Turn the tool-facing ``max_features`` into what sklearn wants.

    Accepts 'sqrt'/'log2', 'all' (or '') for every feature, a fraction in (0, 1]
    or an integer count -- as a string, since MCP arguments are typed, or as a
    real number when it arrives from a tuning grid.
    """
    if isinstance(value, bool):
        raise SpecError(f"max_features must be 'sqrt', 'log2', 'all', a fraction or a count, got {value!r}")
    if isinstance(value, int | float):
        number = float(value)
    else:
        text = str(value).strip().lower()
        if text in {"sqrt", "log2"}:
            return text
        if text in {"", "all", "none"}:
            return None
        try:
            number = float(text)
        except ValueError:
            raise SpecError(
                f"max_features must be 'sqrt', 'log2', 'all', a fraction in (0, 1] or a feature count, got {value!r}"
            ) from None
    if number > 1:
        if not float(number).is_integer():
            raise SpecError(f"max_features above 1 is a feature count and must be a whole number, got {number}")
        return int(number)
    if number <= 0:
        raise SpecError(f"max_features as a fraction must be greater than 0, got {number}")
    return number


def make_pipeline_factory(
    features: pd.DataFrame,
    *,
    n_estimators: int = 300,
    criterion: str = "gini",
    max_depth: int = 0,
    min_samples_split: int = 2,
    min_samples_leaf: int = 1,
    max_features: Any = "sqrt",
    bootstrap: bool = True,
    class_weight: str = "",
    numeric_impute: str = "median",
    max_categories: int = 20,
    random_seed: int = 42,
) -> tuple[Any, dict[str, Any]]:
    """Return (factory, spec): a zero-arg pipeline builder plus what it will build.

    A *factory* rather than a fitted pipeline because cross-validation needs a
    fresh, never-fitted estimator per fold -- refitting one instance would leak
    the previous fold's imputation statistics into the next.
    """
    criterion = (criterion or "gini").lower()
    if criterion not in _CRITERIA:
        raise SpecError(f"criterion must be one of {list(_CRITERIA)}, got {criterion!r}")
    if class_weight not in _CLASS_WEIGHTS:
        raise SpecError(f"class_weight must be one of {list(_CLASS_WEIGHTS)}, got {class_weight!r}")
    if numeric_impute not in _NUMERIC_IMPUTE:
        raise SpecError(f"numeric_impute must be one of {list(_NUMERIC_IMPUTE)}, got {numeric_impute!r}")
    if n_estimators < 1:
        raise SpecError(f"n_estimators must be at least 1, got {n_estimators}")
    if min_samples_split < 2:
        raise SpecError(f"min_samples_split must be at least 2, got {min_samples_split}")
    if min_samples_leaf < 1:
        raise SpecError(f"min_samples_leaf must be at least 1, got {min_samples_leaf}")
    if max_depth < 0:
        raise SpecError(f"max_depth must be 0 (unlimited) or a positive depth, got {max_depth}")
    # 0 rather than None because an MCP argument is typed and there is no
    # null to pass; sklearn's "grow until pure" is spelled None internally.
    depth = None if max_depth == 0 else int(max_depth)
    resolved_features = resolve_max_features(max_features)
    if not bootstrap and class_weight == "balanced_subsample":
        raise SpecError("class_weight='balanced_subsample' needs bootstrap=True -- there are no subsamples otherwise")

    roles = column_roles(features, max_categories)
    if not roles["numeric"] and not roles["categorical"]:
        raise SpecError(
            "no usable feature columns: every non-numeric column exceeded "
            f"max_categories={max_categories} ({roles['dropped_high_cardinality']})"
        )

    def factory() -> Pipeline:
        transformers = []
        if roles["numeric"]:
            # Imputed but not scaled -- see the module docstring.
            transformers.append(
                ("num", Pipeline([("impute", SimpleImputer(strategy=numeric_impute))]), roles["numeric"])
            )
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
        clf = RandomForestClassifier(
            n_estimators=int(n_estimators),
            criterion=criterion,
            max_depth=depth,
            min_samples_split=int(min_samples_split),
            min_samples_leaf=int(min_samples_leaf),
            max_features=resolved_features,
            bootstrap=bool(bootstrap),
            # Free with the fit when bootstrapping, and the source of the
            # `out_of_bag` block; sklearn rejects it outright without bootstrap.
            oob_score=bool(bootstrap),
            class_weight=class_weight or None,
            random_state=random_seed,
            n_jobs=-1,
        )
        return Pipeline([("pre", ColumnTransformer(transformers, remainder="drop")), ("clf", clf)])

    spec = {
        "n_estimators": int(n_estimators),
        "criterion": criterion,
        "max_depth": depth,
        "min_samples_split": int(min_samples_split),
        "min_samples_leaf": int(min_samples_leaf),
        "max_features": resolved_features,
        "bootstrap": bool(bootstrap),
        "class_weight": class_weight or None,
        "numeric_impute": numeric_impute,
        "max_categories": int(max_categories),
        **roles,
    }
    return factory, spec


_IMPURITY_NOTE = (
    "mean decrease in impurity, measured on the training data: biased toward continuous and "
    "high-cardinality features, and it says nothing about the direction of an effect. Pass "
    "permutation_importance=True for a held-out alternative."
)


def importances(pipe: Pipeline, *, top_k: int = 25) -> dict[str, Any]:
    """Impurity importances of the fitted forest, largest first.

    ``std`` is the spread across the individual trees: a feature the whole
    forest agrees on and one that only a handful of trees ever split on can
    report the same mean, and only the spread tells them apart.
    """
    pre, clf = pipe.named_steps["pre"], pipe.named_steps["clf"]
    names = [str(n) for n in pre.get_feature_names_out()]
    values = np.asarray(clf.feature_importances_, dtype=float)
    per_tree = np.asarray([t.feature_importances_ for t in clf.estimators_], dtype=float)
    std = per_tree.std(axis=0) if per_tree.size else np.zeros_like(values)
    order = np.argsort(-values)[: max(1, top_k)]
    return {
        "kind": "impurity",
        "note": _IMPURITY_NOTE,
        "terms": [
            {"feature": names[i], "importance": scoring.finite(values[i]), "std": scoring.finite(std[i])}
            for i in order
        ],
    }


def permutation_terms(
    pipe: Pipeline,
    features: pd.DataFrame,
    y: pd.Series,
    *,
    scorer: str,
    top_k: int = 25,
    n_repeats: int = 5,
    random_seed: int = 42,
) -> dict[str, Any]:
    """Permutation importance on the HELD-OUT split, as raw (unencoded) columns.

    The honest counterpart to :func:`importances`: how much the score drops when
    one column is shuffled, measured on data the forest never saw. Reported per
    *input* column rather than per encoded feature, because shuffling one
    one-hot level while its siblings stay put measures nothing a caller wants.

    This reads the test split but never feeds back into the model: no fit, no
    threshold, no candidate is chosen from it, so it changes no reported metric.
    """
    result = permutation_importance(
        pipe, features, y, scoring=scorer, n_repeats=int(n_repeats), random_state=random_seed, n_jobs=-1
    )
    means = np.asarray(result.importances_mean, dtype=float)
    stds = np.asarray(result.importances_std, dtype=float)
    order = np.argsort(-means)[: max(1, top_k)]
    return {
        "kind": "permutation",
        "scorer": scorer,
        "n_repeats": int(n_repeats),
        "note": (
            f"drop in {scorer} on the held-out split when the column is shuffled, averaged over "
            f"{int(n_repeats)} shuffles. Near-zero or negative means the forest did not rely on it."
        ),
        "terms": [
            {
                "feature": str(features.columns[i]),
                "importance": scoring.finite(means[i]),
                "std": scoring.finite(stds[i]),
            }
            for i in order
        ],
    }


def oob_metrics(
    pipe: Pipeline,
    y: pd.Series,
    classes: list[Any],
    task_type: str,
    positive_label: str,
) -> dict[str, Any] | None:
    """Score the training split on its own out-of-bag predictions, or None.

    Every row is predicted by the trees that did not draw it in their bootstrap
    sample, so this is held-out in the way that matters -- over all n training
    rows, at no extra fits. Returns None when there is nothing honest to report:
    ``bootstrap=False`` (no bags), too few trees to leave every row out at least
    once, or a single class among the rows that do have a prediction.
    """
    clf = pipe.named_steps["clf"]
    raw = getattr(clf, "oob_decision_function_", None)
    if raw is None:
        return None
    proba = np.asarray(raw, dtype=float)
    # A row drawn by every single tree has no OOB prediction; sklearn writes NaN.
    covered = np.isfinite(proba).all(axis=1)
    y_covered = y.to_numpy()[covered]
    if covered.sum() < 2 or len(np.unique(y_covered)) < 2:
        return None

    position = {cls: j for j, cls in enumerate(clf.classes_)}
    aligned = np.column_stack(
        [proba[covered, position[cls]] if cls in position else np.zeros(int(covered.sum())) for cls in classes]
    )
    coverage = {
        "n_rows_scored": int(covered.sum()),
        "n_rows_total": int(len(y)),
        "note": "each row scored by the trees that did not draw it -- a held-out estimate over the whole training split",
    }
    if task_type == "binary":
        label, column = positive_column(classes, positive_label)
        y_bin = (y_covered == label).astype(int)
        bundle = scoring.binary_bundle(y_bin, aligned[:, column], 0.5)
        keep = {"roc_auc", "average_precision", "brier_score", "accuracy", "balanced_accuracy", "f1"}
    else:
        bundle = scoring.multiclass_bundle(y_covered, aligned, classes)
        keep = {"roc_auc_ovr", "accuracy", "balanced_accuracy", "f1_macro", "log_loss"}
    return {**coverage, **{k: v for k, v in bundle.items() if k in keep}}
