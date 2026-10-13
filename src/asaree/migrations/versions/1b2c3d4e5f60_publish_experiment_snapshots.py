"""Freeze experiment settings and generated design with published canvases."""

from alembic import op

from asaree.migrations.guards import create_foreign_key, drop_column
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "1b2c3d4e5f60"
down_revision = "0a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("protocol_revisions", sa.Column("experiment_snapshot", postgresql.JSONB(), nullable=True), if_not_exists=True)
    op.add_column("protocol_revisions", sa.Column("design_revision_id", postgresql.UUID(as_uuid=True), nullable=True), if_not_exists=True)
    create_foreign_key(
        "fk_protocol_revisions_design", "protocol_revisions", "experiment_design_revisions",
        ["design_revision_id"], ["id"], deferrable=True, initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint("fk_protocol_revisions_design", "protocol_revisions", type_="foreignkey", if_exists=True)
    drop_column("protocol_revisions", "design_revision_id")
    drop_column("protocol_revisions", "experiment_snapshot")
