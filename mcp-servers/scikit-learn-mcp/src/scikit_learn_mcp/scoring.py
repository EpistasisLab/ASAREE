"""Server-side metric bundles for the held-out test split.

Scoring lives here, not in the submitted script, and that separation is the
point of the whole server: the script is handed the training matrices only, so
it cannot compute (or quietly optimize against) a test metric even if it tries.
Every number a caller gets back was produced by this module against labels the
script never saw.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
from sklearn import metrics

# Curves are read by a model, so they are thinned to a budget rather than
# returned at one point per test row -- a 20k-row split would otherwise spend
# more context on the ROC curve than on every other metric combined.
_CURVE_POINTS = 100
_SWEEP = np.round(np.arange(0.05, 1.0, 0.05), 2)


def np_default(obj: Any) -> Any:
    """``json.dumps(default=...)`` hook for numpy scalars/arrays."""
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return str(obj)


def dumps(payload: dict[str, Any]) -> str:
    """Every tool's single exit point -- numpy-safe JSON."""
    return json.dumps(payload, default=np_default)


def finite(value: float) -> float | None:
    """NaN/inf -> None, so the JSON stays valid rather than emitting bare NaN."""
    value = float(value)
    return value if np.isfinite(value) else None


def odds_ratio(coef: float) -> float | None:
    """exp(coef), clipped so a huge separable-data coefficient doesn't overflow."""
    return finite(np.exp(np.clip(float(coef), -700.0, 700.0)))


