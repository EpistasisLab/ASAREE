"""Feature-selection fits (pure; the ``fs`` tool bucket).

Variance filtering, supervised univariate filters (f_classif / mutual_info), and
the multi-method selector (mutual_info / anova_f / random_forest / rfe). Every
selector scores the TRAIN fold only and returns a
:class:`~asaree_sklearn_core.artifacts.SelectorArtifact` naming the survivors; the
caller restricts both splits to those columns. Unknown methods raise
:class:`ComputeError`.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_selection import (
    RFE,
    SelectKBest,
    f_classif,
    mutual_info_classif,
)
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder

from .artifacts import PreprocessorArtifact, SelectorArtifact, new_selector_id
from .errors import ComputeError


def selection_frame(
    X_train: pd.DataFrame, preprocessor: PreprocessorArtifact | None = None
) -> tuple[pd.DataFrame, str]:
    """Training matrix for selection — encoded if a preprocessor is supplied.

    Without a preprocessor, restrict to numeric columns and median-fill (the
    space the runner reproduces on test); with one, transform through its fitted
    pipeline into the encoded space.
    """
    if preprocessor is not None:
        arr = preprocessor.pipeline.transform(X_train)
        return pd.DataFrame(arr, columns=preprocessor.feature_names_out), "encoded"
    num = X_train.select_dtypes(include="number")
    return num.fillna(num.median()), "numeric"


def _encode_target(y_train: pd.Series) -> Any:
    if y_train.dtype == object or str(y_train.dtype) == "category":
        return LabelEncoder().fit_transform(y_train)
    return y_train.to_numpy()


def variance_filter(
    X_train: pd.DataFrame,
    source_dataset_id: str,
    *,
    threshold: float = 0.0,
    preprocessor: PreprocessorArtifact | None = None,
) -> tuple[SelectorArtifact, list[dict[str, Any]], str]:
    """Drop features with variance <= *threshold* (TRAIN fold, ddof=0).

    Returns ``(selector, dropped, space)`` — the selector names the survivors,
    ``dropped`` lists each removed feature with its variance.
    """
    from sklearn.feature_selection import VarianceThreshold

    X, space = selection_frame(X_train, preprocessor)

    variances = X.var(axis=0, ddof=0)
    vt = VarianceThreshold(threshold=threshold)
    vt.fit(X.fillna(0.0))
    mask = vt.get_support()
    feature_names = list(X.columns)
    kept = [feature_names[i] for i in range(len(feature_names)) if mask[i]]
    dropped = [
        {"feature": feature_names[i], "variance": round(float(variances.iloc[i]), 6)}
        for i in range(len(feature_names))
        if not mask[i]
    ]

    selector = SelectorArtifact(
        selector_id=new_selector_id(),
        selected_features=kept,
        importances=[
            {"feature": f, "score": round(float(variances[f]), 6), "rank": r + 1}
            for r, f in enumerate(kept)
        ],
        method="variance_filter",
        source_dataset_id=source_dataset_id,
    )
    return selector, dropped, space


def supervised_filter(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    source_dataset_id: str,
    *,
    method: str = "f_classif",
    k: int = 10,
    subset: list[str] | None = None,
    preprocessor: PreprocessorArtifact | None = None,
) -> SelectorArtifact:
    """Top-k univariate supervised filter (``f_classif`` or ``mutual_info_classif``).

    *subset* optionally restricts scoring to a set of feature names (e.g. survivors
    of a prior variance/correlation pass).
    """
    X, _ = selection_frame(X_train, preprocessor)

    if subset:
        keep = [f for f in subset if f in X.columns]
        if keep:
            X = X[keep]

    if method not in ("f_classif", "mutual_info_classif"):
        raise ComputeError(
            f"Unknown method '{method}'. Use f_classif or mutual_info_classif."
        )

    y_enc = _encode_target(y_train)

    feature_names = list(X.columns)
    k_actual = max(1, min(k, len(feature_names)))
    scorer = f_classif if method == "f_classif" else mutual_info_classif
    selector = SelectKBest(scorer, k=k_actual)
    selector.fit(X.fillna(0.0), y_enc)
    scores = np.nan_to_num(selector.scores_, nan=0.0)

    ranked = list(np.argsort(scores)[::-1])
    selected = [feature_names[i] for i in ranked[:k_actual]]
    importances = [
        {
            "feature": feature_names[i],
            "score": round(float(scores[i]), 6),
            "rank": r + 1,
        }
        for r, i in enumerate(ranked)
    ]

    return SelectorArtifact(
        selector_id=new_selector_id(),
        selected_features=selected,
        importances=importances,
        method=f"supervised_{method}",
        source_dataset_id=source_dataset_id,
    )


def select_features(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    source_dataset_id: str,
    *,
    method: str = "mutual_info",
    k: int = 10,
    random_seed: int = 42,
    preprocessor: PreprocessorArtifact | None = None,
) -> SelectorArtifact:
    """Top-k selection via ``mutual_info`` / ``anova_f`` / ``random_forest`` / ``rfe``.

    Scores the TRAIN fold only. ``random_forest`` ranks by impurity importance,
    ``rfe`` by recursive elimination rank; both use *random_seed*.
    """
    if preprocessor is not None:
        X_arr = preprocessor.pipeline.transform(X_train)
        X = pd.DataFrame(X_arr, columns=preprocessor.feature_names_out)
    else:
        num = X_train.select_dtypes(include="number")
        X = num.fillna(num.median())

    y_enc = _encode_target(y_train)

    k_actual = min(k, X.shape[1])
    feature_names = list(X.columns)

    if method == "mutual_info":
        selector = SelectKBest(mutual_info_classif, k=k_actual)
        selector.fit(X, y_enc)
        scores = selector.scores_
    elif method == "anova_f":
        selector = SelectKBest(f_classif, k=k_actual)
        selector.fit(X, y_enc)
        scores = selector.scores_
    elif method == "random_forest":
        rf = RandomForestClassifier(n_estimators=100, random_state=random_seed)
        rf.fit(X, y_enc)
        scores = rf.feature_importances_
        top_idx = np.argsort(scores)[::-1][:k_actual]
        selected = [feature_names[i] for i in top_idx]
        importances = [
            {
                "feature": feature_names[i],
                "score": round(float(scores[i]), 6),
                "rank": r + 1,
            }
            for r, i in enumerate(top_idx)
        ]
        return SelectorArtifact(
            selector_id=new_selector_id(),
            selected_features=selected,
            importances=importances,
            method=method,
            source_dataset_id=source_dataset_id,
        )
    elif method == "rfe":
        est = LogisticRegression(max_iter=1000, random_state=random_seed)
        rfe = RFE(estimator=est, n_features_to_select=k_actual)
        rfe.fit(X, y_enc)
        selected = [
            feature_names[i] for i in range(len(feature_names)) if rfe.support_[i]
        ]
        importances = [
            {
                "feature": f,
                "score": round(float(-rfe.ranking_[i]), 4),
                "rank": int(rfe.ranking_[i]),
            }
            for i, f in enumerate(feature_names)
        ]
        importances.sort(key=lambda x: x["rank"])
        return SelectorArtifact(
            selector_id=new_selector_id(),
            selected_features=selected,
            importances=importances,
            method=method,
            source_dataset_id=source_dataset_id,
        )
    else:
        raise ComputeError(
            f"Unknown method '{method}'. Use: mutual_info, anova_f, random_forest, rfe."
        )

    ranked = np.argsort(scores)[::-1]
    selected = [feature_names[i] for i in ranked[:k_actual]]
    importances = [
        {
            "feature": feature_names[i],
            "score": round(float(scores[i]), 6),
            "rank": r + 1,
        }
        for r, i in enumerate(ranked)
    ]

    return SelectorArtifact(
        selector_id=new_selector_id(),
        selected_features=selected,
        importances=importances,
        method=method,
        source_dataset_id=source_dataset_id,
    )
