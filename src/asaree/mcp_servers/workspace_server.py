"""asaree-workspace — ASAREE's own workspace-orchestration MCP server.

Opens/seeds a per-cell workspace from the dataset registry and drives the staged,
leakage-safe handoff (status, accept, structural gate, manifest). Thin wrapper
over :mod:`asaree_workspace_core`. Bundled and auto-registered by ASAREE itself
as a global system server (``asaree.app``'s lifespan) — not something a
researcher registers, and not one copy per owner.

``open_workspace`` is the only tool that touches the dataset registry, and it
does so as a direct, in-process call to ``asaree.services.datasets`` (this is
ASAREE's own code, running in ASAREE's own process family — no HTTP round-trip,
no service token to manage), scoped to the run's owner via the ambient
``_meta`` (``resolve_owner_id_from_ctx``) rather than trusting a bare dataset
name. Every other tool operates purely on the shared on-disk workspace.
Directly invocable — the notebook driver runs in a different process from the
agents and drives resume from here.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pandas as pd
from asaree_workspace_core import (
    STAGE_VERSION,
    Workspace,
    WorkspaceError,
    make_workspace_id,
    provenance,
    resolve_owner_id_from_ctx,
    resolve_workspace_id_from_ctx,
)
from mcp.server import FastMCP
from mcp.server.fastmcp import Context

from asaree.models.database import get_session
from asaree.services.datasets import get_dataset_by_name

mcp = FastMCP("asaree-workspace")

_STAGES = ("dc", "fte", "fs")


async def _fetch_owned_registration(
    name: str, ctx: Context[Any, Any, Any] | None
) -> tuple[dict[str, Any] | None, str | None]:
    """Look up a registered dataset by name, scoped to the calling run's owner.

    In-process DB call, not an HTTP round-trip — this server is ASAREE's own
    code. Returns (registration-as-dict, error); a dataset that exists but
    belongs to a different owner is reported the same as "not found", matching
    the HTTP API's own 404-not-403 convention (asaree/api/datasets.py).
    """
    owner_id_str = resolve_owner_id_from_ctx(ctx, required=True)
    owner_id = uuid.UUID(owner_id_str)
    async with get_session() as db:
        dataset = await get_dataset_by_name(db, name)
        if dataset is None or dataset.owner_id != owner_id:
            return None, f"Dataset '{name}' not found in registry."
        return {
            "target_column": dataset.target_column,
            "train_path": dataset.train_path,
            "test_path": dataset.test_path,
            "dictionary_json": dataset.dictionary_json,
        }, None


@mcp.tool()
async def open_workspace(
    experiment_id: str,
    cell_label: str,
    name: str,
    target_column: str = "",
    ctx: Context[Any, Any, Any] | None = None,
) -> str:
    """Open (create if absent) the on-disk workspace for one pipeline cell.

    Seeds version ``v0_raw`` from the registered dataset's pre-split
    ``train.parquet``/``test.parquet`` (the split is frozen at upload — never
    re-split) and returns a ``workspace_id`` every downstream stage reads/writes.
    Persistence and the train/test handoff live on the shared filesystem, so a
    later stage (or the notebook's scoring call, in another process) sees the exact
    same versions. Idempotent: re-opening resumes — completed stages are reported
    in ``accepted_stages``. The response describes the TRAINING split only; the
    held-out test rows are never surfaced (leakage guard).

    Args:
        experiment_id: The experiment this cell belongs to (workspace namespace).
        cell_label: Safe per-cell label.
        name: Registered dataset name (must be a pre-split train/test registration,
            and owned by the user who started this run).
        target_column: Override target column; defaults to the registry's.
    """
    try:
        reg, err = await _fetch_owned_registration(name, ctx)
    except WorkspaceError as e:
        return json.dumps({"error": f"owner resolution: {e}"})
    if err is not None:
        return json.dumps({"error": err})
    assert reg is not None

    resolved_target = target_column or reg.get("target_column") or ""
    if not resolved_target:
        return json.dumps({"error": "target_column not provided and not set in registry."})

    try:
        workspace_id = make_workspace_id(experiment_id, cell_label)
        ws = Workspace.open(
            workspace_id,
            target_column=resolved_target,
            seed_train_path=reg["train_path"],
            seed_test_path=reg["test_path"],
        )
        X_train, y_train, X_test, y_test = ws.read_head()  # noqa: N806 — matches sklearn convention throughout
    except (WorkspaceError, FileNotFoundError, OSError) as e:
        return json.dumps({"error": f"workspace: {e}"})

    data_sha256 = provenance.data_sha256(X_train, X_test, y_train, y_test, resolved_target)
    train_dist = y_train.value_counts(normalize=True).round(4).to_dict()
    missing = X_train.isnull().sum()
    accepted_stages = [s for s in _STAGES if ws.has_accepted(s)]
    response: dict[str, object] = {
        "workspace_id": workspace_id,
        "head": ws.load_state().get("head"),
        "target_column": resolved_target,
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "n_features": len(X_train.columns),
        "feature_names": list(X_train.columns),
        "train_class_distribution": {str(k): v for k, v in train_dist.items()},
        "missing_values": {c: int(n) for c, n in missing.items() if n > 0},
        "dtypes": {c: str(dt) for c, dt in X_train.dtypes.items()},
        "data_sha256": data_sha256,
        "accepted_stages": accepted_stages,
        "note": "Workspace opened. The test split is held out and never returned. "
        "Downstream stages read/write this workspace_id on disk (ambient _meta).",
    }
    has_dict = bool(reg.get("dictionary_json"))
    response["data_dictionary_available"] = has_dict
    if has_dict:
        response["data_dictionary_hint"] = f"Call get_data_dictionary(name='{name}', columns='col1,col2') for detail."
    return json.dumps(response)


@mcp.tool()
def workspace_status(workspace_id: str = "", ctx: Context[Any, Any, Any] | None = None) -> str:
    """Report a cell workspace's on-disk state, for orchestration and resume.

    Safe before the workspace exists (fresh cell): returns ``exists=false`` with
    empty ``accepted_stages``. Otherwise returns HEAD, accepted stages (skipped on
    resume), and a per-version summary.

    Args:
        workspace_id: ``"{experiment_id}/{cell_label}"``. Optional — resolved from
            the ambient request ``_meta`` when omitted.
    """
    try:
        wid = resolve_workspace_id_from_ctx(workspace_id, ctx)
        ws = Workspace(wid)
    except WorkspaceError as e:
        return json.dumps({"error": f"workspace: {e}"})
    if not ws.exists():
        return json.dumps({"workspace_id": wid, "exists": False, "head": None, "accepted_stages": [], "versions": []})
    state = ws.load_state()
    accepted = [s for s in _STAGES if ws.has_accepted(s)]
    versions = [
        {"id": v.get("id"), "stage": v.get("stage"), "accepted": bool(v.get("accepted")), "run_id": v.get("run_id", "")}
        for v in state.get("versions", [])
    ]
    return json.dumps(
        {
            "workspace_id": wid,
            "exists": True,
            "head": state.get("head"),
            "target_column": state.get("target_column"),
            "accepted_stages": accepted,
            "versions": versions,
        }
    )


@mcp.tool()
def accept_stage(stage: str, workspace_id: str = "", ctx: Context[Any, Any, Any] | None = None) -> str:
    """Accept a stage's committed output and advance HEAD to it (critic-gated).

    The ONLY operation that advances HEAD, so a rejected or never-committed stage
    can never become a resume point or a scoring input.

    Args:
        stage: one of ``dc``, ``fte``, ``fs``.
        workspace_id: ``"{experiment_id}/{cell_label}"``. Optional — resolved from _meta.
    """
    try:
        wid = resolve_workspace_id_from_ctx(workspace_id, ctx)
        ws = Workspace(wid)
        if not ws.exists():
            return json.dumps({"error": f"workspace {wid!r} not initialized."})
        if stage not in STAGE_VERSION:
            return json.dumps({"error": f"unknown stage {stage!r}; expected one of {list(STAGE_VERSION)}."})
        ws.accept_stage(stage)
        state = ws.load_state()
    except WorkspaceError as e:
        return json.dumps({"error": f"accept_stage: {e}"})
    return json.dumps(
        {
            "workspace_id": wid,
            "accepted_stage": stage,
            "head": state.get("head"),
            "accepted_stages": [s for s in _STAGES if ws.has_accepted(s)],
        }
    )


@mcp.tool()
def reset_stage(stage: str, workspace_id: str = "", ctx: Context[Any, Any, Any] | None = None) -> str:
    """Discard a stage's committed-but-unaccepted version so a re-run starts clean.

    Called by the orchestrator before a critic revision: the rejected attempt
    committed ``v{n}`` (unaccepted), and since the stage's tools read the *working*
    matrix, a naive re-run would transform on top of that rejected output. This
    reverts the stage to its input (prior accepted stage or the ``v0_raw`` seed).
    Refuses to touch an ACCEPTED version (HEAD/handoff); HEAD is never moved.

    Args:
        stage: one of ``dc``, ``fte``, ``fs``.
        workspace_id: ``"{experiment_id}/{cell_label}"``. Optional — resolved from _meta.
    """
    try:
        wid = resolve_workspace_id_from_ctx(workspace_id, ctx)
        ws = Workspace(wid)
        if not ws.exists():
            return json.dumps({"error": f"workspace {wid!r} not initialized."})
        if stage not in STAGE_VERSION:
            return json.dumps({"error": f"unknown stage {stage!r}; expected one of {list(STAGE_VERSION)}."})
        discarded = ws.discard_stage(stage)
        state = ws.load_state()
    except WorkspaceError as e:
        return json.dumps({"error": f"reset_stage: {e}"})
    return json.dumps(
        {
            "workspace_id": wid,
            "reset_stage": stage,
            "discarded": discarded,
            "head": state.get("head"),
            "accepted_stages": [s for s in _STAGES if ws.has_accepted(s)],
        }
    )


@mcp.tool()
def check_stage_gate(stage: str, workspace_id: str = "", ctx: Context[Any, Any, Any] | None = None) -> str:
    """Run the structural post-stage assertions on a committed stage version.

    Deterministic backstop (no agent). Per stage: the committed version exists with
    both partitions; train/test feature-column sets match; the target is present in
    both; DC leaves zero missing values; FS columns are a subset of v2_fte.

    Args:
        stage: one of ``dc``, ``fte``, ``fs``.
        workspace_id: optional; resolved from _meta when omitted.
    """
    try:
        wid = resolve_workspace_id_from_ctx(workspace_id, ctx)
        ws = Workspace(wid)
        if not ws.exists():
            return json.dumps({"passed": False, "errors": [f"workspace {wid!r} not initialized."]})
        if stage not in STAGE_VERSION:
            return json.dumps({"passed": False, "errors": [f"unknown stage {stage!r}."]})
        state = ws.load_state()
        target = state.get("target_column")
        version_id = STAGE_VERSION[stage]
        ver = next((v for v in state.get("versions", []) if v.get("id") == version_id), None)
        if ver is None:
            return json.dumps({"passed": False, "errors": [f"stage {stage!r} has no committed {version_id} version."]})
        train = pd.read_parquet(ver["train"])
        test = pd.read_parquet(ver["test"])
    except (WorkspaceError, OSError, FileNotFoundError) as e:
        return json.dumps({"passed": False, "errors": [f"gate read failed: {e}"]})

    checks: list[str] = []
    errors: list[str] = []

    train_cols = [c for c in train.columns if c != target]
    test_cols = [c for c in test.columns if c != target]
    if set(train_cols) == set(test_cols):
        checks.append(f"train/test column sets match ({len(train_cols)} features)")
    else:
        only_tr = sorted(set(train_cols) - set(test_cols))
        only_te = sorted(set(test_cols) - set(train_cols))
        errors.append(f"train/test column mismatch: only_train={only_tr[:10]}, only_test={only_te[:10]}")

    if target in train.columns and target in test.columns:
        checks.append(f"target {target!r} present in both partitions")
    else:
        errors.append(f"target column {target!r} missing from a partition")

    if stage == "dc":
        n_missing_tr = int(train[train_cols].isna().sum().sum())
        n_missing_te = int(test[test_cols].isna().sum().sum())
        if n_missing_tr == 0 and n_missing_te == 0:
            checks.append("zero missing values after DC")
        else:
            errors.append(f"DC left missing values: train={n_missing_tr}, test={n_missing_te}")

    if stage == "fs":
        fte_ver = next((v for v in state.get("versions", []) if v.get("id") == "v2_fte"), None)
        if fte_ver is None:
            errors.append("FS gate: no v2_fte version to check subset against")
        else:
            fte_cols = set(pd.read_parquet(fte_ver["train"]).columns) - {target}
            extra = sorted(set(train_cols) - fte_cols)
            if extra:
                errors.append(f"FS selected columns not in v2_fte: {extra[:10]}")
            else:
                checks.append(f"FS columns subset of v2_fte ({len(train_cols)}/{len(fte_cols)})")

    return json.dumps(
        {
            "passed": not errors,
            "stage": stage,
            "version": version_id,
            "n_train": int(len(train)),
            "n_test": int(len(test)),
            "n_features": len(train_cols),
            "checks": checks,
            "errors": errors,
        }
    )


@mcp.tool()
def read_stage_manifest(stage: str, workspace_id: str = "", ctx: Context[Any, Any, Any] | None = None) -> str:
    """Return a committed stage's provenance manifest (learned params + rationale).

    Args:
        stage: one of ``dc``, ``fte``, ``fs``.
        workspace_id: optional; resolved from _meta when omitted.
    """
    try:
        wid = resolve_workspace_id_from_ctx(workspace_id, ctx)
        ws = Workspace(wid)
        if not ws.exists():
            return json.dumps({"error": f"workspace {wid!r} not initialized."})
        if stage not in STAGE_VERSION:
            return json.dumps({"error": f"unknown stage {stage!r}."})
        path = ws.manifests_dir / f"{stage}.json"
        if not path.is_file():
            return json.dumps({"error": f"no committed manifest for stage {stage!r}."})
        return path.read_text()
    except (WorkspaceError, OSError) as e:
        return json.dumps({"error": f"read_stage_manifest: {e}"})


@mcp.tool()
def reset_session() -> str:
    """No-op compatibility shim (the split servers hold no in-process session).

    There is no shared process state to reset — every handoff lives on disk, per
    workspace. Retained so a driver's between-run call keeps working.
    """
    return json.dumps({"note": "stateless server; no in-process session to reset", "cleared": {}})


@mcp.tool()
def ping() -> str:
    """Health check — returns 'pong' to verify the server is running."""
    return "pong"


if __name__ == "__main__":
    mcp.run()
