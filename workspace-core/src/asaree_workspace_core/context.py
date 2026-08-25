"""Context-driven matrix resolution — the heart of the core extraction (#1456).

The monolith materialized a workspace HEAD into an in-memory ``Session`` under a
``dataset_id`` (``open_workspace``), and every stage/EDA tool then required that
id threaded back in. That per-process session blocks splitting tools across
processes and makes concurrent runs share one mutable store.

Here, a tool resolves "the current dataset" straight from the on-disk workspace
HEAD, keyed only by ``workspace_id`` — which arrives as ambient run context in
the MCP request ``_meta`` channel (issue #1455). No ``dataset_id``, no session.

This module is deliberately MCP-free: it takes ``workspace_id`` (or a plain meta
mapping) as arguments. The thin server wrappers (issue #1457) read
``ctx.request_context.meta`` and hand the value here, so ``mcp`` never becomes a
dependency of the shared computation core.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from .workspace import Workspace, WorkspaceError

# Namespaced key Motoro injects into the MCP request ``_meta`` (mirrors
# ``META_KEY_WORKSPACE_ID`` in ``motoro.mcp.adapters`` exactly — this
# string, not an import, so this module stays MCP-free per the docstring
# above). Namespaced so it never collides with transport meta (e.g.
# ``progressToken``). Was "ares.workspace_id" from the ARES-era migration;
# fixed to match what Motoro actually emits — the mismatch meant no
# tool call ever found its ambient workspace_id at all.
META_KEY_WORKSPACE_ID = "motoro.workspace_id"


def workspace_id_from_meta(meta: Mapping[str, Any] | None) -> str:
    """Read ``workspace_id`` from an ambient ``_meta`` mapping, or "" if absent.

    *meta* is the plain key/value view of the MCP request ``_meta`` (a server
    wrapper passes ``ctx.request_context.meta.model_extra``). Kept as a bare
    ``Mapping`` so the core stays free of any MCP import.
    """
    if not meta:
        return ""
    value = meta.get(META_KEY_WORKSPACE_ID)
    return value if isinstance(value, str) else ""


def meta_mapping_from_ctx(ctx: Any) -> Mapping[str, Any] | None:
    """Extract the request ``_meta`` key/value mapping from a FastMCP ``Context``.

    Duck-typed on purpose — reads ``ctx.request_context.meta.model_extra`` via
    ``getattr`` so the core never imports ``mcp``. Returns ``None`` when there is
    no context or no ambient meta (e.g. a tool-less or non-workspace call).
    """
    if ctx is None:
        return None
    try:
        request_context = getattr(ctx, "request_context", None)
    except Exception:  # noqa: BLE001 — request_context may raise outside a request
        return None
    if request_context is None:
        return None
    meta = getattr(request_context, "meta", None)
    if meta is None:
        return None
    return getattr(meta, "model_extra", None) or {}


def resolve_workspace_id_from_ctx(
    explicit: str, ctx: Any, *, required: bool = True
) -> str:
    """Convenience: resolve the workspace id from an explicit arg + a FastMCP ctx.

    The single call a server tool makes — combines :func:`meta_mapping_from_ctx`
    and :func:`resolve_workspace_id` so wrappers stay one line.
    """
    return resolve_workspace_id(
        explicit, meta_mapping_from_ctx(ctx), required=required
    )


# Mirrors META_KEY_OWNER_ID in motoro.mcp.adapters exactly (see the
# workspace_id key above for why this is a literal string, not an import).
META_KEY_OWNER_ID = "motoro.owner_id"


def owner_id_from_meta(meta: Mapping[str, Any] | None) -> str:
    """Read the run's owner id from an ambient ``_meta`` mapping, or "" if absent."""
    if not meta:
        return ""
    value = meta.get(META_KEY_OWNER_ID)
    return value if isinstance(value, str) else ""


def resolve_owner_id_from_ctx(ctx: Any, *, required: bool = False) -> str:
    """Convenience: resolve the run's owner id from a FastMCP ctx's ambient meta.

    Unlike workspace_id, not every tool call needs owner scoping (only ones
    that check a resource's ownership, e.g. a shared workspace-management
    tool reading a registered dataset) — so this defaults to optional.
    """
    owner_id = owner_id_from_meta(meta_mapping_from_ctx(ctx))
    if not owner_id and required:
        raise WorkspaceError(
            "owner_id missing from request _meta "
            f"({META_KEY_OWNER_ID!r}) but this operation requires it"
        )
    return owner_id


