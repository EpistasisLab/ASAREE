"""scikit-learn-mcp -- tabular classification on a file, end to end.

Sized for one job: an agent is handed a dataset path and a one-line instruction
("fit a model and report the AUC"), and has to work out the rest itself. That
framing decides the shape of every tool here.

**Declarative first.** Two estimator families -- logistic regression and random
forest -- each with a ``fit_``/``cross_validate_``/``tune_`` trio that takes the
*decisions* (regularization or tree depth, class weighting, how to split) as
typed arguments and owns the mechanics (impute, scale, one-hot, fit, score).
Asking a model to re-emit that pipeline as fresh sklearn source on every call
buys nothing but new ways to get it wrong: unscaled features quietly wrecking a
penalized fit, an unseen category raising at predict time, a threshold tuned on
the test split. The two families share their split, scoring and provenance
blocks, so their results are directly comparable -- run both.
``run_logistic_regression_script`` remains for what the arguments can't express,
and is also the only way to fit a continuous target, since every declarative
tool here is a classifier.

**Nothing is scored on data the model was fit on.** Every metric comes from a
held-out split (or out-of-fold predictions), computed by :mod:`scoring` from
labels the estimator never saw. Threshold selection and hyperparameter search
run against training-side out-of-fold predictions only, so the test split stays
untouched until the final scorecard.

**The split is a first-class, audited, hashed object** (:mod:`splitting`) rather
than an implicit ``test_size``. A random split of non-independent rows inflates
an AUC and looks *better* for it, so grouped and temporal strategies are
available and the realized split is checked for the known leaks on every call.

Nothing here imports ASAREE. The dataset arrives as a path or URI
(:mod:`data`), so the server is usable from any MCP client against any file.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import io
import json
import traceback
from typing import Any

import numpy as np
import pandas as pd
from mcp.server import FastMCP

from scikit_learn_mcp import forest, logistic, profile, scoring, splitting
from scikit_learn_mcp.data import DataError, frame_sha256, load_frame, split_xy

# What this server tells a client it is, during the initialize handshake --
# the server-level counterpart to each tool's own description. Written for the
# agent that will use the tools, so it answers "which of these do I reach for
# and in what order", not "how is this implemented" (that's the module
# docstring above).
INSTRUCTIONS = """\
Fit and evaluate a tabular classifier from a dataset file, with every metric \
computed on data the model never saw.

Start with describe_dataset to see the columns and pick a target, then \
describe_split to check the split isn't leaking (grouped or temporal data \
needs a strategy other than random). Then fit: logistic_regression and \
random_forest each have a fit_/cross_validate_/tune_ trio, and they share \
their split, scoring and provenance blocks, so running both gives directly \
comparable results.

Every declarative tool here is a classifier. A continuous target needs \
run_logistic_regression_script with task_type='regression', which is also the \
escape hatch for any pipeline the typed arguments can't express."""

mcp = FastMCP("scikit-learn-mcp", instructions=INSTRUCTIONS)

_CLASSIFICATION = {"binary", "multiclass"}
_TASK_TYPES = _CLASSIFICATION | {"regression"}
# Truncation budgets. A tool result is read by a model, so an unbounded
# traceback or a script that prints in a loop would otherwise cost more context
# than the metrics the call was made for.
_ERR_CHARS = 2000
_STDOUT_CHARS = 4000


# --------------------------------------------------------------------------
# Shared plumbing
# --------------------------------------------------------------------------


class _Prepared:
    """A loaded, split, audited dataset -- what every declarative tool starts from."""

    def __init__(self, frame: pd.DataFrame, spec: splitting.SplitSpec, split: splitting.Split, task_type: str):
        self.frame = frame
        self.spec = spec
        self.split = split
        self.task_type = task_type
        self.data_sha256 = frame_sha256(frame)
        self.audit = splitting.audit(split, spec, classification=task_type in _CLASSIFICATION)

    def provenance(self, target_column: str, data_path: str) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "target_column": target_column,
            "data_path": data_path,
            "n_rows": int(len(self.frame)),
            "n_features": int(self.split.x_train.shape[1]),
            "feature_columns": [str(c) for c in self.split.x_train.columns],
            "split": {**self.spec.as_dict(), **self.audit},
            "data_sha256": self.data_sha256,
            "split_sha256": splitting.spec_sha256(self.spec, self.data_sha256),
            "package_versions": scoring.env_provenance(),
        }


def _prepare(
    data_path: str,
    target_column: str,
    task_type: str,
    split_json: str,
    test_size: float,
    random_seed: int,
    stratify: bool,
) -> _Prepared:
    """Load, resolve the task type, split, and audit -- raising on anything wrong."""
    spec = splitting.parse_spec(split_json, test_size=test_size, random_seed=random_seed, stratify=stratify)
    frame, spec = splitting.load_for_spec(data_path, spec)
    if target_column not in frame.columns:
        cols = ", ".join(map(str, frame.columns[:25]))
        raise DataError(f"target column {target_column!r} not in dataset; columns are: {cols}")

    resolved = (task_type or "auto").lower()
    if resolved == "auto":
        resolved = profile.infer_task_type(frame[target_column])
        if resolved == "degenerate":
            raise DataError(f"target {target_column!r} has a single distinct value -- nothing to predict")
    if resolved not in _TASK_TYPES:
        raise DataError(f"task_type must be 'auto' or one of {sorted(_TASK_TYPES)}, got {task_type!r}")
    if resolved == "regression":
        raise DataError(
            f"target {target_column!r} looks continuous ({frame[target_column].nunique()} distinct values). "
            "Every declarative tool here is a classifier -- use run_logistic_regression_script with "
            "task_type='regression', or pass task_type='multiclass' if these really are class labels."
        )

    split = splitting.apply_spec(frame, target_column, spec, classification=True)
    return _Prepared(frame, spec, split, resolved)


def _error(message: str, **extra: Any) -> str:
    return scoring.dumps({"error": message, **extra})


def _guarded(fn: Any) -> Any:
    """Turn this package's own exceptions into a tool result rather than a crash.

    ``functools.wraps`` is load-bearing, not tidiness: it sets ``__wrapped__``,
    which is what makes ``inspect.signature`` see through the wrapper -- and
    FastMCP builds each tool's JSON schema from that signature. Without it every
    decorated tool would advertise ``(*args, **kwargs)``.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return fn(*args, **kwargs)
        except (DataError, splitting.SplitError, logistic.SpecError) as e:
            return _error(str(e))
        except Exception as e:  # noqa: BLE001 -- surfaced to the caller, never raised at the transport
            return _error(f"{type(e).__name__}: {e}", traceback=traceback.format_exc()[-_ERR_CHARS:])

    return wrapper


def _model_block(model_spec: dict[str, Any], estimator: str) -> dict[str, Any]:
    """The estimator's hyperparameters, minus the column roles reported separately."""
    roles = {"numeric", "categorical", "dropped_high_cardinality"}
    return {"estimator": estimator, **{k: v for k, v in model_spec.items() if k not in roles}}


def _binary_view(y: pd.Series, classes: list[Any], positive_label: str) -> tuple[np.ndarray, Any, int]:
    """(0/1 labels, positive label, its column in a probability matrix)."""
    label, column = logistic.positive_column(classes, positive_label)
    return (y == label).astype(int).to_numpy(), label, column