def regression_bundle(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    """R^2, RMSE, MAE, MAPE and residual spread on the test split."""
    y_true = np.asarray(y_true, dtype=float).ravel()
    y_pred = np.asarray(y_pred, dtype=float).ravel()
    residuals = y_true - y_pred
    # MAPE is undefined at y == 0; reported only over the nonzero rows, with
    # the count so a caller can see how much of the split it covers.
    nonzero = y_true != 0
    mape = (
        finite(np.mean(np.abs(residuals[nonzero] / y_true[nonzero])) * 100.0)
        if nonzero.any()
        else None
    )
    return {
        "r2": finite(metrics.r2_score(y_true, y_pred)),
        "rmse": finite(float(np.sqrt(metrics.mean_squared_error(y_true, y_pred)))),
        "mae": finite(metrics.mean_absolute_error(y_true, y_pred)),
        "mape_percent": mape,
        "mape_n_rows": int(nonzero.sum()),
        "residual_mean": finite(float(np.mean(residuals))),
        "residual_std": finite(float(np.std(residuals))),
        "y_true_mean": finite(float(np.mean(y_true))),
        "y_pred_mean": finite(float(np.mean(y_pred))),
    }


def binary_bundle(y_true: np.ndarray, proba: np.ndarray, threshold: float) -> dict[str, Any]:
    """Threshold-free (ROC-AUC, PR-AUC, Brier) plus thresholded metrics."""
    y_true = np.asarray(y_true, dtype=int).ravel()
    proba = np.asarray(proba, dtype=float).ravel()
    pred = (proba >= threshold).astype(int)
    both_classes = len(np.unique(y_true)) > 1
    tn, fp, fn, tp = metrics.confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    prevalence = float(np.mean(y_true)) if len(y_true) else 0.0
    return {
        # AUCs are undefined when the test split is single-class -- None rather
        # than a raised error, since the rest of the bundle is still meaningful.
        "roc_auc": finite(metrics.roc_auc_score(y_true, proba)) if both_classes else None,
        "average_precision": finite(metrics.average_precision_score(y_true, proba)) if both_classes else None,
        # PR-AUC's floor is the positive rate, not 0.5 -- without it an AP of
        # 0.30 reads as bad when the prevalence is 0.02 and it is excellent.
        "average_precision_baseline": finite(prevalence),
        "brier_score": finite(metrics.brier_score_loss(y_true, proba)),
        "accuracy": finite(metrics.accuracy_score(y_true, pred)),
        "balanced_accuracy": finite(metrics.balanced_accuracy_score(y_true, pred)),
        "precision": finite(metrics.precision_score(y_true, pred, zero_division=0)),
        "recall": finite(metrics.recall_score(y_true, pred, zero_division=0)),
        "specificity": finite(tn / (tn + fp)) if (tn + fp) else None,
        "f1": finite(metrics.f1_score(y_true, pred, zero_division=0)),
        "mcc": finite(metrics.matthews_corrcoef(y_true, pred)),
        "threshold": float(threshold),
        "n_rows": int(len(y_true)),
        "n_positive": int(y_true.sum()),
        "positive_rate": finite(prevalence),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def multiclass_bundle(y_true: np.ndarray, proba: np.ndarray, classes: list[Any]) -> dict[str, Any]:
    """Macro/weighted F1 and one-vs-rest ROC-AUC over *classes* (ascending)."""
    y_true = np.asarray(y_true).ravel()
    proba = np.asarray(proba, dtype=float)
    # Let numpy infer the label dtype rather than forcing object: an
    # object-dtype prediction array makes sklearn classify the target as
    # "unknown" and refuse every metric below.
    pred = np.asarray(classes)[proba.argmax(axis=1)]
    try:
        roc_ovr = finite(metrics.roc_auc_score(y_true, proba, multi_class="ovr", labels=classes))
        roc_ovr_weighted = finite(
            metrics.roc_auc_score(y_true, proba, multi_class="ovr", average="weighted", labels=classes)
        )
    except ValueError:
        # Raised when the test split doesn't contain every class -- expected on
        # small or imbalanced data, not an error worth failing the whole call.
        roc_ovr = roc_ovr_weighted = None
    return {
        "accuracy": finite(metrics.accuracy_score(y_true, pred)),
        "balanced_accuracy": finite(metrics.balanced_accuracy_score(y_true, pred)),
        "f1_macro": finite(metrics.f1_score(y_true, pred, average="macro", zero_division=0)),
        "f1_weighted": finite(metrics.f1_score(y_true, pred, average="weighted", zero_division=0)),
        "roc_auc_ovr": roc_ovr,
        "roc_auc_ovr_weighted": roc_ovr_weighted,
        "log_loss": _safe_log_loss(y_true, proba, classes),
        "n_rows": int(len(y_true)),
        "classes": list(classes),
        "class_distribution": {str(c): int(np.sum(y_true == c)) for c in classes},
        "confusion_matrix": metrics.confusion_matrix(y_true, pred, labels=classes).tolist(),
        "per_class": [
            {
                "class": str(c),
                "precision": finite(p),
                "recall": finite(r),
                "f1": finite(f),
                "support": int(s),
            }
            for c, p, r, f, s in zip(
                classes,
                *metrics.precision_recall_fscore_support(y_true, pred, labels=classes, zero_division=0),
                strict=False,
            )
        ],
    }


def _safe_log_loss(y_true: np.ndarray, proba: np.ndarray, classes: list[Any]) -> float | None:
    try:
        return finite(metrics.log_loss(y_true, proba, labels=classes))
    except ValueError:
        return None


def _thin(*arrays: np.ndarray, budget: int = _CURVE_POINTS) -> list[list[float]]:
    """Subsample parallel curve arrays to at most *budget* evenly spaced points."""
    n = len(arrays[0])
    idx = np.arange(n) if n <= budget else np.unique(np.linspace(0, n - 1, budget).astype(int))
    return [[finite(v) for v in np.asarray(a, dtype=float)[idx]] for a in arrays]


def binary_curves(y_true: np.ndarray, proba: np.ndarray, *, full: bool = False) -> dict[str, Any]:
    """Alternative operating points, and -- when *full* -- the curves behind them.

    Separate from :func:`binary_bundle` because these are what you need to
    *choose* an operating point or plot the result, whereas the bundle is the
    headline scorecard at one already-chosen threshold. Returned alongside it
    so picking a threshold doesn't cost a second fit.

    Only the two best operating points come back by default. The point arrays
    are perhaps 4x the size of everything else a fit returns, and the caller is
    usually a model reading the result rather than a plotting library: it can
    act on "Youden-J sits at 0.61" but has no use for 100 (fpr, tpr) pairs. Ask
    for *full* when the numbers are actually going to be drawn or exported.
    """
    y_true = np.asarray(y_true, dtype=int).ravel()
    proba = np.asarray(proba, dtype=float).ravel()
    if len(np.unique(y_true)) < 2:
        return {"note": "single-class split -- ROC/PR curves are undefined"}

    out: dict[str, Any] = {
        "best_thresholds": {
            "youden_j": _operating_point(y_true, proba, best_threshold(y_true, proba, "youden")),
            "f1": _operating_point(y_true, proba, best_threshold(y_true, proba, "f1")),
        }
    }
    if not full:
        out["note"] = "pass include_curves=true for ROC/PR points, calibration bins and a threshold sweep"
        return out

    fpr, tpr, roc_thr = metrics.roc_curve(y_true, proba)
    precision, recall, _ = metrics.precision_recall_curve(y_true, proba)
    t_fpr, t_tpr, t_thr = _thin(fpr, tpr, np.clip(roc_thr, 0.0, 1.0))
    t_prec, t_rec = _thin(precision, recall)
    out["roc"] = {"fpr": t_fpr, "tpr": t_tpr, "thresholds": t_thr}
    out["precision_recall"] = {"precision": t_prec, "recall": t_rec}
    out["calibration"] = _calibration(y_true, proba)
    out["threshold_sweep"] = [_operating_point(y_true, proba, t) for t in _SWEEP]
    return out


def _operating_point(y_true: np.ndarray, proba: np.ndarray, threshold: float) -> dict[str, Any]:
    pred = (proba >= threshold).astype(int)
    tn, fp, fn, tp = metrics.confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    specificity = tn / (tn + fp) if (tn + fp) else 0.0
    return {
        "threshold": round(float(threshold), 4),
        "precision": finite(tp / (tp + fp)) if (tp + fp) else 0.0,
        "recall": finite(recall),
        "specificity": finite(specificity),
        "f1": finite(metrics.f1_score(y_true, pred, zero_division=0)),
        "youden_j": finite(recall + specificity - 1.0),
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "tn": int(tn),
    }


def _calibration(y_true: np.ndarray, proba: np.ndarray, n_bins: int = 10) -> list[dict[str, Any]]:
    """Predicted vs. observed positive rate per probability decile (nonempty bins only)."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    which = np.clip(np.digitize(proba, edges[1:-1], right=False), 0, n_bins - 1)
    out = []
    for b in range(n_bins):
        rows = which == b
        if not rows.any():
            continue
        out.append(
            {
                "bin": [round(float(edges[b]), 2), round(float(edges[b + 1]), 2)],
                "n": int(rows.sum()),
                "mean_predicted": finite(float(np.mean(proba[rows]))),
                "observed_rate": finite(float(np.mean(y_true[rows]))),
            }
        )
    return out


def best_threshold(y_true: np.ndarray, proba: np.ndarray, rule: str) -> float:
    """The probability cutoff maximizing *rule* ('youden' or 'f1') on these labels.

    Callers must pass TRAINING (or out-of-fold) predictions here. Tuning a
    threshold against the test split and then reporting test precision/recall at
    it is the quiet leak this server exists to prevent, so the choice is made
    upstream of the held-out data and merely applied to it.
    """
    y_true = np.asarray(y_true, dtype=int).ravel()
    proba = np.asarray(proba, dtype=float).ravel()
    if len(np.unique(y_true)) < 2:
        return 0.5
    if rule == "youden":
        fpr, tpr, thr = metrics.roc_curve(y_true, proba)
        best = thr[int(np.argmax(tpr - fpr))]
        return float(np.clip(best, 0.0, 1.0))
    if rule == "f1":
        scores = [metrics.f1_score(y_true, (proba >= t).astype(int), zero_division=0) for t in _SWEEP]
        return float(_SWEEP[int(np.argmax(scores))])
    raise ValueError(f"unknown threshold rule {rule!r}; use 'youden', 'f1', or a number in (0, 1)")


def summarize_folds(values: list[float | None]) -> dict[str, Any]:
    """mean/std/min/max over per-fold scores, ignoring folds where it was undefined."""
    usable = np.asarray([v for v in values if v is not None], dtype=float)
    if usable.size == 0:
        return {"mean": None, "std": None, "min": None, "max": None, "n_folds": 0}
    return {
        "mean": finite(float(usable.mean())),
        # Population std over the folds, matching cross_val_score's convention:
        # these are the k folds themselves, not a sample drawn from more of them.
        "std": finite(float(usable.std())),
        "min": finite(float(usable.min())),
        "max": finite(float(usable.max())),
        "n_folds": int(usable.size),
    }


def env_provenance() -> dict[str, str]:
    """Versions of the packages whose behavior the reported numbers depend on."""
    import platform

    import pandas
    import sklearn

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pandas.__version__,
        "scikit-learn": sklearn.__version__,
    }
