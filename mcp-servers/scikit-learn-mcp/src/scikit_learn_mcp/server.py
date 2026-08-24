"""scikit-learn-mcp -- a standalone XGBoost + linear-regression MCP server.

Two script-execution tools, one per model family. The caller writes the fitting
code; this server binds it the TRAINING split only, then applies the held-out
test split and computes every metric itself (see :mod:`scikit_learn_mcp.scoring`).
A script therefore cannot see or leak test labels, which makes evaluation
leakage structurally impossible rather than a rule the caller is asked to
follow.

Script-execution rather than a fixed ``fit(params)`` signature because the
useful part of a modeling decision is the preprocessing, encoding, class
weighting and hyperparameter choices *around* the estimator, and enumerating
those as tool parameters would either constrain the caller to a fraction of
what the library does or reproduce the whole sklearn API as JSON schema.

Nothing here imports ASAREE. The dataset arrives as a path or URI
(:mod:`scikit_learn_mcp.data`), so the server is usable from any MCP client
against any file.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import traceback
from typing import Any

import numpy as np
import pandas as pd
from mcp.server import FastMCP

from scikit_learn_mcp import scoring
from scikit_learn_mcp.data import DataError, frame_sha256, load_frame, split_xy

mcp = FastMCP("scikit-learn-mcp")

_CLASSIFICATION = {"binary", "multiclass"}
_TASK_TYPES = _CLASSIFICATION | {"regression"}
# Truncation budgets. A tool result is read by a model, so an unbounded
# traceback or a script that prints in a loop would otherwise cost more context
# than the metrics the call was made for.
_ERR_CHARS = 2000
_STDOUT_CHARS = 4000


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
) -> str:
    """Shared body of both tools: load, split, exec on train, score on test."""
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
        test_metrics = _score(fn, task_type, positive_label, y_train, X_test, y_test)
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


def _score(
    fn: Any,
    task_type: str,
    positive_label: str,
    y_train: pd.Series,
    X_test: pd.DataFrame,  # noqa: N803
    y_test: pd.Series,
) -> dict[str, Any]:
    """Apply the script's callable to the held-out split and bundle its metrics."""
    if task_type == "regression":
        return scoring.regression_bundle(y_test.values, np.asarray(fn(X_test), dtype=float))

    # Class labels come from TRAIN, so the ordering a script's predict_proba
    # columns must follow is knowable from what the script itself was given.
    classes = sorted(np.unique(y_train).tolist())
    if task_type == "binary":
        pos = type(classes[0])(positive_label) if positive_label != "" else classes[-1]
        y_bin = (y_test == pos).astype(int).values
        proba = np.asarray(fn(X_test), dtype=float).ravel()
        bundle = scoring.binary_bundle(y_bin, proba, 0.5)
        bundle["positive_label"] = str(pos)
        return bundle
    return scoring.multiclass_bundle(y_test.values, np.asarray(fn(X_test), dtype=float), classes)


@mcp.tool()
def run_xgboost_script(
    code: str,
    data_path: str,
    target_column: str,
    task_type: str = "binary",
    positive_label: str = "",
    test_size: float = 0.2,
    random_seed: int = 42,
    payload_json: str = "",
) -> str:
    """Fit an XGBoost model with your own script, then score it on a held-out split.

    Your code is executed with the TRAINING split only in scope. It must define a
    top-level callable capturing the fitted model:

      * task_type 'binary'      -> ``predict_proba(X)`` returning 1-D P(positive)
      * task_type 'multiclass'  -> ``predict_proba(X)`` returning 2-D class
        probabilities in ascending class-label order
      * task_type 'regression'  -> ``predict(X)`` returning 1-D predictions

    THIS tool then applies the test split and computes every metric, so the script
    can never see the test labels. Pre-bound names: ``X_train``, ``y_train``, ``xgb``
    (the xgboost module), ``XGBClassifier``, ``XGBRegressor``, ``pd``, ``np``,
    ``random_seed``, ``hp`` (the parsed payload). Any installed package may be
    imported. Optionally set a ``result`` dict of train-side decisions to echo back
    (it must not contain test metrics).

    Args:
        code: Python source defining predict_proba(X) or predict(X) as above.
        data_path: Path or URI to the dataset (.csv/.parquet/.json/.jsonl).
        target_column: Column in that file to predict; every other column is a feature.
        task_type: 'binary', 'multiclass', or 'regression'.
        positive_label: Binary positive-class label; defaults to the highest class.
        test_size: Held-out fraction, strictly between 0 and 1.
        random_seed: Seed for the split, and bound into the script.
        payload_json: Optional JSON object of hyperparameters, bound as ``hp``.
    """
    import xgboost as xgb

    return _run_script(
        code=code,
        data_path=data_path,
        target_column=target_column,
        task_type=task_type,
        positive_label=positive_label,
        test_size=test_size,
        random_seed=random_seed,
        payload_json=payload_json,
        extra_names={"xgb": xgb, "XGBClassifier": xgb.XGBClassifier, "XGBRegressor": xgb.XGBRegressor},
        family="xgboost",
    )