def _baseline(y_train: pd.Series, y_test: pd.Series, task_type: str, positive_label: str) -> dict[str, Any]:
    """What a model that has learned nothing scores on this test split.

    An AUC has no absolute meaning: 0.72 is strong on some problems and
    worthless on others, and 0.95 accuracy is a failing grade at 5% prevalence.
    Reporting the floor next to the number costs nothing -- the no-skill model
    predicts the training class prior for every row -- and spares the caller
    from needing a field's conventions to read the result.
    """
    classes = sorted(np.unique(y_train).tolist())
    prior = y_train.value_counts(normalize=True)
    proba = np.tile(np.array([prior.get(c, 0.0) for c in classes], dtype=float), (len(y_test), 1))
    label = "always predicts the training class prior"
    if task_type == "binary":
        y_bin, _, column = _binary_view(y_test, classes, positive_label)
        bundle = scoring.binary_bundle(y_bin, proba[:, column], 0.5)
        keep = {"roc_auc", "average_precision", "accuracy", "balanced_accuracy", "brier_score"}
    else:
        bundle = scoring.multiclass_bundle(y_test.to_numpy(), proba, classes)
        keep = {"roc_auc_ovr", "accuracy", "balanced_accuracy", "log_loss"}
    return {"strategy": label, **{k: v for k, v in bundle.items() if k in keep}}


def _score_holdout(
    pipe: Any,
    prep: _Prepared,
    positive_label: str,
    threshold_rule: str,
    oof_proba: np.ndarray | None,
    classes: list[Any],
    include_curves: bool = False,
) -> dict[str, Any]:
    """Apply *pipe* to the held-out split and bundle every metric it supports."""
    split = prep.split
    proba = np.asarray(pipe.predict_proba(split.x_test), dtype=float)
    position = {cls: j for j, cls in enumerate(pipe.named_steps["clf"].classes_)}
    aligned = np.column_stack([
        proba[:, position[cls]] if cls in position else np.zeros(len(split.x_test)) for cls in classes
    ])

    if prep.task_type != "binary":
        return {
            "test_metrics": scoring.multiclass_bundle(split.y_test.to_numpy(), aligned, classes),
            "threshold": None,
        }

    y_bin, label, column = _binary_view(split.y_test, classes, positive_label)
    # The operating point is chosen on TRAIN-side out-of-fold predictions and
    # merely applied here. Choosing it on the test split would be the exact
    # leak this server is built to make impossible.
    if oof_proba is None:
        threshold, how = logistic.resolve_threshold(threshold_rule, np.zeros(0), np.zeros(0))
    else:
        y_train_bin, _, _ = _binary_view(split.y_train, classes, positive_label)
        threshold, how = logistic.resolve_threshold(threshold_rule, y_train_bin, oof_proba[:, column])
    # Rounded before scoring, not after, so the cutoff reported in `threshold`
    # is the one the confusion matrix was actually built at.
    threshold = round(float(threshold), 4)
    bundle = scoring.binary_bundle(y_bin, aligned[:, column], threshold)
    bundle["positive_label"] = str(label)
    return {
        "test_metrics": bundle,
        "test_curves": scoring.binary_curves(y_bin, aligned[:, column], full=include_curves),
        "threshold": {
            "value": threshold,
            "rule": how,
            "selected_on": "a fixed value" if how == "fixed" else "training out-of-fold predictions",
        },
    }


# --------------------------------------------------------------------------
# Orientation
# --------------------------------------------------------------------------


@mcp.tool()
@_guarded
def describe_dataset(data_path: str, target_column: str = "", max_columns: int = 100) -> str:
    """Inspect a dataset and get suggestions for how to model it. START HERE.

    Answers the questions that come before any fit: what is in this file, which
    column is plausibly the outcome, is it binary or multiclass, how balanced
    is it, and is there an id or date column that would make a random split
    leak. Suggestions are name/cardinality heuristics labelled with their
    evidence -- they are for the caller to confirm, not to apply blindly.

    Args:
        data_path: Path or URI to the dataset (.csv/.parquet/.json/.jsonl).
        target_column: Optional. When given, adds a task-type inference, the
            class balance, and the caveats that balance implies for AUC.
        max_columns: Cap on how many columns to describe.
    """
    frame = load_frame(data_path)
    columns, truncated = profile.column_report(frame, max_columns)
    payload: dict[str, Any] = {
        "n_rows": int(len(frame)),
        "n_columns": int(frame.shape[1]),
        "columns": columns,
        "truncated_columns": truncated,
        "suggestions": profile.suggestions(frame, columns),
        "data_sha256": frame_sha256(frame),
    }
    if target_column:
        if target_column not in frame.columns:
            cols = ", ".join(map(str, frame.columns[:25]))
            return _error(f"target column {target_column!r} not in dataset; columns are: {cols}")
        payload["target"] = profile.target_summary(frame[target_column], target_column)
    return scoring.dumps(payload)


@mcp.tool()
@_guarded
def describe_split(
    data_path: str,
    target_column: str,
    split_json: str = "",
    test_size: float = 0.2,
    random_seed: int = 42,
    stratify: bool = True,
) -> str:
    """Preview and audit a train/test split without fitting anything.

    Worth one call before a long fit, and the only way to see the leakage audit
    on its own: row counts and class balance per side, which columns were
    excluded from the features as bookkeeping, whether any group value or
    duplicate feature row appears on both sides, and the ``split_sha256`` that
    identifies this exact division of this exact file.

    ``split_json`` is a JSON object accepted by every modeling tool here:

      * ``{"strategy": "random"}`` -- default; stratified row-wise sampling.
      * ``{"strategy": "group", "group_column": "patient_id"}`` -- keeps every
        row of an entity on one side. Use whenever rows repeat per subject,
        site or session; a random split there inflates the AUC.
      * ``{"strategy": "time", "time_column": "visit_date"}`` -- trains on the
        past, tests on the strictly later rows.
      * ``{"strategy": "predefined", "split_column": "split"}`` or
        ``{"strategy": "predefined", "test_path": "test.csv"}`` -- use a split
        somebody else already decided.

    Args:
        data_path: Path or URI to the dataset (.csv/.parquet/.json/.jsonl).
        target_column: Column to predict.
        split_json: Split spec as above; overrides the three arguments below.
        test_size: Held-out fraction, strictly between 0 and 1.
        random_seed: Seed for the split.
        stratify: Keep the class balance equal across sides (classification).
    """
    prep = _prepare(data_path, target_column, "auto", split_json, test_size, random_seed, stratify)
    return scoring.dumps(
        {
            "split": {**prep.spec.as_dict(), **prep.audit},
            "task_type": prep.task_type,
            "feature_columns": [str(c) for c in prep.split.x_train.columns],
            "data_sha256": prep.data_sha256,
            "split_sha256": splitting.spec_sha256(prep.spec, prep.data_sha256),
        }
    )


# --------------------------------------------------------------------------
# Logistic regression
# --------------------------------------------------------------------------


