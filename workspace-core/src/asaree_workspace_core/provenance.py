"""Provenance + JSON-coercion helpers (pure; lifted from the monolith server).

``data_sha256`` and ``env_provenance`` are the reproducibility anchors the
statistician records (stats brief Section 12); ``np_default`` is the numpy-aware
``json.dumps`` default. None of these touch a session, a workspace, or the
network — they lift verbatim into the shared core.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pandas as pd


def data_sha256(
    X_train: pd.DataFrame,
    X_test: pd.DataFrame,
    y_train: pd.Series,
    y_test: pd.Series,
    target_name: str,
) -> str:
    """Deterministic content hash of a loaded train/test split (provenance).

    Hashes column names and per-row values of both folds plus the labels, so the
    statistician can record exactly which input data produced a result (stats
    brief Section 12). Independent of row index and of any downstream feature
    engineering, so it is constant across all replicates of a fixed split.
    """
    h = hashlib.sha256()
    h.update(target_name.encode("utf-8"))
    for label, frame in (("X_train", X_train), ("X_test", X_test)):
        h.update(label.encode("utf-8"))
        h.update(",".join(map(str, frame.columns)).encode("utf-8"))
        h.update(pd.util.hash_pandas_object(frame, index=False).values.tobytes())
    for label, series in (("y_train", y_train), ("y_test", y_test)):
        h.update(label.encode("utf-8"))
        h.update(pd.util.hash_pandas_object(series, index=False).values.tobytes())
    return h.hexdigest()


def env_provenance() -> dict[str, str | None]:
    """Versions of the packages that fit and score the model (stats brief Section 12).

    Captured in the scorer process, which is the authoritative environment for
    reproducibility — the notebook/client environment is irrelevant to the fit.
    """
    import importlib.metadata as _md

    versions: dict[str, str | None] = {}
    for dist in ("scikit-learn", "xgboost", "optuna", "numpy", "scipy", "pandas"):
        try:
            versions[dist] = _md.version(dist)
        except Exception:  # noqa: BLE001 — missing dist is reported as null, never fatal
            versions[dist] = None
    return versions


def np_default(o: Any) -> Any:
    """json.dumps default that coerces numpy scalars/arrays to native Python."""
    if hasattr(o, "item"):
        try:
            return o.item()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(o, "tolist"):
        return o.tolist()
    return str(o)
