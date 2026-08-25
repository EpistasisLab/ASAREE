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
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
from asaree_workspace_core import (
    STAGE_VERSION,
    Workspace,
    WorkspaceError,
    make_workspace_id,
    provenance,
    resolve_dataset_name_from_ctx,
    resolve_owner_id_from_ctx,
    resolve_workspace_id_from_ctx,
)
from mcp.server import FastMCP
from mcp.server.fastmcp import Context

from asaree.services.dataset_workspaces import WorkspaceSeedError, seed_cell_workspace

INSTRUCTIONS = """\
Version a dataset across a cleaning/feature-engineering/selection pipeline, \
so each stage is reviewable and reversible.

Open a stage to get a scratch area seeded from the current accepted version, \
let the matching sklearn server write into it, then accept it to promote the \
result to the new HEAD -- or discard it and the accepted history is \
untouched. Everything downstream reads HEAD, so nothing sees a stage that \
was never accepted."""

mcp = FastMCP("asaree-workspace", instructions=INSTRUCTIONS)

# stderr, never stdout: stdout is the MCP transport itself on a stdio server.
logger = logging.getLogger(__name__)

_STAGES = ("dc", "fte", "fs")

# Stages migrated to the scratch-folder handoff (issue: BYO-MCP decoupling).
# A domain server for a stage in this set never imports asaree_workspace_core
# — it only reads/writes plain train.parquet/test.parquet/meta.json/learned.json
# in its own disposable scratch directory (see _scratch_dir below), and this
# server is the only thing that ever touches the permanent versioned tree.
#
# Two calling conventions share this same scratch directory:
#   - "chain" stages (dc, fte): each tool reads whatever's currently in
#     train.parquet/test.parquet and overwrites it — the working copy evolves
#     tool call by tool call within one attempt.
#   - "fixed-input" stages (fs): every tool independently re-reads the
#     UNCHANGING input_train.parquet/input_test.parquet (seeded once, never
#     touched again this attempt) and writes its candidate selection to
#     train.parquet/test.parquet — nothing chains via the working copy, so
#     the fixed pair lets a stage's tools be fully independent of each
#     other's call order, exactly like the old resolve_stage_input flow.
# Both files are always seeded (see _seed_scratch); a "chain" stage's tools
# simply never read the fixed pair, and a "fixed-input" stage's tools never
# read/write the working pair until they're ready to produce their result.
SCRATCH_STAGES = {"dc", "fte", "fs"}
FIXED_INPUT_STAGES = {"fs"}


def _scratch_dir(ws: Workspace, stage: str) -> Path:
    """This stage attempt's disposable scratch directory.

    Deterministic from (workspace_id, stage) — not a random id — so a domain
    server can compute its own path from the ambient workspace_id plus its own
    (hardcoded, per-server) stage name, without ever calling back into this
    server or importing anything beyond stdlib os/pathlib. That formula is the
    ENTIRE contract a domain server needs: two conventional file names inside
    this directory, nothing about state.json or versioning.
    """
    return ws.dir / ".scratch" / stage


