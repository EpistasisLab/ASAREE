"""Add editable names and notes to published versions."""

from alembic import op

from asaree.migrations.guards import drop_column
import sqlalchemy as sa

revision = "2c3d4e5f6071"
down_revision = "1b2c3d4e5f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("protocol_revisions", sa.Column("name", sa.String(120), nullable=True), if_not_exists=True)
    op.add_column("protocol_revisions", sa.Column("note", sa.Text(), nullable=True), if_not_exists=True)


def downgrade() -> None:
    drop_column("protocol_revisions", "note")
    drop_column("protocol_revisions", "name")
