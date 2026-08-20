"""Exploratory analysis computation (pure; the ``eda`` tool bucket).

Correlations, outlier detection, feature distributions, and class balance —
all read-only over the TRAIN fold, extracted from the monolith tool bodies to
take ``(X_train[, y_train])`` directly and return result dicts. The server
wrapper (#1457) resolves the matrix from the workspace HEAD via ambient context
and JSON-serializes the return. Numeric behaviour is identical to the monolith.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder

from .errors import ComputeError


def compute_correlations(
    X_train: pd.DataFrame, y_train: pd.Series, method: str = "spearman"
) -> dict[str, Any]:
    """Feature-target and high feature-feature correlations on the TRAIN fold.

    *method* is ``"spearman"`` (default) or ``"pearson"``.
    """
    from scipy import stats as scipy_stats

    X = X_train.select_dtypes(include="number")
    y = y_train

    # Encode target if categorical
    if y.dtype == object or str(y.dtype) == "category":
        y_enc = LabelEncoder().fit_transform(y)
    else:
        y_enc = y.values

    target_corrs = []
    for col in X.columns:
        vals = X[col].fillna(X[col].median())
        if method == "pearson":
            r, p = scipy_stats.pearsonr(vals, y_enc)
        else:
            r, p = scipy_stats.spearmanr(vals, y_enc)
        target_corrs.append(
            {
                "feature": col,
                "correlation": round(float(r), 4),
                "p_value": round(float(p), 6),
            }
        )

    target_corrs.sort(key=lambda x: abs(x["correlation"]), reverse=True)

    # Top feature-feature correlations
    corr_matrix = X.fillna(X.median()).corr(method=method)
    high_corr_pairs = []
    cols = list(corr_matrix.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = corr_matrix.iloc[i, j]
            if abs(r) > 0.7:
                high_corr_pairs.append(
                    {
                        "feature_a": cols[i],
                        "feature_b": cols[j],
                        "correlation": round(float(r), 4),
                    }
                )
    high_corr_pairs.sort(key=lambda x: abs(x["correlation"]), reverse=True)

    return {
        "method": method,
        "target_correlations": target_corrs,
        "high_feature_correlations": high_corr_pairs[:20],
        "note": "Correlations computed on training fold only.",
    }


def detect_outliers(
    X_train: pd.DataFrame, method: str = "iqr", threshold: float = 1.5
) -> dict[str, Any]:
    """Outlier counts per numeric TRAIN feature via IQR or IsolationForest.

    *threshold* is the IQR multiplier (``iqr``) or contamination
    (``isolation_forest``). Unknown methods raise :class:`ComputeError`.
    """
    X = X_train.select_dtypes(include="number").fillna(0)

    if method == "iqr":
        result: dict[str, Any] = {}
        for col in X.columns:
            Q1, Q3 = X[col].quantile(0.25), X[col].quantile(0.75)
            IQR = Q3 - Q1
            lower, upper = Q1 - threshold * IQR, Q3 + threshold * IQR
            mask = (X[col] < lower) | (X[col] > upper)
            result[col] = {
                "n_outliers": int(mask.sum()),
                "pct": round(float(mask.mean()) * 100, 2),
            }
        total = sum(v["n_outliers"] for v in result.values())
        # Only features WITH flagged outliers carry signal; emitting a row for
        # every clean column (often most of them) just bloats agent context.
        flagged = {k: v for k, v in result.items() if v["n_outliers"] > 0}
        return {
            "method": "iqr",
            "threshold": threshold,
            "per_feature": flagged,
            "n_features_checked": len(result),
            "n_features_with_outliers": len(flagged),
            "total_outlier_rows_approx": total,
            "note": "per_feature lists only columns with >=1 flagged outlier; "
            f"the other {len(result) - len(flagged)} numeric columns had none.",
        }

    elif method == "isolation_forest":
        from sklearn.ensemble import IsolationForest

        clf = IsolationForest(
            contamination=threshold if threshold < 0.5 else 0.1, random_state=42
        )
        preds = clf.fit_predict(X)
        n_outliers = int((preds == -1).sum())
        return {
            "method": "isolation_forest",
            "n_outliers": n_outliers,
            "pct": round(n_outliers / len(X) * 100, 2),
            "outlier_indices": list(np.where(preds == -1)[0].tolist())[:50],
        }

    raise ComputeError(f"Unknown method '{method}'. Use 'iqr' or 'isolation_forest'.")


def feature_distributions(X_train: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Per-feature distribution statistics (moments/cardinality) on the TRAIN fold.

    Includes ``n_missing`` (raw null count) for every column — the missingness
    signal the DC agent reads.
    """
    X = X_train
    result: dict[str, dict[str, Any]] = {}
    for col in X.columns:
        s = X[col]
        if pd.api.types.is_numeric_dtype(s):
            result[col] = {
                "type": "numeric",
                "mean": round(float(s.mean()), 4),
                "std": round(float(s.std()), 4),
                "min": round(float(s.min()), 4),
                "max": round(float(s.max()), 4),
                "skewness": round(float(s.skew()), 4),
                "kurtosis": round(float(s.kurtosis()), 4),
                "n_missing": int(s.isnull().sum()),
            }
        else:
            result[col] = {
                "type": "categorical",
                "cardinality": int(s.nunique()),
                "top_5": {str(k): int(v) for k, v in s.value_counts().head(5).items()},
                "n_missing": int(s.isnull().sum()),
            }
    return result


