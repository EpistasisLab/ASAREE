"""Stage commit — the write side of the leakage-safe staged handoff (#1452/#1453).

When a transform tool has produced its cumulative ``(train, test)`` output for a
stage, it commits that pair as the stage's on-disk workspace version. This fits
nothing — the caller has already applied its train-fit transform to both splits,
so the leakage guarantee stays structural. Lifted from the monolith's
``_commit_stage_to_workspace`` (MCP-free: it only touches the on-disk
:class:`Workspace`).
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from .workspace import Workspace, WorkspaceError


def commit_stage(
    workspace_id: str,
    stage: str,
    *,
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    learned: dict[str, Any],
    rationale: str = "",
    run_id: str = "",
    root: str | None = None,
) -> dict[str, Any]:
    """Write a stage's current (train, test) matrices as its workspace version.

    Fits nothing — the caller has already applied its train-fit transform to both
    splits, so the leakage guarantee stays structural. Multiple tools within one
    stage each call this; the parquet is overwritten so the LAST call is the
    committed output, and the manifest's ``learned`` block accumulates across the
    stage's tools (DC's manifest ends up carrying both the domain rules and the
    imputation; FTE's both the recipe and the encoding). HEAD is not advanced here
    — only ``accept_stage`` does that, after the critic approves. Raises
    WorkspaceError on any failure so the calling tool reports it and the agent
    retries (a failed persist must never look like success).
    """
    ws = Workspace(workspace_id, root=root)
    if not ws.exists():
        raise WorkspaceError(
            f"workspace {workspace_id!r} is not open — call open_workspace first."
        )
    merged: dict[str, Any] = {}
    manifest_path = ws.manifests_dir / f"{stage}.json"
    if manifest_path.is_file():
        try:
            merged = dict(json.loads(manifest_path.read_text()).get("learned", {}))
        except (json.JSONDecodeError, OSError):
            merged = {}
    merged.update(learned)
    return ws.write_stage(
        stage,
        X_train=X_train.reset_index(drop=True),
        y_train=y_train.reset_index(drop=True),
        X_test=X_test.reset_index(drop=True),
        y_test=y_test.reset_index(drop=True),
        learned=merged,
        rationale=rationale,
        run_id=run_id,
    )


def commit_fs_selection(
    workspace_id: str,
    *,
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    selected: list[str],
    method: str,
    run_id: str = "",
    root: str | None = None,
) -> dict[str, Any]:
    """Commit ``v3_fs`` = the (already encoded) FTE matrix restricted to *selected*.

    In workspace mode the input (v2_fte) is already numeric/model-ready, so column
    selection alone yields the model-ready matrix. Only columns actually present in
    the matrix are kept, on both splits.
    """
    keep = [c for c in selected if c in X_train.columns]
    m = commit_stage(
        workspace_id,
        "fs",
        X_train=X_train[keep],
        y_train=y_train,
        X_test=X_test[keep],
        y_test=y_test,
        learned={"method": method, "selected_features": keep, "n_selected": len(keep)},
        run_id=run_id,
        root=root,
    )
    return {
        "version": m["output_version"],
        "n_features": m["n_features"],
        "sha256_train": m["sha256_train"],
    }
