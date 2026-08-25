"""asaree-sklearn-eda — exploratory-analysis MCP server.

Thin FastMCP wrapper over :mod:`asaree_sklearn_core`. ``get_feature_distributions``
and ``get_dataset_info`` resolve their matrix from the workspace HEAD;
``get_data_dictionary`` reads the copy ``asaree-workspace``'s ``open_workspace``
publishes into that same workspace directory, falling back to the ASAREE
registry over HTTP when there's no ambient workspace.

This server has no dependency on asaree_workspace_core — HEAD's train/test
parquet already live at a stable, permanent location once a stage is
accepted (accept_stage never moves or rewrites them again), so reading
``state.json`` directly (a small, documented pointer file: ``head`` + a
``versions`` list of ``{id, train, test, sha256_train, ...}``) is enough to
find them. That's a read of two known keys, not a dependency on
asaree-workspace's versioning/accept/discard logic.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from mcp.server import FastMCP
from mcp.server.fastmcp import Context

from asaree_sklearn_core import eda

INSTRUCTIONS = """\
Look at a dataset before modeling it: shape, per-feature distributions, and \
what the columns actually mean.

get_dataset_info and get_feature_distributions read the current accepted \
version of the working dataset; get_data_dictionary returns the registered \
descriptions of its columns. All read-only -- nothing here changes the data."""

mcp = FastMCP("asaree-sklearn-eda", instructions=INSTRUCTIONS)

_ASAREE_API_URL = os.environ.get("ASAREE_API_URL", "http://localhost:8000")
_ASAREE_MCP_API_KEY = os.environ.get("ASAREE_INTERNAL_MCP_API_KEY", "")

_META_KEY_WORKSPACE_ID = "motoro.workspace_id"
# The names of the datasets wired into this run, published by ASAREE under
# Motoro's caller-ambient prefix. Mirrors asaree_workspace_core's
# META_KEY_DATASET_NAMES as a literal, for the same reason the key above is one:
# this server takes no dependency on that package (see the module docstring).
_META_KEY_DATASET_NAMES = "motoro.ambient.dataset_names"


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


def _dataset_name_from_ctx(explicit: str, ctx: Context | None) -> str:
    """Resolve which registered dataset a call means, explicit argument first.

    Falls back to the ambient names only when exactly one dataset is wired into
    the run — with several there is a real choice, and guessing the first would
    silently read the wrong dataset.
    """
    if explicit.strip():
        return explicit.strip()
    if ctx is None:
        return ""
    try:
        extra = getattr(ctx.request_context.meta, "model_extra", None) or {}
    except Exception:  # noqa: BLE001 — no ambient meta available outside a request
        return ""
    names = extra.get(_META_KEY_DATASET_NAMES)
    if not isinstance(names, list):
        return ""
    names = [n for n in names if isinstance(n, str) and n]
    return names[0] if len(names) == 1 else ""


def _read_head(workspace_id: str) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series, str]:
    """Load (X_train, y_train, X_test, y_test, target) for the workspace's current HEAD."""
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
    return X_train, y_train, X_test, y_test, target


def _dictionary_from_workspace(ctx: Context | None) -> str | None:
    """The dictionary asaree-workspace's ``open_workspace`` left in this cell's
    workspace, or None if there isn't one.

    Preferred over the HTTP path below for the same reason this server reads
    ``state.json`` off disk instead of asking for the data over the wire: the
    workspace directory is already shared with this subprocess, so there's no
    endpoint to reach and no token to hold. ``open_workspace`` writes it there
    on every open, having read it from the database under the run owner's own
    scoping.
    """
    try:
        workspace_id = _workspace_id_from_ctx("", ctx)
    except HeadNotReadyError:
        return None
    root = os.environ.get("ASAREE_DATASET_WORKSPACE_DIR", "./data/workspaces")
    path = Path(root).resolve() / workspace_id / "data_dictionary.json"
    try:
        return path.read_text() if path.is_file() else None
    except OSError:
        return None


