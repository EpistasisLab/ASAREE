"""asaree-sklearn-fs — feature-selection MCP server (SF-FS).

Thin FastMCP wrapper over :mod:`asaree_sklearn_core`. This server does NOT
import asaree_workspace_core — it only knows the ambient ``workspace_id``
(request ``_meta``) and one fixed convention for where its own disposable
scratch files live, under
``{ASAREE_DATASET_WORKSPACE_DIR}/{workspace_id}/.scratch/fs/``.
asaree-workspace prepares that directory (``open_workspace(..., stage="fs")``)
and is the only thing that later reads it back out and promotes it into the
permanent versioned tree (``accept_stage``).

Unlike DC/FTE, FS's tools do NOT chain via a working copy: each selector
independently re-reads the FIXED ``input_train.parquet``/``input_test.parquet``
(the accepted FTE output, seeded once and never touched again this attempt —
compounding across selectors is explicit via ``features_json``) and writes its
candidate selection to ``train.parquet``/``test.parquet``, the scratch output
accept_stage promotes. Correlations and class balance are read-only
diagnostics over the same fixed input.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from mcp.server import FastMCP
from mcp.server.fastmcp import Context

import asaree_sklearn_core as core
from asaree_sklearn_core import eda, fs

INSTRUCTIONS = """\
Select which features to keep, and see the correlations and class balance \
behind that choice.

Each selector independently re-reads the same fixed input rather than \
chaining off the previous one, so running two does not silently compound \
them -- to stack selections, pass the earlier result forward via \
features_json. The diagnostics are read-only views of that same input."""

mcp = FastMCP("asaree-sklearn-fs", instructions=INSTRUCTIONS)

STAGE = "fs"

_META_KEY_WORKSPACE_ID = "motoro.workspace_id"


class ScratchNotReadyError(Exception):
    """Raised when this stage's scratch input is missing, empty, or unreadable."""


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
    raise ScratchNotReadyError("workspace_id missing: pass it explicitly or via ambient _meta")


def _scratch_dir(workspace_id: str) -> Path:
    root = os.environ.get("ASAREE_DATASET_WORKSPACE_DIR", "./data/workspaces")
    return Path(root).resolve() / workspace_id / ".scratch" / STAGE


def _read_scratch_input(
    workspace_id: str,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series, str]:
    """The FIXED v2_fte-derived input — the same for every tool call this attempt."""
    scratch = _scratch_dir(workspace_id)
    train_path = scratch / "input_train.parquet"
    meta_path = scratch / "meta.json"
    if not train_path.is_file() or not meta_path.is_file():
        raise ScratchNotReadyError(
            f"no scratch input for {workspace_id!r}/{STAGE} — call "
            "open_workspace(..., stage='fs') first."
        )
    target = json.loads(meta_path.read_text())["target_column"]
    train_df = pd.read_parquet(train_path)
    test_df = pd.read_parquet(scratch / "input_test.parquet")
    X_train = train_df.drop(columns=[target]).reset_index(drop=True)  # noqa: N806
    y_train = train_df[target].reset_index(drop=True)
    X_test = test_df.drop(columns=[target]).reset_index(drop=True)  # noqa: N806
    y_test = test_df[target].reset_index(drop=True)
    return X_train, y_train, X_test, y_test, target


def _write_scratch_output(
    workspace_id: str,
    target: str,
    X_train: pd.DataFrame,  # noqa: N803
    y_train: pd.Series,
    X_test: pd.DataFrame,  # noqa: N803
    y_test: pd.Series,
) -> None:
    scratch = _scratch_dir(workspace_id)
    train_df = X_train.copy()
    train_df[target] = y_train.to_numpy()
    test_df = X_test.copy()
    test_df[target] = y_test.to_numpy()
    train_df.to_parquet(scratch / "train.parquet", index=False)
    test_df.to_parquet(scratch / "test.parquet", index=False)


def _merge_learned(workspace_id: str, new_keys: dict[str, Any]) -> None:
    path = _scratch_dir(workspace_id) / "learned.json"
    merged: dict[str, Any] = {}
    if path.is_file():
        try:
            merged = json.loads(path.read_text())
        except json.JSONDecodeError:
            merged = {}
    merged.update(new_keys)
    path.write_text(json.dumps(merged, default=str))


def _record_run_id(workspace_id: str, run_id: str) -> None:
    if run_id:
        (_scratch_dir(workspace_id) / "run_meta.json").write_text(json.dumps({"run_id": run_id}))


