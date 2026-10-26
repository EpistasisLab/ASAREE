"""Repair the factor index on databases already stamped with the digest fix.

Some deployed databases report 93a4b5c6d7e8 but still have the original
JSONB B-tree index. Re-running upgrade at that head does nothing: Alembic
tracks revision stamps, not the live index definition. A new revision is
needed to repair those databases through the normal Compose upgrade path.

Revision ID: a4b5c6d7e8f9
Revises: 93a4b5c6d7e8
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4b5c6d7e8f9"
down_revision: str | None = "93a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Replace the existing index unconditionally; an existence guard would
    # preserve exactly the stale definition this migration needs to repair.
    op.drop_index("uq_factorial_cells_revision_factors", table_name="factorial_cells", if_exists=True)
    op.create_index(
        "uq_factorial_cells_revision_factors",
        "factorial_cells",
        ["design_revision_id", sa.text("md5(factor_values::text)")],
        unique=True,
        postgresql_where=sa.text("factor_values IS NOT NULL AND factor_values <> '{}'::jsonb"),
    )
    # Verify the live definition before Alembic records success, in the same
    # transaction as the index replacement.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_index
                WHERE indexrelid = 'uq_factorial_cells_revision_factors'::regclass
                  AND indisunique AND indisvalid
                  AND position('md5(' in pg_get_indexdef(indexrelid)) > 0
            ) THEN
                RAISE EXCEPTION 'factorial cell factor index must use a unique MD5 digest';
            END IF;
        END $$
    """)


def downgrade() -> None:
    # This only repairs the schema promised by the preceding revision. Its
    # intended index is already the digest index, so there is nothing to undo.
    pass
