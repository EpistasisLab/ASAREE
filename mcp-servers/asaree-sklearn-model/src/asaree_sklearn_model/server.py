"""asaree-sklearn-model — modeling MCP server.

Thin FastMCP wrapper over :mod:`asaree_sklearn_core` exposing a single tool,
``run_model_script`` (workspace-mode only — the legacy session / artifact-key
replay paths are retired with the monolith's in-memory session).

Fit and evaluate are separated: the approved script is bound ONLY the training
matrix (read from the workspace HEAD) and must define ``predict_proba(X)``;
THIS tool then applies the held-out test split and computes every metric
server-side, so a script can never see or leak test labels.

No dependency on asaree_workspace_core — HEAD's train/test parquet already
live at a stable, permanent location once a stage is accepted (accept_stage
never moves or rewrites them again), so reading ``state.json`` directly (a
small, documented pointer file: ``head`` + a ``versions`` list of ``{id,
train, test, sha256_train, ...}``) is enough to find them and their recorded
checksum, without importing asaree-workspace's versioning/accept/discard logic.
"""

from __future__ import annotations

import contextlib
import hashlib
import io as _io
import json
import os
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from mcp.server import FastMCP
from mcp.server.fastmcp import Context

from asaree_sklearn_core import model, provenance

mcp = FastMCP("asaree-sklearn-model")

_META_KEY_WORKSPACE_ID = "motoro.workspace_id"


class HeadNotReadyError(Exception):
    """Raised when the workspace or its HEAD version can't be resolved."""


def _workspace_id_from_ctx(explicit: str, ctx: Context | None) -> str:
    if explicit.strip():
        return explicit.strip()
    if ctx is not None:
        try:
            meta = ctx.request_context.meta
            extra = getattr(meta, "model_extra", None) or {}
            wid = extra.get(_META_KEY_WORKSPACE_ID)
            if isinstance(wid, str) and wid:
                return wid
        except Exception:  # noqa: BLE001 — no ambient meta available outside a request
            pass
    raise HeadNotReadyError("workspace_id missing: pass it explicitly or via ambient _meta")


def _read_head(
    workspace_id: str,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series, str, str, str | None]:
    """Load (X_train, y_train, X_test, y_test, target, head_version, sha256_train)."""
    root = os.environ.get("ASAREE_DATASET_WORKSPACE_DIR", "./data/workspaces")
    state_path = Path(root).resolve() / workspace_id / "state.json"
    if not state_path.is_file():
        raise HeadNotReadyError(f"workspace {workspace_id!r} not initialized")
    state = json.loads(state_path.read_text())
    target = state["target_column"]
    head_id = state["head"]
    ver = next((v for v in state.get("versions", []) if v.get("id") == head_id), None)
    if ver is None:
        raise HeadNotReadyError(f"HEAD version {head_id!r} not found in {workspace_id!r}'s state")
    train_df = pd.read_parquet(ver["train"])
    test_df = pd.read_parquet(ver["test"])
    X_train = train_df.drop(columns=[target]).reset_index(drop=True)  # noqa: N806
    y_train = train_df[target].reset_index(drop=True)
    X_test = test_df.drop(columns=[target]).reset_index(drop=True)  # noqa: N806
    y_test = test_df[target].reset_index(drop=True)
    return X_train, y_train, X_test, y_test, target, head_id, ver.get("sha256_train")


