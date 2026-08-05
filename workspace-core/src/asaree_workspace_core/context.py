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

# Namespaced key agentic-core injects into the MCP request ``_meta`` (mirrors
# ``META_KEY_WORKSPACE_ID`` in ``agentic_core.mcp.adapters`` exactly — this
# string, not an import, so this module stays MCP-free per the docstring
# above). Namespaced so it never collides with transport meta (e.g.
# ``progressToken``). Was "ares.workspace_id" from the ARES-era migration;
# fixed to match what agentic-core actually emits — the mismatch meant no
# tool call ever found its ambient workspace_id at all.
META_KEY_WORKSPACE_ID = "agentic_core.workspace_id"


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
