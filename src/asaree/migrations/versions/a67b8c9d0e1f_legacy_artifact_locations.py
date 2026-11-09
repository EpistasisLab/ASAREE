"""Retain unavailable legacy locations on installations already at the split revision."""

import sqlalchemy as sa
from alembic import op

revision = "a67b8c9d0e1f"
down_revision = "94b6c8d0e2f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dataset_artifacts", sa.Column("legacy_path", sa.Text()), if_not_exists=True)


def downgrade() -> None:
    # The corrected parent migration also creates this evidence column. Keep
    # it for the parent's online rollback to restore original unavailable paths.
    pass
