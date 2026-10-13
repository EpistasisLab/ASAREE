"""Durable result commands and restart-safe per-slot planning checkpoints."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "5f60718293a4"
down_revision = "4e5f60718293"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_protocol_runs_legacy_result_slot ON protocol_runs "
        "(protocol_id, protocol_revision_id, design_revision_id, replicate_label, created_at DESC, id DESC) "
        "WHERE replicate_result_id IS NULL AND row_result_id IS NULL")
    op.create_table(
        "result_jobs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "experiment_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("research_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "protocol_id", pg.UUID(as_uuid=True), sa.ForeignKey("protocols.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "protocol_revision_id", pg.UUID(as_uuid=True), sa.ForeignKey("protocol_revisions.id", ondelete="CASCADE")
        ),
        sa.Column(
            "design_revision_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("experiment_design_revisions.id", ondelete="CASCADE"),
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("params", pg.JSONB, nullable=False),
        sa.Column("progress", pg.JSONB, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "request_key"), if_not_exists=True)
    op.create_index("ix_result_jobs_pending", "result_jobs", ["status", "created_at"], if_not_exists=True)
    op.create_table(
        "result_job_members",
        sa.Column(
            "job_id", pg.UUID(as_uuid=True), sa.ForeignKey("result_jobs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "replicate_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("factorial_replicate_results.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("row_index", sa.BigInteger, primary_key=True),
        sa.Column("slot_id", pg.UUID(as_uuid=True)),
        sa.Column("expected_run_id", pg.UUID(as_uuid=True)),
        sa.Column("processed", sa.Boolean, nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("protocol_runs.id", ondelete="SET NULL")),
        sa.Column("delivered", sa.Boolean, nullable=False), if_not_exists=True)
    op.create_index(
        "ix_result_job_members_pending", "result_job_members", ["job_id", "processed", "replicate_id", "row_index"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_protocol_runs_legacy_result_slot", table_name="protocol_runs", if_exists=True)
    op.drop_table("result_job_members", if_exists=True)
    op.drop_table("result_jobs", if_exists=True)