@mcp.tool()
@_guarded
def fit_logistic_regression(
    data_path: str,
    target_column: str,
    task_type: str = "auto",
    positive_label: str = "",
    penalty: str = "l2",
    C: float = 1.0,  # noqa: N803 -- sklearn's name for inverse regularization strength
    solver: str = "auto",
    class_weight: str = "",
    max_iter: int = 1000,
    l1_ratio: float = 0.5,
    scale: bool = True,
    numeric_impute: str = "median",
    max_categories: int = 20,
    threshold: str = "0.5",
    split_json: str = "",
    test_size: float = 0.2,
    random_seed: int = 42,
    stratify: bool = True,
    top_k_coefficients: int = 25,
    include_curves: bool = False,
) -> str:
    """Fit a logistic regression and report ROC-AUC and its associated metrics.

    The main tool. Builds the standard pipeline -- impute missing values, scale
    the numerics, one-hot the categoricals, fit ``LogisticRegression`` -- fits it
    on the training split only, and scores it on the held-out split. No code to
    write, and no way to accidentally score on training data.

    Returns: ``test_metrics`` (ROC-AUC, PR-AUC and its prevalence baseline,
    Brier, accuracy, balanced accuracy, precision/recall/specificity, F1, MCC,
    confusion matrix), ``test_curves`` (ROC and PR points, a calibration
    profile, and a threshold sweep with the Youden-J and best-F1 operating
    points), ``coefficients`` with odds ratios, a ``baseline`` showing what a
    no-skill model scores on the same split, the ``preprocessing`` decisions
    actually applied, and the audited ``split``.

    Non-numeric columns with more than ``max_categories`` levels are dropped
    rather than one-hot encoded, and reported under ``preprocessing`` -- an id
    or free-text column would otherwise expand into thousands of columns that
    fit the training data perfectly and predict nothing.

    Args:
        data_path: Path or URI to the dataset (.csv/.parquet/.json/.jsonl).
        target_column: Column to predict; every other non-bookkeeping column is a feature.
        task_type: 'auto' (infer from the target), 'binary' or 'multiclass'.
        positive_label: Binary positive class; defaults to the highest label.
        penalty: 'l2' (default), 'l1', 'elasticnet' or 'none'.
        C: Inverse regularization strength; smaller means more shrinkage.
        solver: 'auto' picks one compatible with the penalty; else lbfgs/liblinear/saga/etc.
        class_weight: '' or 'balanced' -- reweight classes by inverse frequency.
        max_iter: Solver iteration cap; raise it if convergence is reported as a warning.
        l1_ratio: Elasticnet mix, 0 (pure l2) to 1 (pure l1). Ignored otherwise.
        scale: Standardize numeric features. Keep on for any penalized fit.
        numeric_impute: 'median' (default), 'mean' or 'most_frequent'.
        max_categories: Cardinality ceiling above which a categorical column is dropped.
        threshold: '0.5', a number in (0, 1), or 'youden'/'f1' to tune the cutoff on
            TRAINING out-of-fold predictions. Never tuned on the test split.
        split_json: Split spec -- see describe_split for grouped/temporal/predefined.
        test_size: Held-out fraction, strictly between 0 and 1.
        random_seed: Seed for the split and the solver.
        stratify: Keep the class balance equal across the split.
        top_k_coefficients: How many coefficients to report, largest magnitude first.
        include_curves: Add ROC/PR points, calibration bins and a 19-step threshold
            sweep to the result. Off by default because they roughly quintuple its
            size; switch on when you're plotting or exporting, not just reading.
            The best Youden-J and F1 operating points come back either way.
    """
    prep = _prepare(data_path, target_column, task_type, split_json, test_size, random_seed, stratify)
    split = prep.split
    factory, model_spec = logistic.make_pipeline_factory(
        split.x_train,
        penalty=penalty,
        C=C,
        solver=solver,
        class_weight=class_weight,
        max_iter=max_iter,
        l1_ratio=l1_ratio,
        scale=scale,
        numeric_impute=numeric_impute,
        max_categories=max_categories,
        random_seed=random_seed,
    )

    pipe = factory()
    pipe.fit(split.x_train, split.y_train)
    classes = sorted(np.unique(split.y_train).tolist())

    # Out-of-fold training predictions, computed only when a tuned threshold
    # actually needs them -- k extra fits are not worth paying for a fixed 0.5.
    oof = None
    if prep.task_type == "binary" and threshold.strip().lower() in {"youden", "f1"}:
        n_splits = logistic.usable_n_splits(split.y_train, 5, split.groups_train)
        folds = splitting.fold_indices(
            split.x_train, split.y_train, prep.spec, n_splits,
            groups=split.groups_train, classification=True,
        )
        oof, _, _ = logistic.out_of_fold_proba(factory, split.x_train, split.y_train, folds)

    scored = _score_holdout(pipe, prep, positive_label, threshold, oof, classes, include_curves)
    return scoring.dumps(
        {
            **scored,
            "coefficients": logistic.coefficients(pipe, scaled=scale, top_k=top_k_coefficients),
            "baseline": _baseline(split.y_train, split.y_test, prep.task_type, positive_label),
            "model": _model_block(model_spec, "LogisticRegression"),
            "preprocessing": {
                "numeric_columns": model_spec["numeric"],
                "categorical_columns_one_hot": model_spec["categorical"],
                "dropped_high_cardinality": model_spec["dropped_high_cardinality"],
                "n_encoded_features": int(pipe.named_steps["pre"].transform(split.x_train.head(1)).shape[1]),
            },
            "convergence": _convergence(pipe),
            **prep.provenance(target_column, data_path),
        }
    )


def _convergence(pipe: Any) -> dict[str, Any]:
    """Whether the solver actually converged -- a silent non-convergence is a wrong model."""
    clf = pipe.named_steps["clf"]
    iters = np.atleast_1d(np.asarray(getattr(clf, "n_iter_", []), dtype=float))
    if iters.size == 0:
        return {"converged": None}
    hit_cap = bool(np.any(iters >= clf.max_iter))
    return {
        "converged": not hit_cap,
        "n_iter": [int(i) for i in iters],
        "max_iter": int(clf.max_iter),
        **({"warning": "the solver hit max_iter without converging -- raise max_iter or scale the features"}
           if hit_cap else {}),
    }


@mcp.tool()
@_guarded
def cross_validate_logistic_regression(
    data_path: str,
    target_column: str,
    task_type: str = "auto",
    positive_label: str = "",
    n_splits: int = 5,
    penalty: str = "l2",
    C: float = 1.0,  # noqa: N803
    solver: str = "auto",
    class_weight: str = "",
    max_iter: int = 1000,
    l1_ratio: float = 0.5,
    scale: bool = True,
    numeric_impute: str = "median",
    max_categories: int = 20,
    split_json: str = "",
    random_seed: int = 42,
    stratify: bool = True,
    include_curves: bool = False,
) -> str:
    """Cross-validate a logistic regression: AUC with an error bar, over the whole dataset.

    A single 80/20 split yields one AUC and no sense of how much of it is luck;
    on a few hundred rows two seeds can differ by 0.05. This refits per fold
    over every row and reports per-fold scores, their mean and standard
    deviation, and the pooled out-of-fold metrics (every row predicted by a
    model that never saw it) -- which is the number to report when the question
    is "how good is this model", as distinct from ``fit_logistic_regression``'s
    "how did this model do on that holdout".

    Grouping is honored: a ``group_column`` in ``split_json`` makes the folds
    grouped too, so the CV estimate isn't leak-inflated. ``n_splits`` is clamped
    down when the rarest class or the group count can't support it, and the
    realized value is reported.

    Args:
        data_path: Path or URI to the dataset (.csv/.parquet/.json/.jsonl).
        target_column: Column to predict.
        task_type: 'auto', 'binary' or 'multiclass'.
        positive_label: Binary positive class; defaults to the highest label.
        n_splits: Requested number of folds (clamped to what the data supports).
        penalty: 'l2', 'l1', 'elasticnet' or 'none'.
        C: Inverse regularization strength.
        solver: 'auto', or one compatible with the penalty.
        class_weight: '' or 'balanced'.
        max_iter: Solver iteration cap.
        l1_ratio: Elasticnet mix, 0 to 1.
        scale: Standardize numeric features.
        numeric_impute: 'median', 'mean' or 'most_frequent'.
        max_categories: Cardinality ceiling for one-hot encoding.
        split_json: Only ``group_column``/``stratify``/``random_seed`` matter here --
            CV uses every row, so ``test_size`` and the holdout strategies don't apply.
        random_seed: Seed for the folds and the solver.
        stratify: Keep the class balance equal across folds.
        include_curves: Add pooled out-of-fold ROC/PR points, calibration bins and a
            threshold sweep. Off by default -- see fit_logistic_regression.
    """
    spec = splitting.parse_spec(split_json, random_seed=random_seed, stratify=stratify)
    frame, spec = splitting.load_for_spec(data_path, spec)
    if target_column not in frame.columns:
        cols = ", ".join(map(str, frame.columns[:25]))
        return _error(f"target column {target_column!r} not in dataset; columns are: {cols}")

    resolved = profile.infer_task_type(frame[target_column]) if (task_type or "auto").lower() == "auto" else task_type
    if resolved not in _CLASSIFICATION:
        return _error(
            f"target {target_column!r} is {resolved!r}, not a classification target. "
            "Logistic regression needs discrete classes."
        )

    reserved = splitting.reserved_columns(spec, target_column)
    features = frame[[c for c in frame.columns if c not in reserved]]
    y = frame[target_column]
    groups = frame[spec.group_column] if spec.group_column and spec.group_column in frame.columns else None

    factory, model_spec = logistic.make_pipeline_factory(
        features, penalty=penalty, C=C, solver=solver, class_weight=class_weight, max_iter=max_iter,
        l1_ratio=l1_ratio, scale=scale, numeric_impute=numeric_impute, max_categories=max_categories,
        random_seed=random_seed,
    )
    used_splits = logistic.usable_n_splits(y, n_splits, groups)
    folds = splitting.fold_indices(features, y, spec, used_splits, groups=groups, classification=True)
    oof, classes, _ = logistic.out_of_fold_proba(factory, features, y, folds)

    if resolved == "binary":
        y_bin, label, column = _binary_view(y, classes, positive_label)
        per_fold = [scoring.binary_bundle(y_bin[te], oof[te, column], 0.5) for _, te in folds]
        keys = ("roc_auc", "average_precision", "brier_score", "accuracy", "balanced_accuracy", "f1")
        pooled = scoring.binary_bundle(y_bin, oof[:, column], 0.5)
        pooled["positive_label"] = str(label)
        pooled_curves: dict[str, Any] = scoring.binary_curves(y_bin, oof[:, column], full=include_curves)
    else:
        y_values = y.to_numpy()
        per_fold = [scoring.multiclass_bundle(y_values[te], oof[te], classes) for _, te in folds]
        keys = ("roc_auc_ovr", "accuracy", "balanced_accuracy", "f1_macro", "log_loss")
        pooled = scoring.multiclass_bundle(y_values, oof, classes)
        pooled_curves = {}

    data_sha256 = frame_sha256(frame)
    return scoring.dumps(
        {
            "cv_metrics": {k: scoring.summarize_folds([f.get(k) for f in per_fold]) for k in keys},
            "per_fold": [
                {"fold": i, "n": int(len(te)), **{k: f.get(k) for k in keys}}
                for i, ((_, te), f) in enumerate(zip(folds, per_fold, strict=True))
            ],
            # Pooled != mean-of-folds: one AUC over all out-of-fold predictions
            # at once, which is less noisy but hides between-fold variation.
            # Both are reported because they answer different questions.
            "pooled_out_of_fold_metrics": pooled,
            **({"pooled_out_of_fold_curves": pooled_curves} if pooled_curves else {}),
            "n_splits_requested": int(n_splits),
            "n_splits_used": int(used_splits),
            "grouped_folds": groups is not None,
            "model": _model_block(model_spec, "LogisticRegression"),
            "preprocessing": {
                "numeric_columns": model_spec["numeric"],
                "categorical_columns_one_hot": model_spec["categorical"],
                "dropped_high_cardinality": model_spec["dropped_high_cardinality"],
            },
            "task_type": resolved,
            "target_column": target_column,
            "data_path": data_path,
            "n_rows": int(len(frame)),
            "class_distribution": {str(k): int(v) for k, v in y.value_counts().items()},
            "data_sha256": data_sha256,
            "package_versions": scoring.env_provenance(),
        }
    )