def _seed_scratch(ws: Workspace, stage: str, target: str) -> None:
    """(Re)materialize a stage's input into its scratch dir, as BOTH the
    working pair (train.parquet/test.parquet — a "chain" stage's starting
    point) and the fixed pair (input_train.parquet/input_test.parquet — a
    "fixed-input" stage's only input, see SCRATCH_STAGES/FIXED_INPUT_STAGES).
    Writing both regardless of which convention this stage actually uses
    keeps this function, and the accept_stage/reset_stage call sites, the
    same for every scratch stage.

    Called by open_workspace (attempt start) and reset_stage (revision
    restart) — both cases want the domain server to see a clean, correct
    starting point. resolve_stage_input, not resolve_stage_working: nothing
    ever gets committed to the permanent tree mid-attempt anymore (only
    accept_stage's promote does), so "this stage's own committed-but-
    unaccepted version" never exists to fall back from — the stage input
    (prior accepted stage, or the v0_raw seed) is always the right seed.
    Safe to call repeatedly before the domain server's own tools have written
    anything (idempotent); MUST NOT be called after they have, or their
    in-progress work is lost — the notebook's own call ordering (reset_stage
    before a retry, open_workspace as the agent's first tool call in a fresh
    run) already guarantees this.
    """
    X_train, y_train, X_test, y_test = ws.read_stage_input(stage)
    scratch = _scratch_dir(ws, stage)
    scratch.mkdir(parents=True, exist_ok=True)
    train_df = X_train.copy()
    train_df[target] = y_train.to_numpy()
    test_df = X_test.copy()
    test_df[target] = y_test.to_numpy()
    train_df.to_parquet(scratch / "input_train.parquet", index=False)
    test_df.to_parquet(scratch / "input_test.parquet", index=False)
    if stage not in FIXED_INPUT_STAGES:
        # A "chain" stage's first tool call expects something already in the
        # working pair. A "fixed-input" stage must NOT get one here: its own
        # tools only ever write train.parquet/test.parquet once they've
        # produced a real result, and accept_stage's "nothing to accept" check
        # relies on that file being ABSENT until then — a placeholder here
        # would let accept_stage silently promote the untouched input as if a
        # selection had actually happened.
        train_df.to_parquet(scratch / "train.parquet", index=False)
        test_df.to_parquet(scratch / "test.parquet", index=False)
    (scratch / "meta.json").write_text(json.dumps({"target_column": target}))
    # Clear any stale provenance from a prior (discarded) attempt at this stage.
    for name in ("learned.json", "run_meta.json"):
        f = scratch / name
        if f.is_file():
            f.unlink()
    # A fixed-input stage's PRIOR attempt may have left a candidate selection
    # behind; a fresh/reset attempt must not resume from it.
    if stage in FIXED_INPUT_STAGES:
        for name in ("train.parquet", "test.parquet"):
            f = scratch / name
            if f.is_file():
                f.unlink()