def _dictionary_from_api(name: str) -> tuple[str | None, str | None]:
    """Fall back to GET /datasets/by-name — returns (dictionary_json, error).

    Only reachable with ``ASAREE_INTERNAL_MCP_API_KEY`` set to a real user API
    token (the API authenticates by token hash and scopes datasets by owner),
    which is why the workspace file above is the normal route. This stays for
    the direct/notebook case, where a tool is called outside a cell run and
    there's no ambient workspace at all.
    """
    headers: dict[str, str] = {}
    if _ASAREE_MCP_API_KEY:
        headers["X-API-Key"] = _ASAREE_MCP_API_KEY
    try:
        with httpx.Client(base_url=_ASAREE_API_URL, timeout=10.0, headers=headers) as client:
            resp = client.get(f"/api/datasets/by-name/{name}")
    except httpx.RequestError as e:
        return None, f"Could not reach ASAREE API: {e}"

    if resp.status_code == 404:
        return None, f"Dataset '{name}' not found in registry."
    if resp.status_code != 200:
        return None, f"ASAREE API returned {resp.status_code}: {resp.text}"
    dictionary_json = resp.json().get("dictionary_json")
    if not dictionary_json:
        return None, f"Dataset '{name}' has no data dictionary registered."
    return str(dictionary_json), None


@mcp.tool()
def get_data_dictionary(name: str = "", columns: str = "", ctx: Context | None = None) -> str:
    """Fetch the data dictionary for a registered dataset, on demand.

    Args:
        name: Dataset name as registered via POST /api/datasets. Optional —
            resolved from the ambient request _meta when the run has exactly one
            dataset wired, and unused entirely when the open workspace already
            carries a published copy (the usual case).
        columns: Comma-separated column names to return in FULL detail. If empty,
            returns a COMPACT INDEX (name + type + truncated description) so you can
            scan what exists, then call again for the few you need.
    """
    raw = _dictionary_from_workspace(ctx)
    if raw is None:
        # Only the API fallback needs a name at all; resolving lazily keeps the
        # common workspace path from failing over an argument it never reads.
        name = _dataset_name_from_ctx(name, ctx)
        if not name:
            return json.dumps({"error": "name not provided and not resolvable from the run's ambient _meta."})
        raw, error = _dictionary_from_api(name)
        if raw is None:
            return json.dumps({"error": error})
    try:
        ddict = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        return json.dumps({"error": f"Could not parse data dictionary: {e}"})

    cols = ddict.get("columns", []) if isinstance(ddict, dict) else []
    dataset_label = ddict.get("dataset", name) if isinstance(ddict, dict) else name

    requested = [c.strip() for c in columns.split(",") if c.strip()]
    if requested:
        by_name = {c.get("name"): c for c in cols if isinstance(c, dict)}
        found = [by_name[r] for r in requested if r in by_name]
        not_found = [r for r in requested if r not in by_name]
        return json.dumps({"dataset": dataset_label, "columns": found, "not_found": not_found})

    index = []
    for c in cols:
        if not isinstance(c, dict):
            continue
        desc = str(c.get("description", ""))
        index.append(
            {"name": c.get("name"), "type": c.get("type"),
             "description": desc[:80] + ("…" if len(desc) > 80 else "")}
        )
    return json.dumps({"dataset": dataset_label, "n_columns": len(index), "index": index})


@mcp.tool()
def get_feature_distributions(workspace_id: str = "", ctx: Context | None = None) -> str:
    """Distribution statistics (moments/cardinality/missingness) for every training
    feature at the workspace HEAD.

    Args:
        workspace_id: optional; resolved from the ambient request _meta when omitted.
    """
    try:
        wid = _workspace_id_from_ctx(workspace_id, ctx)
        X_train, _, _, _, _ = _read_head(wid)  # noqa: N806
    except HeadNotReadyError as e:
        return json.dumps({"error": str(e)})
    return json.dumps({"features": eda.feature_distributions(X_train)})


@mcp.tool()
def get_dataset_info(workspace_id: str = "", ctx: Context | None = None) -> str:
    """Descriptive statistics and metadata for the workspace HEAD (training fold only).

    Args:
        workspace_id: optional; resolved from the ambient request _meta when omitted.
    """
    try:
        wid = _workspace_id_from_ctx(workspace_id, ctx)
        X_train, y_train, X_test, _, _ = _read_head(wid)  # noqa: N806
    except HeadNotReadyError as e:
        return json.dumps({"error": str(e)})
    info: dict[str, Any] = eda.dataset_info(
        X_train, y_train, int(len(X_test)), list(X_train.columns)
    )
    return json.dumps(info)


@mcp.tool()
def ping() -> str:
    """Health check — returns 'pong' to verify the server is running."""
    return "pong"
