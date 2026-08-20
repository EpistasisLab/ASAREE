"""Model scoring + CV harness (pure; the ``run_model_script`` scoring core).

Extracted from the monolith: the held-out-test metric bundles, threshold-free
permutation importance, the algorithm factory, and the stratified-CV training
harness. All operate on in-memory arrays/frames handed in by the caller — no
session, no workspace, no exec of agent code (that orchestration stays in the
server wrapper). Leakage safety is the caller's contract: fit on TRAIN only,
score the returned model on the held-out split exactly once.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_validate
from sklearn.preprocessing import LabelEncoder
from sklearn.svm import SVC
from xgboost import XGBClassifier

# Algorithm factory: name -> (hyperparams, seed) -> estimator. The pinned seed
# and fixed extra kwargs (max_iter, probability, verbosity) reproduce the
# monolith's numeric behaviour exactly.
ALGORITHMS = {
    "logistic_regression": lambda hp, seed: LogisticRegression(
        max_iter=1000, random_state=seed, **hp
    ),
    "random_forest": lambda hp, seed: RandomForestClassifier(random_state=seed, **hp),
    "gradient_boosting": lambda hp, seed: GradientBoostingClassifier(
        random_state=seed, **hp
    ),
    "svm": lambda hp, seed: SVC(probability=True, random_state=seed, **hp),
    "xgboost": lambda hp, seed: XGBClassifier(random_state=seed, verbosity=0, **hp),
}


def roc_auc(
    y_true: Any, proba: Any, task_type: str, classes: list[Any]
) -> float | None:
    """ROC-AUC used as the permutation-importance baseline metric."""
    from sklearn.metrics import roc_auc_score

    try:
        if task_type == "binary":
            return float(roc_auc_score(y_true, proba))
        return float(
            roc_auc_score(
                y_true, proba, multi_class="ovr", average="weighted", labels=classes
            )
        )
    except Exception:  # noqa: BLE001
        return None


def avg_precision(
    y_true: Any, proba: Any, task_type: str, classes: list[Any]
) -> float | None:
    """Average precision (PR-AUC) used as a permutation-importance baseline metric.

    Threshold-free like ROC-AUC, but reflects minority-class ranking, so it aligns
    permutation importance with an ``average_precision`` selection metric.
    """
    from sklearn.metrics import average_precision_score
    from sklearn.preprocessing import label_binarize

    try:
        if task_type == "binary":
            return float(average_precision_score(y_true, proba))
        oh = label_binarize(y_true, classes=classes)
        return float(average_precision_score(oh, proba, average="weighted"))
    except Exception:  # noqa: BLE001
        return None


def binary_bundle(y_true: Any, proba: Any, threshold: float) -> dict[str, Any]:
    """Full binary test-metric bundle from positive-class probabilities."""
    from sklearn.metrics import (
        accuracy_score,
        average_precision_score,
        balanced_accuracy_score,
        brier_score_loss,
        confusion_matrix,
        precision_recall_fscore_support,
        roc_auc_score,
    )

    y_true = np.asarray(y_true).astype(int)
    proba = np.asarray(proba, dtype=float).ravel()
    prevalence = float(y_true.mean())

    def at(t: float) -> dict[str, Any]:
        pred = (proba >= t).astype(int)
        precision, recall, f1, _ = precision_recall_fscore_support(
            y_true, pred, average="binary", zero_division=0
        )
        return {
            "threshold": round(float(t), 4),
            "accuracy": round(float(accuracy_score(y_true, pred)), 4),
            "balanced_accuracy": round(float(balanced_accuracy_score(y_true, pred)), 4),
            "precision": round(float(precision), 4),
            "recall": round(float(recall), 4),
            "f1": round(float(f1), 4),
            # Positive-prediction count: F1/precision are undefined (0/0) iff this is 0.
            # zero_division=0 above reports 0.0 there; consumers apply the prespecified
            # undefined-metric rule using this signal rather than trusting the 0.0.
            "n_pred_pos": int(pred.sum()),
            "confusion_matrix": confusion_matrix(y_true, pred).tolist(),
        }

    sweep = []
    for i in range(1, 20):
        t = i * 0.05
        p, r, f, _ = precision_recall_fscore_support(
            y_true, (proba >= t).astype(int), average="binary", zero_division=0
        )
        sweep.append(
            {
                "threshold": round(t, 2),
                "precision": round(float(p), 4),
                "recall": round(float(r), 4),
                "f1": round(float(f), 4),
            }
        )

    return {
        "roc_auc": round(float(roc_auc_score(y_true, proba)), 4),
        "average_precision": round(float(average_precision_score(y_true, proba)), 4),
        "brier_score": round(float(brier_score_loss(y_true, proba)), 4),
        "metrics_at_0.5": at(0.5),
        "metrics_at_chosen_threshold": at(threshold),
        "chosen_threshold": round(float(threshold), 4),
        # Observed test-set positive rate used as a decision cutoff: with an
        # imbalanced target a fixed 0.5 threshold under-predicts the minority
        # class, so reporting at prevalence gives a second, comparable
        # operating point alongside 0.5 (both descriptive; neither is fit on test).
        "metrics_at_prevalence": at(prevalence),
        "test_prevalence": round(prevalence, 4),
        "threshold_sweep": sweep,
        "n_test_samples": int(len(y_true)),
        "n_pos_test": int(y_true.sum()),
    }


def multiclass_bundle(y_true: Any, proba: Any, classes: list[Any]) -> dict[str, Any]:
    """Full multiclass test-metric bundle. proba columns must be in `classes` order."""
    from sklearn.metrics import (
        accuracy_score,
        average_precision_score,
        balanced_accuracy_score,
        confusion_matrix,
        f1_score,
        precision_recall_fscore_support,
        roc_auc_score,
    )

    y_true = np.asarray(y_true)
    proba = np.asarray(proba, dtype=float)
    pred = np.array(classes)[np.argmax(proba, axis=1)]

    oh = np.zeros_like(proba)
    idx = {c: i for i, c in enumerate(classes)}
    for row, yv in enumerate(y_true):
        oh[row, idx[yv]] = 1.0

    try:
        mauc: float | None = float(
            roc_auc_score(
                y_true, proba, multi_class="ovr", average="weighted", labels=classes
            )
        )
    except Exception:  # noqa: BLE001
        mauc = None

    p, r, f, sup = precision_recall_fscore_support(
        y_true, pred, labels=classes, zero_division=0
    )
    return {
        "accuracy": round(float(accuracy_score(y_true, pred)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(y_true, pred)), 4),
        "macro_f1": round(
            float(f1_score(y_true, pred, average="macro", zero_division=0)), 4
        ),
        "macro_roc_auc_ovr": round(mauc, 4) if mauc is not None else None,
        "macro_average_precision": round(
            float(average_precision_score(oh, proba, average="macro")), 4
        ),
        "multiclass_brier": round(float(((proba - oh) ** 2).sum(axis=1).mean()), 4),
        "per_class_metrics": [
            {
                "class": str(c),
                "precision": round(float(p[i]), 4),
                "recall": round(float(r[i]), 4),
                "f1": round(float(f[i]), 4),
                "support": int(sup[i]),
            }
            for i, c in enumerate(classes)
        ],
        "confusion_matrix": confusion_matrix(y_true, pred, labels=classes).tolist(),
        "n_test_samples": int(len(y_true)),
    }


def perm_importance(
    predict_proba: Any,
    X_test: Any,
    y_true: Any,
    task_type: str,
    classes: list[Any],
    random_seed: int,
    n_repeats: int = 5,
    top_k: int = 15,
    metric: str = "roc_auc",
) -> list[dict[str, Any]]:
    """Permutation importance on the test split, scored by the drop in the
    selection metric. ``average_precision`` uses PR-AUC; any other value (incl.
    threshold-dependent ones) falls back to ROC-AUC so the score stays
    threshold-free and well defined under shuffling."""
    score_fn = avg_precision if metric == "average_precision" else roc_auc
    rng = np.random.RandomState(random_seed)
    base = score_fn(y_true, predict_proba(X_test), task_type, classes)
    if base is None:
        return []
    out = []
    for col in list(X_test.columns):
        drops = []
        for _ in range(n_repeats):
            Xp = X_test.copy()
            Xp[col] = rng.permutation(Xp[col].values)
            score = score_fn(y_true, predict_proba(Xp), task_type, classes)
            if score is not None:
                drops.append(base - score)
        if drops:
            out.append(
                {
                    "feature": str(col),
                    "importance_mean": round(float(np.mean(drops)), 5),
                    "importance_std": round(float(np.std(drops)), 5),
                }
            )
    out.sort(key=lambda d: d["importance_mean"], reverse=True)
    return out[:top_k]


def cross_validate_model(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    algorithm: str,
    hyperparams: dict[str, Any],
    cv_folds: int = 5,
    random_seed: int = 42,
) -> tuple[Any, dict[str, Any], list[str]]:
    """Stratified-CV a classifier on the TRAIN fold, then fit it on all of TRAIN.

    Mirrors the monolith ``train_model`` numerics exactly: numeric-only matrix,
    NaN->0 fill, label-encode object/categorical targets, StratifiedKFold with
    shuffle, and the balanced_accuracy / roc_auc_ovr_weighted scoring pair.

    Returns ``(fitted_estimator, cv_results, feature_names)``. The caller owns
    persistence and the held-out-test scoring — this touches TRAIN only.
    """
    if algorithm not in ALGORITHMS:
        raise ValueError(
            f"Unknown algorithm '{algorithm}'. Use: {list(ALGORITHMS.keys())}"
        )

    X = X_train.copy()
    y = y_train.copy()
    feature_names = list(X.columns)

    X_arr = X.select_dtypes(include="number").fillna(0).values
    if y.dtype == object or str(y.dtype) == "category":
        y_enc = LabelEncoder().fit_transform(y)
    else:
        y_enc = y.values

    estimator = ALGORITHMS[algorithm](hyperparams, random_seed)

    cv = StratifiedKFold(n_splits=cv_folds, shuffle=True, random_state=random_seed)
    cv_res = cross_validate(
        estimator,
        X_arr,
        y_enc,
        cv=cv,
        scoring={
            "balanced_accuracy": "balanced_accuracy",
            "roc_auc": "roc_auc_ovr_weighted",
        },
        return_train_score=False,
    )

    estimator.fit(X_arr, y_enc)

    cv_results = {
        "balanced_accuracy_mean": round(
            float(cv_res["test_balanced_accuracy"].mean()), 4
        ),
        "balanced_accuracy_std": round(
            float(cv_res["test_balanced_accuracy"].std()), 4
        ),
        "roc_auc_mean": round(float(cv_res["test_roc_auc"].mean()), 4),
        "roc_auc_std": round(float(cv_res["test_roc_auc"].std()), 4),
        "per_fold_balanced_accuracy": [
            round(float(v), 4) for v in cv_res["test_balanced_accuracy"]
        ],
    }
    return estimator, cv_results, feature_names