@mcp.tool()
@_guarded
def tune_logistic_regression(
    data_path: str,
    target_column: str,
    grid_json: str = "",
    task_type: str = "auto",
    positive_label: str = "",
    selection_metric: str = "roc_auc",
    n_splits: int = 5,
    threshold: str = "0.5",
    max_categories: int = 20,
    split_json: str = "",
    test_size: float = 0.2,
    random_seed: int = 42,
    stratify: bool = True,
    top_k_coefficients: int = 25,
    include_curves: bool = False,
) -> str:
    """Search hyperparameters by CV on the training split, then score the winner on the holdout.

    The honest version of "try a few settings and report the best number".
    Every candidate is scored by cross-validation *inside the training split*;
    only the winner is refit on the full training split and applied to the
    held-out data, exactly once. Picking a winner on the test split and then
    reporting that same split's AUC -- the usual way this goes wrong -- is not
    reachable through this tool.

    The default grid varies ``C`` over four orders of magnitude and toggles
    ``class_weight``, which covers most of what is available on a clean tabular
    file. Supply ``grid_json`` to search something else, e.g.
    ``{"C": [0.1, 1, 10], "penalty": ["l1", "l2"], "solver": ["liblinear"]}``.

    Args:
        data_path: Path or URI to the dataset (.csv/.parquet/.json/.jsonl).
        target_column: Column to predict.
        grid_json: JSON object mapping any of C/penalty/solver/class_weight/l1_ratio/
            scale/numeric_impute/max_iter to a list of values. Empty uses the default grid.
        task_type: 'auto', 'binary' or 'multiclass'.
        positive_label: Binary positive class; defaults to the highest label.
        selection_metric: What CV maximizes -- 'roc_auc' (default),
            'average_precision' (better under heavy imbalance), 'balanced_accuracy' or 'f1'.
        n_splits: Inner CV folds (clamped to what the training split supports).
        threshold: '0.5', a number, or 'youden'/'f1' tuned on the winner's out-of-fold
            training predictions.
        max_categories: Cardinality ceiling for one-hot encoding.
        split_json: Split spec -- see describe_split.
        test_size: Held-out fraction.
        random_seed: Seed for the split, the folds and the solver.
        stratify: Keep the class balance equal across the split and folds.
        top_k_coefficients: How many coefficients of the winner to report.
        include_curves: Add the winner's holdout ROC/PR points, calibration bins and a
            threshold sweep. Off by default -- see fit_logistic_regression.
    """
    prep = _prepare(data_path, target_column, task_type, split_json, test_size, random_seed, stratify)
    split = prep.split

    grid = logistic.DEFAULT_GRID
    if grid_json.strip():
        try:
            grid = json.loads(grid_json)
        except json.JSONDecodeError as e:
            return _error(f"grid_json is not valid JSON: {e}")
        if not isinstance(grid, dict):
            return _error(f"grid_json must be a JSON object, got {type(grid).__name__}")
    candidates = logistic.expand_grid(grid)

    metric_key = {
        "roc_auc": "roc_auc" if prep.task_type == "binary" else "roc_auc_ovr",
        "average_precision": "average_precision",
        "balanced_accuracy": "balanced_accuracy",
        "f1": "f1" if prep.task_type == "binary" else "f1_macro",
    }.get(selection_metric)
    if metric_key is None:
        return _error(
            f"selection_metric must be one of ['roc_auc', 'average_precision', 'balanced_accuracy', 'f1'], "
            f"got {selection_metric!r}"
        )
    if selection_metric == "average_precision" and prep.task_type != "binary":
        return _error("selection_metric 'average_precision' applies to binary targets only")

    used_splits = logistic.usable_n_splits(split.y_train, n_splits, split.groups_train)
    folds = splitting.fold_indices(
        split.x_train, split.y_train, prep.spec, used_splits,
        groups=split.groups_train, classification=True,
    )
    classes = sorted(np.unique(split.y_train).tolist())

    results = []
    for candidate in candidates:
        factory, model_spec = logistic.make_pipeline_factory(
            split.x_train, max_categories=max_categories, random_seed=random_seed, **candidate
        )
        oof, _, _ = logistic.out_of_fold_proba(factory, split.x_train, split.y_train, folds)
        if prep.task_type == "binary":
            y_bin, _, column = _binary_view(split.y_train, classes, positive_label)
            fold_scores = [scoring.binary_bundle(y_bin[te], oof[te, column], 0.5).get(metric_key) for _, te in folds]
        else:
            y_values = split.y_train.to_numpy()
            fold_scores = [scoring.multiclass_bundle(y_values[te], oof[te], classes).get(metric_key) for _, te in folds]
        summary = scoring.summarize_folds(fold_scores)
        results.append({"params": dict(candidate), "cv": summary, "_spec": model_spec, "_oof": oof})

    scored = [r for r in results if r["cv"]["mean"] is not None]
    if not scored:
        return _error(f"every candidate scored None on {selection_metric!r} -- the folds may be single-class")
    # Ties broken toward the *more* regularized model (smaller C): when two
    # settings are indistinguishable in CV, the simpler one generalizes better.
    best = min(scored, key=lambda r: (-r["cv"]["mean"], r["params"].get("C", 1.0)))

    factory, model_spec = logistic.make_pipeline_factory(
        split.x_train, max_categories=max_categories, random_seed=random_seed, **best["params"]
    )
    pipe = factory()
    pipe.fit(split.x_train, split.y_train)
    holdout = _score_holdout(pipe, prep, positive_label, threshold, best["_oof"], classes, include_curves)

    return scoring.dumps(
        {
            **holdout,
            "best_params": best["params"],
            "best_cv": {selection_metric: best["cv"]},
            "search": {
                "selection_metric": selection_metric,
                "n_candidates": len(candidates),
                "n_splits_used": int(used_splits),
                "scored_on": "cross-validation within the training split only",
                "leaderboard": sorted(
                    ({"params": r["params"], selection_metric: r["cv"]["mean"], "std": r["cv"]["std"]} for r in scored),
                    key=lambda r: -(r[selection_metric] or 0),
                ),
            },
            "coefficients": logistic.coefficients(
                pipe, scaled=bool(best["params"].get("scale", True)), top_k=top_k_coefficients
            ),
            "baseline": _baseline(split.y_train, split.y_test, prep.task_type, positive_label),
            "model": _model_block(model_spec, "LogisticRegression"),
            "preprocessing": {
                "numeric_columns": model_spec["numeric"],
                "categorical_columns_one_hot": model_spec["categorical"],
                "dropped_high_cardinality": model_spec["dropped_high_cardinality"],
            },
            "convergence": _convergence(pipe),
            **prep.provenance(target_column, data_path),
        }
    )


