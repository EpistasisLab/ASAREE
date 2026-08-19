"""asaree-workspace-core — the on-disk versioned dataset workspace, no ML-domain logic.

Split out of what was ares-sklearn-core's workspace.py/staging.py/context.py/
provenance.py (see spinal-surgery-sklearn-servers). Owned by ASAREE because the
workspace and the registered dataset it seeds from are both ASAREE concepts;
depended on externally by any domain-specific MCP server (e.g.
ares-sklearn-core's dc/fte/fs/eda/model) that reads/writes staged data through
the same on-disk format, the same way ASAREE itself depends on Motoro.
"""

from __future__ import annotations

from . import provenance, staging
from .context import (
    META_KEY_OWNER_ID,
    META_KEY_WORKSPACE_ID,
    meta_mapping_from_ctx,
    owner_id_from_meta,
    resolve_matrix_from_head,
    resolve_owner_id_from_ctx,
    resolve_stage_input,
    resolve_stage_working,
    resolve_workspace_id,
    resolve_workspace_id_from_ctx,
    workspace_id_from_meta,
)
from .workspace import (
    SEED_VERSION,
    STAGE_VERSION,
    STAGES,
    Workspace,
    WorkspaceError,
    make_workspace_id,
)

__all__ = [
    "provenance",
    "staging",
    "META_KEY_OWNER_ID",
    "META_KEY_WORKSPACE_ID",
    "meta_mapping_from_ctx",
    "owner_id_from_meta",
    "resolve_matrix_from_head",
    "resolve_owner_id_from_ctx",
    "resolve_stage_input",
    "resolve_stage_working",
    "resolve_workspace_id",
    "resolve_workspace_id_from_ctx",
    "workspace_id_from_meta",
    "SEED_VERSION",
    "STAGE_VERSION",
    "STAGES",
    "Workspace",
    "WorkspaceError",
    "make_workspace_id",
]