# Motoro's open extension point for a *product's* own ambient references
# (``META_AMBIENT_PREFIX`` in ``motoro.mcp.adapters``), as opposed to the run
# identity keys above that core itself owns. ASAREE puts the names of the
# datasets wired into the protocol here, so ``open_workspace`` — the one
# workspace tool that needs to know *which* dataset, and so the one the
# workspace_id above cannot fully serve — can resolve it without the model
# retyping a name it read out of its prompt.
META_KEY_DATASET_NAMES = "motoro.ambient.dataset_names"


def dataset_names_from_meta(meta: Mapping[str, Any] | None) -> list[str]:
    """Read the run's wired dataset names from an ambient ``_meta`` mapping.

    Returns ``[]`` when absent or malformed — never raises, because a run with
    no datasets wired is an ordinary, expected state.
    """
    if not meta:
        return []
    value = meta.get(META_KEY_DATASET_NAMES)
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and v]


def resolve_dataset_name(explicit: str, meta: Mapping[str, Any] | None) -> str:
    """Resolve which registered dataset a call means (issue #1455 follow-on).

    Precedence matches :func:`resolve_workspace_id`: an explicit argument wins,
    otherwise the ambient value. The ambient fallback applies *only when exactly
    one* dataset is wired into the run — with several, there is a real choice to
    make and silently picking the first would be a guess dressed up as context.
    In that case this returns "" and the caller reports the candidates so the
    model can choose deliberately.
    """
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    names = dataset_names_from_meta(meta)
    return names[0] if len(names) == 1 else ""


def resolve_dataset_name_from_ctx(explicit: str, ctx: Any) -> tuple[str, list[str]]:
    """Convenience: :func:`resolve_dataset_name` against a FastMCP ctx's meta.

    Returns ``(resolved_name, ambient_candidates)`` — the candidates let a
    caller name the actual options in its error rather than a bare "missing".
    """
    meta = meta_mapping_from_ctx(ctx)
    return resolve_dataset_name(explicit, meta), dataset_names_from_meta(meta)


def resolve_workspace_id(
    explicit: str,
    meta: Mapping[str, Any] | None,
    *,
    required: bool = True,
) -> str:
    """Resolve the effective workspace id (issue #1455 / #1456).

    Precedence: an explicit argument wins (backward compatible), otherwise the
    ambient value from the request ``_meta``. When *required* and neither source
    supplies one, fail loud rather than silently operate on the wrong workspace.
    """
    resolved = (explicit or "").strip() or workspace_id_from_meta(meta)
    if not resolved and required:
        raise WorkspaceError(
            "workspace_id missing: pass it explicitly or via request _meta key "
            f"{META_KEY_WORKSPACE_ID!r}"
        )
    return resolved


def resolve_matrix_from_head(
    workspace_id: str,
    *,
    root: str | None = None,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    """Load ``(X_train, y_train, X_test, y_test)`` from the workspace HEAD.

    HEAD is the last accepted stage output — the current model-ready matrix.
    This is the ``dataset_id``-free replacement for
    ``get_session().get_dataset(dataset_id)``: the only key is the ambient
    ``workspace_id``. Raises :class:`WorkspaceError` when the workspace is
    absent or malformed, so a missing context can never resolve to wrong data.
    """
    ws = Workspace(workspace_id, root=root)
    if not ws.exists():
        raise WorkspaceError(f"workspace {workspace_id!r} not initialized")
    return ws.read_head()


def resolve_stage_input(
    workspace_id: str,
    stage: str,
    *,
    root: str | None = None,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    """Load the ``(X_train, y_train, X_test, y_test)`` a stage should start from.

    A stage reads the prior accepted stage's output (or the ``v0_raw`` seed for
    the first stage) — the input side of the leakage-safe staged handoff.
    """
    ws = Workspace(workspace_id, root=root)
    if not ws.exists():
        raise WorkspaceError(f"workspace {workspace_id!r} not initialized")
    return ws.read_stage_input(stage)


def resolve_stage_working(
    workspace_id: str,
    stage: str,
    *,
    root: str | None = None,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    """Load a stage's working matrix — its committed (unaccepted) version if
    present, else the stage input.

    This is how the tools of a multi-tool stage chain without an in-memory
    session: the second DC tool (``drop_and_impute``) reads the first's committed
    ``v1_dc``; the second FTE tool (``fit_preprocessor``) reads ``build_features``'
    committed ``v2_fte``. See :meth:`Workspace.read_stage_working`.
    """
    ws = Workspace(workspace_id, root=root)
    if not ws.exists():
        raise WorkspaceError(f"workspace {workspace_id!r} not initialized")
    return ws.read_stage_working(stage)