# --------------------------------------------------------------------------
# Random forest
# --------------------------------------------------------------------------


def _forest_preprocessing(model_spec: dict[str, Any], pipe: Any = None, features: pd.DataFrame | None = None) -> dict[str, Any]:
    """The preprocessing block, with the reason there's no scaling step in it."""
    block = {
        "numeric_columns_imputed": model_spec["numeric"],
        "categorical_columns_one_hot": model_spec["categorical"],
        "dropped_high_cardinality": model_spec["dropped_high_cardinality"],
        "scaling": "not applied -- a tree splits on thresholds, so rescaling a feature changes nothing",
    }
    if pipe is not None and features is not None:
        block["n_encoded_features"] = int(pipe.named_steps["pre"].transform(features.head(1)).shape[1])
    return block


def _permutation_scorer(task_type: str) -> str:
    return "roc_auc" if task_type == "binary" else "roc_auc_ovr"


@mcp.tool()
@_guarded
def fit_random_forest(
    data_path: str,
    target_column: str,
    task_type: str = "auto",
    positive_label: str = "",
    n_estimators: int = 300,
    criterion: str = "gini",
    max_depth: int = 0,
    min_samples_split: int = 2,
    min_samples_leaf: int = 1,
    max_features: str = "sqrt",
    bootstrap: bool = True,
    class_weight: str = "",
    numeric_impute: str = "median",
    max_categories: int = 20,
    threshold: str = "0.5",
    split_json: str = "",
    test_size: float = 0.2,
    random_seed: int = 42,
    stratify: bool = True,
    top_k_features: int = 25,
    permutation_importance: bool = False,
    include_curves: bool = False,
) -> str:
    """Fit a random forest and report ROC-AUC and its associated metrics.

    The nonlinear counterpart to ``fit_logistic_regression``, and the one to
    reach for when the outcome plausibly depends on interactions or thresholds
    rather than a weighted sum -- it needs no feature engineering to find them.
    Same contract: builds the pipeline (impute, one-hot the categoricals, fit
    ``RandomForestClassifier``), fits on the training split only, scores on the
    held-out split. Run both and compare; if the forest doesn't beat the logistic
    fit, the readable model is the one to report.

    Numerics are imputed but NOT scaled -- a tree is invariant to rescaling, so
    there is no ``scale`` argument here.

    Returns the same ``test_metrics``/``test_curves``/``baseline``/``split`` blocks
    as the logistic tool, plus ``feature_importances`` in place of coefficients
    and, when ``bootstrap`` is on, an ``out_of_bag`` block: every training row
    scored by the trees that didn't draw it, which is a second held-out estimate
    over all n rows for free. A large gap between the OOB and holdout AUCs is
    worth reading as a split that isn't representative.

    Args:
        data_path: Path or URI to the dataset (.csv/.parquet/.json/.jsonl).
        target_column: Column to predict; every other non-bookkeeping column is a feature.
        task_type: 'auto' (infer from the target), 'binary' or 'multiclass'.
        positive_label: Binary positive class; defaults to the highest label.
        n_estimators: Number of trees. More is monotonically better and slower --
            never a source of overfitting; drop it only to make a search finish.
        criterion: Split quality -- 'gini' (default), 'entropy' or 'log_loss'.
        max_depth: 0 (default) grows every tree until its leaves are pure; a positive
            depth caps them, which is the blunt way to fight overfitting.
        min_samples_split: Minimum rows in a node before it may split.
        min_samples_leaf: Minimum rows in a leaf. Raise it (5, 10, 25) on noisy or
            small data -- the gentler, usually better regularizer.
        max_features: Features considered per split: 'sqrt' (default), 'log2', 'all',
            a fraction in (0, 1], or a whole-number count. Lower decorrelates the trees.
        bootstrap: Sample rows with replacement per tree. Off means every tree sees
            every row, which loses both the variance reduction and the OOB estimate.
        class_weight: '', 'balanced', or 'balanced_subsample' (reweighted per bootstrap).
        numeric_impute: 'median' (default), 'mean' or 'most_frequent'.
        max_categories: Cardinality ceiling above which a categorical column is dropped.
        threshold: '0.5', a number in (0, 1), or 'youden'/'f1' to tune the cutoff on
            TRAINING out-of-fold predictions. Never tuned on the test split.
        split_json: Split spec -- see describe_split for grouped/temporal/predefined.
        test_size: Held-out fraction, strictly between 0 and 1.
        random_seed: Seed for the split and the forest.
        stratify: Keep the class balance equal across the split.
        top_k_features: How many features to report, most important first.
        permutation_importance: Also measure importance by shuffling each raw column
            on the held-out split. Slower, and the number to trust when the impurity
            ranking is doing the talking -- impurity importance flatters continuous
            and high-cardinality columns.
        include_curves: Add ROC/PR points, calibration bins and a 19-step threshold
            sweep. Off by default -- see fit_logistic_regression.
    """
    prep = _prepare(data_path, target_column, task_type, split_json, test_size, random_seed, stratify)
    split = prep.split
    factory, model_spec = forest.make_pipeline_factory(
        split.x_train,
        n_estimators=n_estimators,
        criterion=criterion,
        max_depth=max_depth,
        min_samples_split=min_samples_split,
        min_samples_leaf=min_samples_leaf,
        max_features=max_features,
        bootstrap=bootstrap,
        class_weight=class_weight,
        numeric_impute=numeric_impute,
        max_categories=max_categories,
        random_seed=random_seed,
    )

    pipe = factory()
    pipe.fit(split.x_train, split.y_train)
    classes = sorted(np.unique(split.y_train).tolist())

    # Out-of-fold training predictions, only when a tuned threshold needs them.
    # The forest's own OOB predictions are also out-of-sample and would be free,
    # but they can be missing for a row no tree left out -- k explicit folds give
    # every row a prediction, which is what a threshold sweep assumes.
    oof = None
    if prep.task_type == "binary" and threshold.strip().lower() in {"youden", "f1"}:
        n_splits = forest.usable_n_splits(split.y_train, 5, split.groups_train)
        folds = splitting.fold_indices(
            split.x_train, split.y_train, prep.spec, n_splits,
            groups=split.groups_train, classification=True,
        )
        oof, _, _ = forest.out_of_fold_proba(factory, split.x_train, split.y_train, folds)

    scored = _score_holdout(pipe, prep, positive_label, threshold, oof, classes, include_curves)
    out_of_bag = forest.oob_metrics(pipe, split.y_train, classes, prep.task_type, positive_label)
    importances: dict[str, Any] = {"impurity": forest.importances(pipe, top_k=top_k_features)}
    if permutation_importance:
        importances["permutation"] = forest.permutation_terms(
            pipe, split.x_test, split.y_test,
            scorer=_permutation_scorer(prep.task_type), top_k=top_k_features, random_seed=random_seed,
        )

    return scoring.dumps(
        {
            **scored,
            "feature_importances": importances,
            **({"out_of_bag": out_of_bag} if out_of_bag else {}),
            "baseline": _baseline(split.y_train, split.y_test, prep.task_type, positive_label),
            "model": _model_block(model_spec, "RandomForestClassifier"),
            "preprocessing": _forest_preprocessing(model_spec, pipe, split.x_train),
            **prep.provenance(target_column, data_path),
        }
    )


