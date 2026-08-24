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


def _finite(value: float) -> float | None:
    """NaN/inf -> None, so the JSON stays valid rather than emitting bare NaN."""
    return float(value) if np.isfinite(value) else None


def regression_bundle(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    """R^2, RMSE, MAE, MAPE and residual spread on the test split."""
    y_true = np.asarray(y_true, dtype=float).ravel()
    y_pred = np.asarray(y_pred, dtype=float).ravel()
    residuals = y_true - y_pred
    # MAPE is undefined at y == 0; reported only over the nonzero rows, with
    # the count so a caller can see how much of the split it covers.
    nonzero = y_true != 0
    mape = (
        _finite(np.mean(np.abs(residuals[nonzero] / y_true[nonzero])) * 100.0)
        if nonzero.any()
        else None
    )
    return {
        "r2": _finite(metrics.r2_score(y_true, y_pred)),
        "rmse": _finite(float(np.sqrt(metrics.mean_squared_error(y_true, y_pred)))),
        "mae": _finite(metrics.mean_absolute_error(y_true, y_pred)),
        "mape_percent": mape,
        "mape_n_rows": int(nonzero.sum()),
        "residual_mean": _finite(float(np.mean(residuals))),
        "residual_std": _finite(float(np.std(residuals))),
        "y_true_mean": _finite(float(np.mean(y_true))),
        "y_pred_mean": _finite(float(np.mean(y_pred))),
    }


def binary_bundle(y_true: np.ndarray, proba: np.ndarray, threshold: float) -> dict[str, Any]:
    """Threshold-free (ROC-AUC, PR-AUC, Brier) plus thresholded metrics."""
    y_true = np.asarray(y_true, dtype=int).ravel()
    proba = np.asarray(proba, dtype=float).ravel()
    pred = (proba >= threshold).astype(int)
    both_classes = len(np.unique(y_true)) > 1
    tn, fp, fn, tp = metrics.confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return {
        # AUCs are undefined when the test split is single-class -- None rather
        # than a raised error, since the rest of the bundle is still meaningful.
        "roc_auc": _finite(metrics.roc_auc_score(y_true, proba)) if both_classes else None,
        "average_precision": _finite(metrics.average_precision_score(y_true, proba)) if both_classes else None,
        "brier_score": _finite(metrics.brier_score_loss(y_true, proba)),
        "accuracy": _finite(metrics.accuracy_score(y_true, pred)),
        "balanced_accuracy": _finite(metrics.balanced_accuracy_score(y_true, pred)),
        "precision": _finite(metrics.precision_score(y_true, pred, zero_division=0)),
        "recall": _finite(metrics.recall_score(y_true, pred, zero_division=0)),
        "f1": _finite(metrics.f1_score(y_true, pred, zero_division=0)),
        "mcc": _finite(metrics.matthews_corrcoef(y_true, pred)),
        "threshold": float(threshold),
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
        roc_auc = _finite(metrics.roc_auc_score(y_true, proba, multi_class="ovr", labels=classes))
    except ValueError:
        # Raised when the test split doesn't contain every class -- expected on
        # small or imbalanced data, not an error worth failing the whole call.
        roc_auc = None
    return {
        "accuracy": _finite(metrics.accuracy_score(y_true, pred)),
        "balanced_accuracy": _finite(metrics.balanced_accuracy_score(y_true, pred)),
        "f1_macro": _finite(metrics.f1_score(y_true, pred, average="macro", zero_division=0)),
        "f1_weighted": _finite(metrics.f1_score(y_true, pred, average="weighted", zero_division=0)),
        "roc_auc_ovr": roc_auc,
        "classes": list(classes),
        "confusion_matrix": metrics.confusion_matrix(y_true, pred, labels=classes).tolist(),
    }


def env_provenance() -> dict[str, str]:
    """Versions of the packages whose behavior the reported numbers depend on."""
    import platform

    import pandas
    import sklearn

    versions = {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pandas.__version__,
        "scikit-learn": sklearn.__version__,
    }
    try:
        import xgboost

        versions["xgboost"] = xgboost.__version__
    except ImportError:  # pragma: no cover -- a hard dependency, defensive only
        pass
    return versions
