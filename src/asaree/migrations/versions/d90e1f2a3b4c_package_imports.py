"""Durable package imports and typed source provenance.

Revision ID: d90e1f2a3b4c
Revises: c89d0e1f2a3b
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "d90e1f2a3b4c"
down_revision = "c89d0e1f2a3b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("package_imports",
        sa.Column("id", pg.UUID(), primary_key=True),
        sa.Column("owner_id", pg.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("preflight", pg.JSONB(), nullable=False),
        sa.Column("progress", pg.JSONB(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("experiment_id", pg.UUID(), sa.ForeignKey("research_experiments.id", ondelete="SET NULL")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("configured_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "request_key", name="uq_package_import_request"))
    op.create_index("ix_package_imports_owner_id", "package_imports", ["owner_id"])
    op.create_index("ix_package_imports_status", "package_imports", ["status"])
    op.create_table("imported_identities",
        sa.Column("import_id", pg.UUID(), sa.ForeignKey("package_imports.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kind", sa.String(40), primary_key=True),
        sa.Column("source_id", sa.Text(), primary_key=True),
        sa.Column("local_id", pg.UUID(), nullable=False),
        sa.Column("provenance", pg.JSONB(), nullable=False))
    op.create_index("ix_imported_identities_local_id", "imported_identities", ["local_id"])


def downgrade():
    op.drop_table("imported_identities")
    op.drop_table("package_imports")