@mcp.tool()
@_guarded
def cross_validate_random_forest(
    data_path: str,
    target_column: str,
    task_type: str = "auto",
    positive_label: str = "",
    n_splits: int = 5,
    n_estimators: int = 300,
    criterion: str = "gini",
    max_depth: int = 0,
    min_samples_split: int = 2,
    min_samples_leaf: int = 1,
    max_features: str = "sqrt",
    bootstrap: bool = True,
    class_weight: str = "",
    numeric_impute: str = "median",
    max_categories: int = 20,
    split_json: str = "",
    random_seed: int = 42,
    stratify: bool = True,
    include_curves: bool = False,
) -> str:
    """Cross-validate a random forest: AUC with an error bar, over the whole dataset.

    Exactly ``cross_validate_logistic_regression``'s contract with a forest in
    place of the linear model -- per-fold scores, their mean and standard
    deviation, and the pooled out-of-fold metrics -- and the fair way to compare
    the two families, since a single holdout can flatter either one by a few
    hundredths. Grouping is honored; ``n_splits`` is clamped to what the rarest
    class and the group count support, and the realized value reported.

    Note this refits the whole forest per fold, so it costs ``n_splits`` times
    one fit. ``fit_random_forest``'s ``out_of_bag`` block is the cheap
    approximation when that's too slow.

    Args:
        data_path: Path or URI to the dataset (.csv/.parquet/.json/.jsonl).
        target_column: Column to predict.
        task_type: 'auto', 'binary' or 'multiclass'.
        positive_label: Binary positive class; defaults to the highest label.
        n_splits: Requested number of folds (clamped to what the data supports).
        n_estimators: Number of trees per fold.
        criterion: 'gini', 'entropy' or 'log_loss'.
        max_depth: 0 for unlimited, else the depth cap.
        min_samples_split: Minimum rows in a node before it may split.
        min_samples_leaf: Minimum rows in a leaf.
        max_features: 'sqrt', 'log2', 'all', a fraction in (0, 1], or a count.
        bootstrap: Sample rows with replacement per tree.
        class_weight: '', 'balanced' or 'balanced_subsample'.
        numeric_impute: 'median', 'mean' or 'most_frequent'.
        max_categories: Cardinality ceiling for one-hot encoding.
        split_json: Only ``group_column``/``stratify``/``random_seed`` matter here --
            CV uses every row, so ``test_size`` and the holdout strategies don't apply.
        random_seed: Seed for the folds and the forest.
        stratify: Keep the class balance equal across folds.
        include_curves: Add pooled out-of-fold ROC/PR points, calibration bins and a
            threshold sweep. Off by default -- see fit_logistic_regression.
    """
    spec = splitting.parse_spec(split_json, random_seed=random_seed, stratify=stratify)
    frame, spec = splitting.load_for_spec(data_path, spec)
    if target_column not in frame.columns:
        cols = ", ".join(map(str, frame.columns[:25]))
        return _error(f"target column {target_column!r} not in dataset; columns are: {cols}")

    resolved = profile.infer_task_type(frame[target_column]) if (task_type or "auto").lower() == "auto" else task_type
    if resolved not in _CLASSIFICATION:
        return _error(
            f"target {target_column!r} is {resolved!r}, not a classification target. "
            "RandomForestClassifier needs discrete classes."
        )

    reserved = splitting.reserved_columns(spec, target_column)
    features = frame[[c for c in frame.columns if c not in reserved]]
    y = frame[target_column]
    groups = frame[spec.group_column] if spec.group_column and spec.group_column in frame.columns else None

    factory, model_spec = forest.make_pipeline_factory(
        features, n_estimators=n_estimators, criterion=criterion, max_depth=max_depth,
        min_samples_split=min_samples_split, min_samples_leaf=min_samples_leaf, max_features=max_features,
        bootstrap=bootstrap, class_weight=class_weight, numeric_impute=numeric_impute,
        max_categories=max_categories, random_seed=random_seed,
    )
    used_splits = forest.usable_n_splits(y, n_splits, groups)
    folds = splitting.fold_indices(features, y, spec, used_splits, groups=groups, classification=True)
    oof, classes, _ = forest.out_of_fold_proba(factory, features, y, folds)

    if resolved == "binary":
        y_bin, label, column = _binary_view(y, classes, positive_label)
        per_fold = [scoring.binary_bundle(y_bin[te], oof[te, column], 0.5) for _, te in folds]
        keys = ("roc_auc", "average_precision", "brier_score", "accuracy", "balanced_accuracy", "f1")
        pooled = scoring.binary_bundle(y_bin, oof[:, column], 0.5)
        pooled["positive_label"] = str(label)
        pooled_curves: dict[str, Any] = scoring.binary_curves(y_bin, oof[:, column], full=include_curves)
    else:
        y_values = y.to_numpy()
        per_fold = [scoring.multiclass_bundle(y_values[te], oof[te], classes) for _, te in folds]
        keys = ("roc_auc_ovr", "accuracy", "balanced_accuracy", "f1_macro", "log_loss")
        pooled = scoring.multiclass_bundle(y_values, oof, classes)
        pooled_curves = {}

    return scoring.dumps(
        {
            "cv_metrics": {k: scoring.summarize_folds([f.get(k) for f in per_fold]) for k in keys},
            "per_fold": [
                {"fold": i, "n": int(len(te)), **{k: f.get(k) for k in keys}}
                for i, ((_, te), f) in enumerate(zip(folds, per_fold, strict=True))
            ],
            "pooled_out_of_fold_metrics": pooled,
            **({"pooled_out_of_fold_curves": pooled_curves} if pooled_curves else {}),
            "n_splits_requested": int(n_splits),
            "n_splits_used": int(used_splits),
            "grouped_folds": groups is not None,
            "model": _model_block(model_spec, "RandomForestClassifier"),
            "preprocessing": _forest_preprocessing(model_spec),
            "task_type": resolved,
            "target_column": target_column,
            "data_path": data_path,
            "n_rows": int(len(frame)),
            "class_distribution": {str(k): int(v) for k, v in y.value_counts().items()},
            "data_sha256": frame_sha256(frame),
            "package_versions": scoring.env_provenance(),
        }
    )


