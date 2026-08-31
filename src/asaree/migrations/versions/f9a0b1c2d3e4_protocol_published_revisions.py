"""add immutable published protocol revisions

Protocol.graph remains an autosaved draft.  Existing protocols are backfilled
as revision 1 and pointed at it so deployment is backwards-compatible; future
production runs always pin an explicit published revision.

Revision ID: f9a0b1c2d3e4
Revises: e2f7c4a91b60
Create Date: 2026-08-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from asaree.migrations.guards import create_foreign_key, drop_column

revision: str = "f9a0b1c2d3e4"
down_revision: str | None = "e2f7c4a91b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "protocol_revisions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("protocol_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("graph", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["protocol_id"], ["protocols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("protocol_id", "revision", name="uq_protocol_revisions_protocol_revision"),
        if_not_exists=True,
    )
    op.create_index("ix_protocol_revisions_protocol_id", "protocol_revisions", ["protocol_id"], if_not_exists=True)
    op.add_column("protocols", sa.Column("published_revision_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True), if_not_exists=True)
    create_foreign_key(
        "protocols_published_revision_id_fkey", "protocols", "protocol_revisions", ["published_revision_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_protocols_published_revision_id", "protocols", ["published_revision_id"], if_not_exists=True)
    op.execute(
        """
        INSERT INTO protocol_revisions (id, protocol_id, revision, graph, published_at)
        SELECT gen_random_uuid(), p.id, 1, p.graph, now()
        FROM protocols AS p
        ON CONFLICT (protocol_id, revision) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE protocols AS p
        SET published_revision_id = r.id
        FROM protocol_revisions AS r
        WHERE r.protocol_id = p.id AND r.revision = 1 AND p.published_revision_id IS NULL
        """
    )
    op.add_column("protocol_runs", sa.Column("protocol_revision_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True), if_not_exists=True)
    create_foreign_key(
        "protocol_runs_protocol_revision_id_fkey", "protocol_runs", "protocol_revisions", ["protocol_revision_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_protocol_runs_protocol_revision_id", "protocol_runs", ["protocol_revision_id"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_protocol_runs_protocol_revision_id", table_name="protocol_runs", if_exists=True)
    drop_column("protocol_runs", "protocol_revision_id")
    op.drop_index("ix_protocols_published_revision_id", table_name="protocols", if_exists=True)
    drop_column("protocols", "published_revision_id")
    op.drop_index("ix_protocol_revisions_protocol_id", table_name="protocol_revisions", if_exists=True)
    op.drop_table("protocol_revisions", if_exists=True)