def column_missingness(X_train: pd.DataFrame) -> dict[str, Any]:
    """Per-column missingness (null count + fraction) on the TRAIN fold.

    The focused missingness signal the DC agent reads to apply its step-4
    threshold. Unlike :func:`feature_distributions` this computes ONLY nulls, so
    it is cheap and dtype-agnostic. Columns are sorted by descending missingness;
    ``pct_missing`` is a fraction in ``[0, 1]``.
    """
    n_rows = int(len(X_train))
    columns: list[dict[str, Any]] = []
    for col in X_train.columns:
        n_missing = int(X_train[col].isnull().sum())
        columns.append(
            {
                "feature": str(col),
                "n_missing": n_missing,
                "pct_missing": round(n_missing / n_rows, 6) if n_rows else 0.0,
            }
        )
    columns.sort(key=lambda c: c["pct_missing"], reverse=True)
    return {"n_rows": n_rows, "n_features": len(columns), "columns": columns}


def dataset_info(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    n_test: int,
    feature_names: list[str],
) -> dict[str, Any]:
    """Descriptive per-column statistics + class distribution for the TRAIN fold.

    Mirrors the monolith ``get_dataset_info`` payload minus the ``dataset_id``
    envelope (the caller adds identity). Numeric columns get quantiles/skew;
    categorical get cardinality + top values.
    """
    X = X_train
    y = y_train
    numeric_cols = X.select_dtypes(include="number").columns.tolist()
    cat_cols = X.select_dtypes(exclude="number").columns.tolist()

    stats: dict[str, Any] = {}
    for col in numeric_cols:
        s = X[col]
        stats[col] = {
            "type": "numeric",
            "mean": round(float(s.mean()), 4),
            "std": round(float(s.std()), 4),
            "min": round(float(s.min()), 4),
            "25%": round(float(s.quantile(0.25)), 4),
            "50%": round(float(s.median()), 4),
            "75%": round(float(s.quantile(0.75)), 4),
            "max": round(float(s.max()), 4),
            "missing": int(s.isnull().sum()),
            "skewness": round(float(s.skew()), 4),
        }
    for col in cat_cols:
        s = X[col]
        stats[col] = {
            "type": "categorical",
            "cardinality": int(s.nunique()),
            "top_values": s.value_counts().head(5).to_dict(),
            "missing": int(s.isnull().sum()),
        }

    class_dist = y.value_counts()
    imbalance_ratio = (
        float(class_dist.max() / class_dist.min()) if len(class_dist) > 1 else 1.0
    )

    return {
        "n_train": len(X),
        "n_test": n_test,
        "n_features": len(feature_names),
        "numeric_features": numeric_cols,
        "categorical_features": cat_cols,
        "target_distribution": {str(k): int(v) for k, v in class_dist.items()},
        "imbalance_ratio": round(imbalance_ratio, 2),
        "smote_recommended": imbalance_ratio > 3.0,
        "feature_stats": stats,
    }


def check_class_balance(y_train: pd.Series) -> dict[str, Any]:
    """Class counts/proportions, imbalance ratio, and a SMOTE recommendation."""
    y = y_train
    counts = y.value_counts()
    ratio = float(counts.max() / counts.min()) if len(counts) > 1 else 1.0

    recommendation = "balanced"
    if ratio > 10:
        recommendation = "severe_imbalance_use_smote_or_class_weight"
    elif ratio > 3:
        recommendation = "moderate_imbalance_consider_smote_or_class_weight"

    return {
        "class_counts": {str(k): int(v) for k, v in counts.items()},
        "class_proportions": {
            str(k): round(float(v / len(y)), 4) for k, v in counts.items()
        },
        "imbalance_ratio": round(ratio, 2),
        "recommendation": recommendation,
    }