@mcp.tool()
@_guarded
def tune_random_forest(
    data_path: str,
    target_column: str,
    grid_json: str = "",
    task_type: str = "auto",
    positive_label: str = "",
    selection_metric: str = "roc_auc",
    n_splits: int = 5,
    n_estimators: int = 300,
    threshold: str = "0.5",
    max_categories: int = 20,
    split_json: str = "",
    test_size: float = 0.2,
    random_seed: int = 42,
    stratify: bool = True,
    top_k_features: int = 25,
    include_curves: bool = False,
) -> str:
    """Search forest hyperparameters by CV on the training split, then score the winner.

    ``tune_logistic_regression``'s guarantee, applied to a forest: every
    candidate is scored by cross-validation *inside the training split*, and only
    the winner touches the held-out data, exactly once.

    The default grid varies ``max_features`` and ``min_samples_leaf`` and toggles
    ``class_weight`` -- how decorrelated the trees are and how far they may
    memorize, which is where a forest's achievable AUC actually lives. It does
    not search ``n_estimators``: more trees never overfit, so that's a compute
    knob (its own argument here), not a candidate. Supply ``grid_json`` to search
    something else, e.g. ``{"min_samples_leaf": [1, 5, 20], "max_depth": [0, 6]}``.

    Budget: candidates x folds full forest fits. Keep the grid and
    ``n_estimators`` modest while exploring, then confirm the winner with
    ``fit_random_forest`` at a larger tree count.

    Args:
        data_path: Path or URI to the dataset (.csv/.parquet/.json/.jsonl).
        target_column: Column to predict.
        grid_json: JSON object mapping any of n_estimators/criterion/max_depth/
            min_samples_split/min_samples_leaf/max_features/bootstrap/class_weight/
            numeric_impute to a list of values. Empty uses the default grid.
        task_type: 'auto', 'binary' or 'multiclass'.
        positive_label: Binary positive class; defaults to the highest label.
        selection_metric: What CV maximizes -- 'roc_auc' (default),
            'average_precision' (better under heavy imbalance), 'balanced_accuracy' or 'f1'.
        n_splits: Inner CV folds (clamped to what the training split supports).
        n_estimators: Trees per candidate, unless the grid overrides it.
        threshold: '0.5', a number, or 'youden'/'f1' tuned on the winner's out-of-fold
            training predictions.
        max_categories: Cardinality ceiling for one-hot encoding.
        split_json: Split spec -- see describe_split.
        test_size: Held-out fraction.
        random_seed: Seed for the split, the folds and the forest.
        stratify: Keep the class balance equal across the split and folds.
        top_k_features: How many of the winner's feature importances to report.
        include_curves: Add the winner's holdout ROC/PR points, calibration bins and a
            threshold sweep. Off by default -- see fit_logistic_regression.
    """
    prep = _prepare(data_path, target_column, task_type, split_json, test_size, random_seed, stratify)
    split = prep.split

    grid = forest.DEFAULT_GRID
    if grid_json.strip():
        try:
            grid = json.loads(grid_json)
        except json.JSONDecodeError as e:
            return _error(f"grid_json is not valid JSON: {e}")
        if not isinstance(grid, dict):
            return _error(f"grid_json must be a JSON object, got {type(grid).__name__}")
    candidates = forest.expand_grid(grid, forest.GRID_KEYS)

    metric_key = {
        "roc_auc": "roc_auc" if prep.task_type == "binary" else "roc_auc_ovr",
        "average_precision": "average_precision",
        "balanced_accuracy": "balanced_accuracy",
        "f1": "f1" if prep.task_type == "binary" else "f1_macro",
    }.get(selection_metric)
    if metric_key is None:
        return _error(
            f"selection_metric must be one of ['roc_auc', 'average_precision', 'balanced_accuracy', 'f1'], "
            f"got {selection_metric!r}"
        )
    if selection_metric == "average_precision" and prep.task_type != "binary":
        return _error("selection_metric 'average_precision' applies to binary targets only")

    used_splits = forest.usable_n_splits(split.y_train, n_splits, split.groups_train)
    folds = splitting.fold_indices(
        split.x_train, split.y_train, prep.spec, used_splits,
        groups=split.groups_train, classification=True,
    )
    classes = sorted(np.unique(split.y_train).tolist())
    defaults = {"n_estimators": n_estimators, "max_categories": max_categories, "random_seed": random_seed}

    results = []
    for candidate in candidates:
        factory, _ = forest.make_pipeline_factory(split.x_train, **{**defaults, **candidate})
        oof, _, _ = forest.out_of_fold_proba(factory, split.x_train, split.y_train, folds)
        if prep.task_type == "binary":
            y_bin, _, column = _binary_view(split.y_train, classes, positive_label)
            fold_scores = [scoring.binary_bundle(y_bin[te], oof[te, column], 0.5).get(metric_key) for _, te in folds]
        else:
            y_values = split.y_train.to_numpy()
            fold_scores = [scoring.multiclass_bundle(y_values[te], oof[te], classes).get(metric_key) for _, te in folds]
        results.append({"params": dict(candidate), "cv": scoring.summarize_folds(fold_scores), "_oof": oof})

    scored = [r for r in results if r["cv"]["mean"] is not None]
    if not scored:
        return _error(f"every candidate scored None on {selection_metric!r} -- the folds may be single-class")
    # Ties broken toward the *more* constrained forest (bigger leaves): when two
    # settings are indistinguishable in CV, the one that memorizes less wins.
    best = min(scored, key=lambda r: (-r["cv"]["mean"], -r["params"].get("min_samples_leaf", 1)))

    factory, model_spec = forest.make_pipeline_factory(split.x_train, **{**defaults, **best["params"]})
    pipe = factory()
    pipe.fit(split.x_train, split.y_train)
    holdout = _score_holdout(pipe, prep, positive_label, threshold, best["_oof"], classes, include_curves)
    out_of_bag = forest.oob_metrics(pipe, split.y_train, classes, prep.task_type, positive_label)

    return scoring.dumps(
        {
            **holdout,
            "best_params": best["params"],
            "best_cv": {selection_metric: best["cv"]},
            "search": {
                "selection_metric": selection_metric,
                "n_candidates": len(candidates),
                "n_splits_used": int(used_splits),
                "scored_on": "cross-validation within the training split only",
                "leaderboard": sorted(
                    ({"params": r["params"], selection_metric: r["cv"]["mean"], "std": r["cv"]["std"]} for r in scored),
                    key=lambda r: -(r[selection_metric] or 0),
                ),
            },
            "feature_importances": {"impurity": forest.importances(pipe, top_k=top_k_features)},
            **({"out_of_bag": out_of_bag} if out_of_bag else {}),
            "baseline": _baseline(split.y_train, split.y_test, prep.task_type, positive_label),
            "model": _model_block(model_spec, "RandomForestClassifier"),
            "preprocessing": _forest_preprocessing(model_spec),
            **prep.provenance(target_column, data_path),
        }
    )


# --------------------------------------------------------------------------
# Script execution -- the escape hatch
# --------------------------------------------------------------------------


def _parse_payload(payload_json: str) -> tuple[dict[str, Any] | None, str]:
    """Parse the optional hyperparameter payload; returns (parsed, sha256)."""
    if not payload_json:
        return None, ""
    digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    parsed = json.loads(payload_json)  # JSONDecodeError handled by the caller
    if not isinstance(parsed, dict):
        raise TypeError(f"payload_json must be a JSON object, got {type(parsed).__name__}")
    return parsed, digest