@mcp.tool()
def run_linear_regression_script(
    code: str,
    data_path: str,
    target_column: str,
    test_size: float = 0.2,
    random_seed: int = 42,
    payload_json: str = "",
) -> str:
    """Fit a linear regression with your own script, then score it on a held-out split.

    Regression only -- for a linear *classifier* use LogisticRegression via
    ``run_xgboost_script``'s script slot, or fit it here and report your own
    diagnostics. Your code is executed with the TRAINING split only in scope and
    must define a top-level callable ``predict(X)`` returning 1-D predictions;
    THIS tool applies the test split and computes R^2/RMSE/MAE/MAPE itself.

    Pre-bound names: ``X_train``, ``y_train``, ``LinearRegression``, ``Ridge``,
    ``Lasso``, ``ElasticNet``, ``Pipeline``, ``StandardScaler``, ``pd``, ``np``,
    ``random_seed``, ``hp`` (the parsed payload). Any installed package may be
    imported. Set a ``result`` dict to echo back train-side decisions -- coefficients
    and intercept are a good thing to put there, since this tool reports metrics
    but knows nothing about your model's internals.

    Args:
        code: Python source defining predict(X).
        data_path: Path or URI to the dataset (.csv/.parquet/.json/.jsonl).
        target_column: Numeric column to predict; every other column is a feature.
        test_size: Held-out fraction, strictly between 0 and 1.
        random_seed: Seed for the split, and bound into the script.
        payload_json: Optional JSON object of hyperparameters, bound as ``hp``.
    """
    from sklearn.linear_model import ElasticNet, Lasso, LinearRegression, Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    return _run_script(
        code=code,
        data_path=data_path,
        target_column=target_column,
        task_type="regression",
        positive_label="",
        test_size=test_size,
        random_seed=random_seed,
        payload_json=payload_json,
        extra_names={
            "LinearRegression": LinearRegression,
            "Ridge": Ridge,
            "Lasso": Lasso,
            "ElasticNet": ElasticNet,
            "Pipeline": Pipeline,
            "StandardScaler": StandardScaler,
        },
        family="linear_regression",
    )


@mcp.tool()
def describe_dataset(data_path: str, max_columns: int = 100) -> str:
    """Inspect a dataset's shape, columns and dtypes before modeling it.

    Exists so the two modeling tools' ``target_column`` argument can be chosen
    from what the file actually contains rather than guessed at.

    Args:
        data_path: Path or URI to the dataset (.csv/.parquet/.json/.jsonl).
        max_columns: Cap on how many columns to describe.
    """
    try:
        frame = load_frame(data_path)
    except DataError as e:
        return scoring.dumps({"error": f"dataset: {e}"})
    head = frame.iloc[:, :max_columns]
    return scoring.dumps(
        {
            "n_rows": int(len(frame)),
            "n_columns": int(frame.shape[1]),
            "columns": [
                {
                    "name": str(name),
                    "dtype": str(head[name].dtype),
                    "n_missing": int(head[name].isna().sum()),
                    "n_unique": int(head[name].nunique(dropna=True)),
                }
                for name in head.columns
            ],
            "truncated_columns": max(0, int(frame.shape[1]) - int(head.shape[1])),
            "data_sha256": frame_sha256(frame),
        }
    )


@mcp.tool()
def ping() -> str:
    """Health check -- returns 'pong' to verify the server is running."""
    return "pong"