@mcp.tool()
def variance_filter(
    threshold: float = 0.0,
    workspace_id: str = "",
    run_id: str = "",
    ctx: Context | None = None,
) -> str:
    """Drop low/near-zero-variance features on the training fold; writes the selection.

    Args:
        threshold: Variance threshold; features with variance <= threshold are dropped.
        workspace_id: Optional explicit workspace id; else resolved from _meta.
        run_id: Optional ASAREE run id, recorded alongside this attempt's provenance.
    """
    try:
        wid = _workspace_id_from_ctx(workspace_id, ctx)
        X_train, y_train, X_test, y_test, target = _read_scratch_input(wid)  # noqa: N806
    except ScratchNotReadyError as e:
        return json.dumps({"error": str(e)})

    selector, dropped, space = fs.variance_filter(X_train, wid, threshold=threshold)
    keep = selector.selected_features
    _write_scratch_output(wid, target, X_train[keep], y_train, X_test[keep], y_test)
    _merge_learned(wid, {"method": "variance_filter", "selected_features": keep})
    _record_run_id(wid, run_id)

    return json.dumps(
        {
            "space": space,
            "threshold": threshold,
            "n_features_out": len(keep),
            "kept_features": keep,
            "dropped_features": dropped,
            "note": "Selection written to this stage's scratch.",
        }
    )


@mcp.tool()
def supervised_filter(
    method: str = "f_classif",
    k: int = 10,
    features_json: str = "",
    workspace_id: str = "",
    run_id: str = "",
    ctx: Context | None = None,
) -> str:
    """Rank features by a supervised univariate filter and keep the top-k; writes the selection.

    Use f_classif (ANOVA F) or mutual_info_classif for classification.

    Args:
        method: 'f_classif' or 'mutual_info_classif'.
        k: Number of top-ranked features to keep.
        features_json: Optional JSON list of feature names to restrict scoring to
            (e.g. the survivors after variance/correlation filtering).
        workspace_id: Optional explicit workspace id; else resolved from _meta.
        run_id: Optional ASAREE run id, recorded alongside this attempt's provenance.
    """
    try:
        wid = _workspace_id_from_ctx(workspace_id, ctx)
        X_train, y_train, X_test, y_test, target = _read_scratch_input(wid)  # noqa: N806
    except ScratchNotReadyError as e:
        return json.dumps({"error": str(e)})

    subset = None
    if features_json.strip():
        subset, err = core.parse_json_list(
            features_json, arg_name="features_json",
            prefer_keys=("selected_features", "features"),
        )
        if err is not None:
            return json.dumps({"error": err})

    try:
        selector = fs.supervised_filter(
            X_train, y_train, wid, method=method, k=k, subset=subset
        )
    except core.ComputeError as e:
        return json.dumps({"error": str(e)})

    keep = selector.selected_features
    _write_scratch_output(wid, target, X_train[keep], y_train, X_test[keep], y_test)
    _merge_learned(wid, {"method": f"supervised_{method}", "selected_features": keep})
    _record_run_id(wid, run_id)

    return json.dumps(
        {
            "method": method,
            "selected_features": keep,
            "top_scores": [[d["feature"], d["score"]] for d in selector.importances[:20]],
            "note": "Selection written to this stage's scratch.",
        }
    )


@mcp.tool()
def compute_correlations(
    method: str = "spearman",
    workspace_id: str = "",
    ctx: Context | None = None,
) -> str:
    """Feature-target and high feature-feature correlations on the training fold.

    Read-only diagnostic. Args: method='spearman' (default) or 'pearson';
    workspace_id optional (else resolved from _meta).
    """
    try:
        wid = _workspace_id_from_ctx(workspace_id, ctx)
        X_train, y_train, _, _, _ = _read_scratch_input(wid)  # noqa: N806
    except ScratchNotReadyError as e:
        return json.dumps({"error": str(e)})
    return json.dumps(eda.compute_correlations(X_train, y_train, method=method))


@mcp.tool()
def check_class_balance(
    workspace_id: str = "",
    ctx: Context | None = None,
) -> str:
    """Class distribution and imbalance in the target (training fold). Read-only."""
    try:
        wid = _workspace_id_from_ctx(workspace_id, ctx)
        _, y_train, _, _, _ = _read_scratch_input(wid)
    except ScratchNotReadyError as e:
        return json.dumps({"error": str(e)})
    return json.dumps(eda.check_class_balance(y_train))


@mcp.tool()
def ping() -> str:
    """Health check — returns 'pong' to verify the server is running."""
    return "pong"