def _run_script(
    *,
    code: str,
    data_path: str,
    target_column: str,
    task_type: str,
    positive_label: str,
    test_size: float,
    random_seed: int,
    payload_json: str,
    extra_names: dict[str, Any],
    family: str,
    include_curves: bool = False,
) -> str:
    """Shared body of both script tools: load, split, exec on train, score on test."""
    code_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()
    base = {"model_family": family, "code_sha256": code_sha256}

    if task_type not in _TASK_TYPES:
        return scoring.dumps({**base, "error": f"task_type must be one of {sorted(_TASK_TYPES)}, got {task_type!r}"})
    if not 0.0 < test_size < 1.0:
        return scoring.dumps({**base, "error": f"test_size must be strictly between 0 and 1, got {test_size}"})

    try:
        hp, payload_sha256 = _parse_payload(payload_json)
    except (json.JSONDecodeError, TypeError) as e:
        return scoring.dumps({**base, "error": f"payload_json: {e}"})
    base["payload_sha256"] = payload_sha256

    try:
        frame = load_frame(data_path)
        X_train, y_train, X_test, y_test = split_xy(  # noqa: N806
            frame, target_column, test_size, random_seed, stratify=task_type in _CLASSIFICATION
        )
    except DataError as e:
        return scoring.dumps({**base, "error": f"dataset: {e}"})

    # Execute with the TRAIN split only in scope -- X_test/y_test are never bound.
    namespace: dict[str, Any] = {
        "pd": pd,
        "np": np,
        "random_seed": random_seed,
        "hp": hp,
        "X_train": X_train,
        "y_train": y_train,
        "result": None,
        "chosen_threshold": None,
        "predict": None,
        "predict_proba": None,
        **extra_names,
    }
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            exec(code, namespace)  # noqa: S102 -- executing caller-supplied code is this tool's purpose
    except Exception as e:  # noqa: BLE001
        return scoring.dumps(
            {
                **base,
                "error": f"Script execution failed: {type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-_ERR_CHARS:],
                "stdout": stdout.getvalue()[-_ERR_CHARS:],
                "executed_code": code,
            }
        )

    wanted = "predict_proba" if task_type in _CLASSIFICATION else "predict"
    fn = namespace.get(wanted)
    if not callable(fn):
        return scoring.dumps(
            {
                **base,
                "error": (
                    f"Script must define a callable `{wanted}(X)` for "
                    f"task_type={task_type!r} (missing or not callable)."
                ),
                "stdout": stdout.getvalue()[-_ERR_CHARS:],
                "executed_code": code,
            }
        )

    try:
        test_metrics, curves = _score_script(
            fn, task_type, positive_label, y_train, X_test, y_test, namespace, include_curves
        )
    except Exception as e:  # noqa: BLE001
        return scoring.dumps(
            {
                **base,
                "error": f"Test scoring failed: {type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-_ERR_CHARS:],
                "stdout": stdout.getvalue()[-_ERR_CHARS:],
                "executed_code": code,
            }
        )

    decisions = namespace.get("result")
    return scoring.dumps(
        {
            **base,
            "task_type": task_type,
            "test_metrics": test_metrics,
            **({"test_curves": curves} if curves else {}),
            "model_decisions": decisions if isinstance(decisions, dict) else {},
            "stdout": stdout.getvalue()[-_STDOUT_CHARS:],
            "executed_code": code,
            "n_train": int(len(X_train)),
            "n_test": int(len(X_test)),
            "n_features": int(X_train.shape[1]),
            "feature_names": [str(c) for c in X_train.columns],
            "target_column": target_column,
            "data_path": data_path,
            "data_sha256": frame_sha256(frame),
            "package_versions": scoring.env_provenance(),
        }
    )


def _score_script(
    fn: Any,
    task_type: str,
    positive_label: str,
    y_train: pd.Series,
    X_test: pd.DataFrame,  # noqa: N803
    y_test: pd.Series,
    namespace: dict[str, Any],
    include_curves: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply the script's callable to the held-out split and bundle its metrics."""
    if task_type == "regression":
        return scoring.regression_bundle(y_test.to_numpy(), np.asarray(fn(X_test), dtype=float)), {}

    # Class labels come from TRAIN, so the ordering a script's predict_proba
    # columns must follow is knowable from what the script itself was given.
    classes = sorted(np.unique(y_train).tolist())
    if task_type == "binary":
        pos = type(classes[0])(positive_label) if positive_label != "" else classes[-1]
        y_bin = (y_test == pos).astype(int).to_numpy()
        proba = np.asarray(fn(X_test), dtype=float).ravel()
        chosen = namespace.get("chosen_threshold")
        bundle = scoring.binary_bundle(y_bin, proba, 0.5 if chosen is None else float(chosen))
        bundle["positive_label"] = str(pos)
        return bundle, scoring.binary_curves(y_bin, proba, full=include_curves)
    return scoring.multiclass_bundle(y_test.to_numpy(), np.asarray(fn(X_test), dtype=float), classes), {}


@mcp.tool()
def run_logistic_regression_script(
    code: str,
    data_path: str,
    target_column: str,
    task_type: str = "binary",
    positive_label: str = "",
    test_size: float = 0.2,
    random_seed: int = 42,
    payload_json: str = "",
    include_curves: bool = False,
) -> str:
    """Fit a model with your own script, then score it on a held-out split.

    The escape hatch for what ``fit_logistic_regression``'s arguments can't
    express -- a custom ColumnTransformer, an interaction basis, a calibrated
    or stacked estimator, a different classifier entirely. Prefer the
    declarative tool when it covers the case: it handles preprocessing,
    threshold selection and the split audit for you.

    It is also the ONLY route to a regression target: every declarative tool
    here is a classifier, so ``task_type='regression'`` plus a ``predict(X)``
    is how you fit and score one (import Ridge, LinearRegression or whatever
    else you need -- they aren't pre-bound).

    Your code runs with the TRAINING split only in scope. It must define a
    top-level callable capturing the fitted model:

      * task_type 'binary'      -> ``predict_proba(X)`` returning 1-D P(positive)
      * task_type 'multiclass'  -> ``predict_proba(X)`` returning 2-D class
        probabilities in ascending class-label order
      * task_type 'regression'  -> ``predict(X)`` returning 1-D predictions

    THIS tool then applies the test split and computes every metric, so the
    script can never see the test labels. Pre-bound names: ``X_train``,
    ``y_train``, ``LogisticRegression``, ``LogisticRegressionCV``, ``Pipeline``,
    ``make_pipeline``, ``ColumnTransformer``, ``StandardScaler``,
    ``OneHotEncoder``, ``SimpleImputer``, ``pd``, ``np``, ``random_seed``, ``hp``
    (the parsed payload). Any installed package may be imported. Set
    ``chosen_threshold`` to pick the binary operating point (default 0.5), and a
    ``result`` dict of train-side decisions to echo back (it must not contain
    test metrics).

    Args:
        code: Python source defining predict_proba(X) or predict(X) as above.
        data_path: Path or URI to the dataset (.csv/.parquet/.json/.jsonl).
        target_column: Column to predict; every other column is a feature.
        task_type: 'binary', 'multiclass', or 'regression'.
        positive_label: Binary positive-class label; defaults to the highest class.
        test_size: Held-out fraction, strictly between 0 and 1.
        random_seed: Seed for the split, and bound into the script.
        payload_json: Optional JSON object of hyperparameters, bound as ``hp``.
        include_curves: Add ROC/PR points, calibration bins and a threshold sweep.
            Off by default -- see fit_logistic_regression.
    """
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression, LogisticRegressionCV
    from sklearn.pipeline import Pipeline, make_pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    return _run_script(
        code=code,
        data_path=data_path,
        target_column=target_column,
        task_type=task_type,
        positive_label=positive_label,
        test_size=test_size,
        random_seed=random_seed,
        payload_json=payload_json,
        include_curves=include_curves,
        extra_names={
            "LogisticRegression": LogisticRegression,
            "LogisticRegressionCV": LogisticRegressionCV,
            "Pipeline": Pipeline,
            "make_pipeline": make_pipeline,
            "ColumnTransformer": ColumnTransformer,
            "StandardScaler": StandardScaler,
            "OneHotEncoder": OneHotEncoder,
            "SimpleImputer": SimpleImputer,
        },
        # Not a flat "logistic_regression": with the linear-regression script
        # tool gone this is also the regression route, and a run whose target
        # was continuous is not a logistic fit however it got here. The script
        # decides the estimator, so task_type is the most honest label
        # available.
        family="script" if task_type == "regression" else "logistic_regression",
    )


@mcp.tool()
def ping() -> str:
    """Health check -- returns 'pong' to verify the server is running."""
    return "pong"
