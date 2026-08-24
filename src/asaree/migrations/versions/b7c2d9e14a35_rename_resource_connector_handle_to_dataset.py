"""rename the protocol connector handle "resource" -> "dataset"

A protocol's node/edge graph is one opaque JSONB blob (see models/protocol.py),
so a connector slot's id lives in stored data, not just in code -- renaming one
means rewriting every saved graph's edges. Same shape as 3f1a7c9b2e04, which
introduced the ``resource`` spelling this one retires.

The slot's only member is the Dataset node, so naming it "Resource" bought
nothing but a second word for the same thing; it is now named after the node
type, and sits next to Skill on the agent card instead of at the far right.

Unlike 3f1a7c9b2e04's ``tool`` -> ``resource`` half, this rename is TOTAL: no
other slot has ever used the ``resource`` handle, so every such edge moves and
no source-node check is needed in either direction.

Both ``sourceHandle`` and ``targetHandle`` carry the slot id (the canvas writes
the same value into both), and each is rewritten independently so an edge that
somehow has only one of them set doesn't get the other invented.

Belt-and-braces, as before: services/protocol_execution.py still resolves the
old spelling (_LEGACY_DATASET_HANDLES) and the canvas rewrites any graph it
opens (migrateLegacyHandles in ProtocolCanvas.tsx), which together make the
deploy order-independent -- this migration makes the data canonical, the shims
cover the window where an already-loaded browser tab is still autosaving the
old spelling.

Revision ID: b7c2d9e14a35
Revises: 3f1a7c9b2e04
Create Date: 2026-08-24 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'b7c2d9e14a35'
down_revision: str | None = '3f1a7c9b2e04'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Verbatim from 3f1a7c9b2e04 (minus its dataset-source EXISTS clause, which
# this total rename doesn't need) rather than imported from it: a migration is
# a historical record of one statement run against one schema, so it stays
# readable and re-runnable on its own even if that file is later squashed away.
#
# jsonb_agg over an empty array returns NULL, which would blank the edges list
# outright -- both statements are guarded on there being something to rewrite,
# so a graph with no edges (or no `edges` key at all) is skipped, not mangled.
_RENAME_EDGE_HANDLE = """
UPDATE protocols p
SET graph = jsonb_set(
        p.graph,
        '{{edges}}',
        (
            SELECT jsonb_agg(
                CASE WHEN e->>'{field}' = '{old}'
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


def _rename(field: str, old: str, new: str) -> None:
    op.execute(_RENAME_EDGE_HANDLE.format(field=field, old=old, new=new))


def upgrade() -> None:
    for field in ("targetHandle", "sourceHandle"):
        _rename(field, "resource", "dataset")


def downgrade() -> None:
    for field in ("targetHandle", "sourceHandle"):
        _rename(field, "dataset", "resource")
