"""rename protocol connector handles: llm -> ai, dataset tool -> resource

A protocol's node/edge graph is one opaque JSONB blob (see models/protocol.py),
so a connector slot's id lives in stored data, not just in code -- renaming one
means rewriting every saved graph's edges.

Two renames land here:

  * ``llm`` -> ``ai``, on every edge, after the connector's caption was
    renamed to "AI".
  * ``tool`` -> ``resource``, but ONLY on edges whose source node is a
    ``dataset`` -- Dataset used to share the Tool slot with mcp_tool/Script,
    which both keep using ``tool``. Hence the EXISTS check rather than a blanket
    swap.

Both ``sourceHandle`` and ``targetHandle`` carry the slot id (the canvas writes
the same value into both), and each is rewritten independently so an edge that
somehow has only one of them set doesn't get the other invented.

This is belt-and-braces, not the only line of defence: services/
protocol_execution.py still resolves the old spellings (_LEGACY_AI_HANDLES /
_LEGACY_DATASET_HANDLES) and the canvas rewrites any graph it opens
(migrateLegacyHandles in ProtocolCanvas.tsx). That combination is what makes the
deploy order-independent -- this migration makes the data canonical, the shims
cover the window where an already-loaded browser tab is still autosaving the old
spelling.

Revision ID: 3f1a7c9b2e04
Revises: d4e5f6a7b8c9
Create Date: 2026-08-24 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '3f1a7c9b2e04'
down_revision: str | None = 'd4e5f6a7b8c9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# jsonb_agg over an empty array returns NULL, which would blank the edges list
# outright -- every statement below is guarded on there being something to
# rewrite, so a graph with no edges (or no `edges` key at all, e.g. a protocol
# created but never opened) is skipped rather than mangled.
_RENAME_EDGE_HANDLE = """
UPDATE protocols p
SET graph = jsonb_set(
        p.graph,
        '{{edges}}',
        (
            SELECT jsonb_agg(
                CASE WHEN e->>'{field}' = '{old}'{extra_when}
                     THEN jsonb_set(e, '{{{field}}}', '"{new}"')
                     ELSE e
                END
                ORDER BY ord
            )
            FROM jsonb_array_elements(p.graph->'edges') WITH ORDINALITY AS t(e, ord)
        )
    )
WHERE jsonb_typeof(p.graph->'edges') = 'array'
  AND jsonb_array_length(p.graph->'edges') > 0
  AND p.graph->'edges' @> '[{{"{field}": "{old}"}}]'
"""

# Only a dataset-sourced Tool edge moves to the Resource slot -- an mcp_tool or
# script source keeps its Tool handle. Correlated back to the same row's own
# node list, since an edge only names its source by id.
_DATASET_SOURCE_ONLY = """
                 AND EXISTS (
                     SELECT 1
                     FROM jsonb_array_elements(p.graph->'nodes') AS n
                     WHERE n->>'id' = e->>'source' AND n->>'type' = 'dataset'
                 )"""


def _rename(field: str, old: str, new: str, extra_when: str = "") -> None:
    op.execute(_RENAME_EDGE_HANDLE.format(field=field, old=old, new=new, extra_when=extra_when))


def upgrade() -> None:
    for field in ("targetHandle", "sourceHandle"):
        _rename(field, "llm", "ai")
        _rename(field, "tool", "resource", extra_when=_DATASET_SOURCE_ONLY)


def downgrade() -> None:
    # Exact inverse: "resource" is only ever a dataset edge, so unlike the
    # upgrade direction it needs no source-type check to move back to "tool".
    for field in ("targetHandle", "sourceHandle"):
        _rename(field, "ai", "llm")
        _rename(field, "resource", "tool")