@mcp.tool()
def run_model_script(
    code: str,
    random_seed: int = 42,
    task_type: str = "binary",
    positive_label: str = "",
    selection_metric: str = "roc_auc",
    payload_json: str = "",
    workspace_id: str = "",
    ctx: Context | None = None,
) -> str:
    """Execute an approved modeling script that NEVER sees the test split.

    The training code is bound ONLY the training data (X_train, y_train, read from
    the workspace HEAD — the fully engineered/preprocessed/selected model-ready
    matrix). It must define a top-level callable ``predict_proba(X)`` capturing its
    trained + calibrated pipeline (binary: 1-D P(positive); multiclass: 2-D class
    probabilities in ascending class-label order) and, for binary, set
    ``chosen_threshold``. THIS tool then applies the held-out test split and
    computes every test metric — the script cannot access or leak test labels, so
    evaluation leakage is structurally impossible.

    Pre-bound names: X_train, y_train, pd, np, random_seed, hp (parsed payload).
    Any installed package may be imported. The script may set a ``result`` dict of
    train-side decisions (it must NOT contain test metrics). The exact source is
    hashed (code_sha256) for verbatim-execution assertions.

    Args:
        code: Python source. Must define predict_proba(X); (binary) chosen_threshold.
        random_seed: Seed exposed to the code and used for permutation importance.
        task_type: 'binary' or 'multiclass'.
        positive_label: binary positive-class label (coerced to y's dtype).
        selection_metric: scores permutation importance ('average_precision' -> PR-AUC
            drop; otherwise ROC-AUC drop).
        payload_json: optional typed hyperparameter payload; bound as ``hp`` (its
            SHA-256 is returned as payload_sha256).
        workspace_id: Cell workspace id; when omitted, resolved from ambient _meta.
            The matrices are read from the accepted HEAD version on disk.
    """
    code_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()

    hp: dict[str, Any] | None = None
    payload_sha256 = ""
    if payload_json:
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            hp = json.loads(payload_json)
        except json.JSONDecodeError as e:
            return json.dumps(
                {"error": f"payload_json is not valid JSON: {e}",
                 "code_sha256": code_sha256, "payload_sha256": payload_sha256}
            )
        if not isinstance(hp, dict):
            return json.dumps(
                {"error": f"payload_json must be a JSON object, got {type(hp).__name__}.",
                 "code_sha256": code_sha256, "payload_sha256": payload_sha256}
            )

    try:
        wid = _workspace_id_from_ctx(workspace_id, ctx)
        X_train, y_train, X_test, y_test, target, _head_id, data_sha256_value = _read_head(wid)  # noqa: N806
    except HeadNotReadyError as e:
        return json.dumps({"error": f"workspace: {e}", "code_sha256": code_sha256})

    # Execute with TRAIN ONLY in scope — the test split is never bound.
    namespace: dict[str, Any] = {
        "pd": pd,
        "np": np,
        "random_seed": random_seed,
        "hp": hp,
        "X_train": X_train,
        "y_train": y_train,
        "result": None,
        "chosen_threshold": None,
        "predict_proba": None,
    }
    stdout = _io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            exec(code, namespace)  # noqa: S102
    except Exception as e:  # noqa: BLE001
        return json.dumps(
            {"error": f"Script execution failed: {type(e).__name__}: {e}",
             "traceback": traceback.format_exc()[-2000:],
             "stdout": stdout.getvalue()[-2000:],
             "executed_code": code, "code_sha256": code_sha256}
        )

    predict_proba = namespace.get("predict_proba")
    if not callable(predict_proba):
        return json.dumps(
            {"error": "Script must define a callable `predict_proba(X)` (missing or not callable).",
             "stdout": stdout.getvalue()[-2000:],
             "executed_code": code, "code_sha256": code_sha256}
        )

    # Trusted, server-side scoring on the held-out split the script never saw.
    try:
        classes = sorted(np.unique(y_train).tolist())
        if task_type == "binary":
            sample = classes[0]
            pos_val = type(sample)(positive_label) if positive_label != "" else classes[-1]
            y_bin = (y_test == pos_val).astype(int).values
            proba = np.asarray(predict_proba(X_test), dtype=float).ravel()
            thr = namespace.get("chosen_threshold")
            thr = 0.5 if thr is None else float(thr)
            test_metrics = model.binary_bundle(y_bin, proba, thr)
            perm = model.perm_importance(
                lambda Z: np.asarray(predict_proba(Z), dtype=float).ravel(),
                X_test, y_bin, "binary", [0, 1], random_seed, metric=selection_metric,
            )
        else:
            yv = y_test.values
            proba = np.asarray(predict_proba(X_test), dtype=float)
            test_metrics = model.multiclass_bundle(yv, proba, classes)
            perm = model.perm_importance(
                lambda Z: np.asarray(predict_proba(Z), dtype=float),
                X_test, yv, "multiclass", classes, random_seed, metric=selection_metric,
            )
    except Exception as e:  # noqa: BLE001
        return json.dumps(
            {"error": f"Test scoring failed: {type(e).__name__}: {e}",
             "traceback": traceback.format_exc()[-2000:],
             "stdout": stdout.getvalue()[-2000:],
             "executed_code": code, "code_sha256": code_sha256}
        )

    model_decisions = namespace.get("result")
    if not isinstance(model_decisions, dict):
        model_decisions = {}

    test_class_distribution = {str(k): int(v) for k, v in y_test.value_counts().items()}

    return json.dumps(
        {
            "test_metrics": test_metrics,
            "permutation_importance_top15": perm,
            "model_decisions": model_decisions,
            "stdout": stdout.getvalue()[-4000:],
            "executed_code": code,
            "code_sha256": code_sha256,
            "payload_sha256": payload_sha256,
            "n_train": int(len(X_train)),
            "n_test": int(len(X_test)),
            "n_features": int(X_train.shape[1]),
            "data_sha256": data_sha256_value,
            "package_versions": provenance.env_provenance(),
            "test_class_distribution": test_class_distribution,
        },
        default=provenance.np_default,
    )


@mcp.tool()
def ping() -> str:
    """Health check — returns 'pong' to verify the server is running."""
    return "pong"
