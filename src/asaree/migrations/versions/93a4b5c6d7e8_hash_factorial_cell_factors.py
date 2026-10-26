"""Index factor digests so long prompt levels fit in the unique B-tree.

Revision ID: 93a4b5c6d7e8
Revises: 8293a4b5c6d7
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "93a4b5c6d7e8"
down_revision: str | None = "8293a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Rebuild even if this revision is replayed after a lost stamp. Alembic's
    # PostgreSQL DDL transaction keeps the replacement atomic.
    op.drop_index("uq_factorial_cells_revision_factors", table_name="factorial_cells", if_exists=True)
    op.create_index(
        "uq_factorial_cells_revision_factors",
        "factorial_cells",
        ["design_revision_id", sa.text("md5(factor_values::text)")],
        unique=True,
        postgresql_where=sa.text("factor_values IS NOT NULL AND factor_values <> '{}'::jsonb"),
    )


def downgrade() -> None:
    # Restoring the old index requires removing any oversized factors first;
    # otherwise PostgreSQL refuses it and rolls this transaction back.
    op.drop_index("uq_factorial_cells_revision_factors", table_name="factorial_cells", if_exists=True)
    op.create_index(
        "uq_factorial_cells_revision_factors",
        "factorial_cells",
        ["design_revision_id", "factor_values"],
        unique=True,
        postgresql_where=sa.text("factor_values IS NOT NULL AND factor_values <> '{}'::jsonb"),
    )