def _read_scratch_output(
    scratch: Path, target: str
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    """Load whatever the domain server last wrote to its scratch train/test files."""
    train_df = pd.read_parquet(scratch / "train.parquet")
    test_df = pd.read_parquet(scratch / "test.parquet")
    X_train = train_df.drop(columns=[target]).reset_index(drop=True)
    y_train = train_df[target].reset_index(drop=True)
    X_test = test_df.drop(columns=[target]).reset_index(drop=True)
    y_test = test_df[target].reset_index(drop=True)
    return X_train, y_train, X_test, y_test


def _scratch_learned(scratch: Path) -> dict[str, Any]:
    f = scratch / "learned.json"
    if not f.is_file():
        return {}
    try:
        return dict(json.loads(f.read_text()))
    except (json.JSONDecodeError, OSError):
        return {}


def _scratch_run_id(scratch: Path) -> str:
    f = scratch / "run_meta.json"
    if not f.is_file():
        return ""
    try:
        return str(json.loads(f.read_text()).get("run_id", ""))
    except (json.JSONDecodeError, OSError):
        return ""


def _structural_checks(
    stage: str, target: str, train: pd.DataFrame, test: pd.DataFrame, state: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """Deterministic post-stage assertions (no agent) — shared by check_stage_gate
    (old-flow stages, reading a committed version from disk) and accept_stage's
    scratch-promote path (new-flow stages, checking in-memory scratch frames
    before they're ever written to the permanent tree). Per stage: train/test
    feature-column sets match; the target is present in both; DC leaves zero
    missing values; FS columns are a subset of v2_fte."""
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

    return checks, errors


@mcp.tool()
async def open_workspace(
    experiment_id: str = "",
    cell_label: str = "",
    name: str = "",
    target_column: str = "",
    stage: str = "",
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

    In a run started by ASAREE with a Dataset wired, you normally do not need to
    call this at all — ASAREE seeds the cell's workspace before your first turn
    (``services.dataset_workspaces.seed_cell_workspace``) and your prompt says so.
    It remains the entry point for everything that isn't that case: calling from
    outside a run, overriding the target column, picking between several wired
    datasets, (re)seeding a stage's scratch area, or re-reading the summary below.

    Every argument is optional in a run started by ASAREE: the workspace and the
    wired dataset both arrive as ambient request ``_meta``, so the usual call is
    a bare ``open_workspace()``. Pass an argument only to override what the run
    already knows, or when calling from outside a run.

    Args:
        experiment_id: The experiment this cell belongs to (workspace namespace).
            Optional — with cell_label, resolved from the ambient workspace_id.
        cell_label: Safe per-cell label. Optional, as above.
        name: Registered dataset name (must be a pre-split train/test registration,
            and owned by the user who started this run). Optional — resolved from
            _meta when the run has exactly one dataset wired; with several, this
            picks between them and the error lists the candidates.
        target_column: Override target column; defaults to the registry's.
        stage: For a stage migrated to the scratch-folder handoff (see
            SCRATCH_STAGES) — one of "dc"/"fte"/"fs" — (re)materializes that
            stage's current working matrix into its scratch directory, so the
            calling domain server's tools have a clean starting point. Omit for
            stages still on the old shared-library flow.
    """
    # Both halves of the workspace id, or neither: a half-specified pair would
    # have to be reconciled against the ambient id, and there is no sensible
    # answer when they disagree.
    if experiment_id and cell_label:
        composed = ""
    elif experiment_id or cell_label:
        return json.dumps({"error": "pass experiment_id and cell_label together, or neither (both come from _meta)."})
    else:
        composed = "resolve"
    try:
        if composed:
            workspace_id = resolve_workspace_id_from_ctx("", ctx)
        else:
            workspace_id = make_workspace_id(experiment_id, cell_label)
    except WorkspaceError as e:
        return json.dumps({"error": f"workspace: {e}"})

    resolved_name, candidates = resolve_dataset_name_from_ctx(name, ctx)
    if not resolved_name:
        return json.dumps(
            {
                "error": "name not provided and not resolvable from _meta"
                + (
                    f"; this run has {len(candidates)} datasets wired ({', '.join(candidates)}) "
                    "— pass the one this cell should use."
                    if candidates
                    else " (no dataset is wired into this run)."
                )
            }
        )

    try:
        owner_id = uuid.UUID(resolve_owner_id_from_ctx(ctx, required=True))
    except WorkspaceError as e:
        return json.dumps({"error": f"owner resolution: {e}"})

    # The seeding itself is shared with ASAREE's own pre-seeding at run start
    # (protocol_execution._resolve_node_dataset), so a run that never
    # calls this tool still lands in exactly the same on-disk state.
    try:
        seeded = await seed_cell_workspace(
            workspace_id=workspace_id,
            dataset_name=resolved_name,
            owner_id=owner_id,
            target_column=target_column,
        )
    except WorkspaceSeedError as e:
        return json.dumps({"error": str(e), "workspace_id": workspace_id})

    ws, resolved_target = seeded.workspace, seeded.target_column
    try:
        X_train, y_train, X_test, y_test = ws.read_head()  # noqa: N806 — matches sklearn convention throughout
        if stage in SCRATCH_STAGES:
            _seed_scratch(ws, stage, resolved_target)
    except (WorkspaceError, FileNotFoundError, OSError) as e:
        return json.dumps({"error": f"workspace: {e}"})

    data_sha256 = provenance.data_sha256(X_train, X_test, y_train, y_test, resolved_target)
    train_dist = y_train.value_counts(normalize=True).round(4).to_dict()
    missing = X_train.isnull().sum()
    accepted_stages = [s for s in _STAGES if ws.has_accepted(s)]
    response: dict[str, object] = {
        "workspace_id": workspace_id,
        # Echoed because both may have been resolved from ambient _meta rather
        # than passed: the caller should be able to see what it actually opened.
        "dataset_name": resolved_name,
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
    has_dict = seeded.data_dictionary_available
    response["data_dictionary_available"] = has_dict
    if has_dict:
        response["data_dictionary_hint"] = (
            f"Call get_data_dictionary(name='{resolved_name}', columns='col1,col2') for detail."
        )
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
    """Accept a stage's output and advance HEAD to it (critic-gated).

    The ONLY operation that advances HEAD, so a rejected or never-committed stage
    can never become a resume point or a scoring input.

    For a SCRATCH_STAGES stage (see module docstring), this is also where the
    domain server's scratch output first touches the permanent versioned tree at
    all: it's read, run through the same structural checks check_stage_gate
    exposes, and only promoted (written + accepted in one step) if they pass —
    a failed check is reported and nothing is written, so a broken scratch
    output never becomes visible history. For an old-flow stage, this just
    advances HEAD to whatever was already committed (no structural judgment).

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

        if stage in SCRATCH_STAGES:
            scratch = _scratch_dir(ws, stage)
            if not (scratch / "train.parquet").is_file() or not (scratch / "test.parquet").is_file():
                return json.dumps(
                    {"error": f"nothing to accept: {stage!r} scratch is empty "
                              "— the domain server hasn't written an output yet."}
                )
            target = ws.target_column
            X_train, y_train, X_test, y_test = _read_scratch_output(scratch, target)
            state = ws.load_state()
            checks, errors = _structural_checks(
                stage, target,
                pd.concat([X_train, y_train.rename(target)], axis=1),
                pd.concat([X_test, y_test.rename(target)], axis=1),
                state,
            )
            if errors:
                return json.dumps({"error": "structural checks failed", "checks": checks, "errors": errors})
            ws.write_stage(
                stage,
                X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
                learned=_scratch_learned(scratch), run_id=_scratch_run_id(scratch),
                accepted=True,
            )
            shutil.rmtree(scratch, ignore_errors=True)
        else:
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
    """Discard a stage's in-progress attempt so a re-run starts clean.

    Called by the orchestrator before a critic revision. For a SCRATCH_STAGES
    stage, this wipes the scratch directory and re-seeds it fresh from the
    stage's input (same as open_workspace's first-attempt seeding) — the
    domain server never committed anything to the permanent tree, so there is
    nothing to discard there. For an old-flow stage, this discards the
    committed-but-unaccepted version (the rejected attempt), since that stage's
    tools read the *working* matrix and a naive re-run would transform on top
    of it. Refuses to touch an ACCEPTED version (HEAD/handoff) either way; HEAD
    is never moved.

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

        if stage in SCRATCH_STAGES:
            scratch = _scratch_dir(ws, stage)
            discarded = scratch.is_dir() and any(scratch.iterdir())
            shutil.rmtree(scratch, ignore_errors=True)
            _seed_scratch(ws, stage, ws.target_column)
        else:
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
        target = ws.target_column
        version_id = STAGE_VERSION[stage]
        ver = next((v for v in state.get("versions", []) if v.get("id") == version_id), None)
        if ver is None:
            return json.dumps({"passed": False, "errors": [f"stage {stage!r} has no committed {version_id} version."]})
        train = pd.read_parquet(ver["train"])
        test = pd.read_parquet(ver["test"])
    except (WorkspaceError, OSError, FileNotFoundError) as e:
        return json.dumps({"passed": False, "errors": [f"gate read failed: {e}"]})

    checks, errors = _structural_checks(stage, target, train, test, state)
    n_features = len([c for c in train.columns if c != target])
    return json.dumps(
        {
            "passed": not errors,
            "stage": stage,
            "version": version_id,
            "n_train": int(len(train)),
            "n_test": int(len(test)),
            "n_features": n_features,
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
def read_scratch_learned(stage: str, workspace_id: str = "", ctx: Context[Any, Any, Any] | None = None) -> str:
    """Return a SCRATCH_STAGES stage's current in-progress attempt's learned
    block — the provenance a domain server has written to its scratch dir so
    far this attempt, before accept_stage ever promotes it (or reset_stage
    discards it).

    Unlike read_stage_manifest (which only ever has something once a stage is
    ACCEPTED), this is the only place a rejected-but-not-yet-accepted attempt's
    decisions exist — needed to snapshot them before a critic-requested
    revision resets the scratch dir out from under them.

    Args:
        stage: one of ``dc``, ``fte``, ``fs``.
        workspace_id: optional; resolved from _meta when omitted.
    """
    try:
        wid = resolve_workspace_id_from_ctx(workspace_id, ctx)
        ws = Workspace(wid)
        if not ws.exists():
            return json.dumps({"error": f"workspace {wid!r} not initialized."})
    except WorkspaceError as e:
        return json.dumps({"error": f"read_scratch_learned: {e}"})
    if stage not in SCRATCH_STAGES:
        return json.dumps({"error": f"stage {stage!r} is not a scratch stage."})
    scratch = _scratch_dir(ws, stage)
    return json.dumps({"learned": _scratch_learned(scratch)})


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
