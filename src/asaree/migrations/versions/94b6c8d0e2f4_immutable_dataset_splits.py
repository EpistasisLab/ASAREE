"""Preserve immutable dataset splits and relocatable source/partition artifacts."""

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

from asaree.migrations.guards import create_foreign_key, drop_column, has_column, has_table

revision = "94b6c8d0e2f4"
down_revision = "a4b5c6d7e8f0"
branch_labels = None
depends_on = None

LEGACY_COLUMNS = {
    "train_path": sa.Text(), "test_path": sa.Text(),
    "train_sha256": sa.String(64), "test_sha256": sa.String(64),
    "split_method": sa.String(16), "split_group_column": sa.String(255),
    "split_test_size": sa.Float(), "split_seed": sa.Integer(),
}


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "dataset_artifacts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("dataset_id", uuid, sa.ForeignKey("registered_datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("namespace", sa.String(32), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("legacy_path", sa.Text()),
        sa.Column("sha256", sa.String(64)), sa.Column("byte_size", sa.BigInteger()),
        sa.Column("format", sa.String(16), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        if_not_exists=True,
    )
    op.create_index("ix_dataset_artifacts_dataset_id", "dataset_artifacts", ["dataset_id"], if_not_exists=True)
    op.create_table(
        "dataset_splits",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("dataset_id", uuid, sa.ForeignKey("registered_datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_sha256", sa.String(64)),
        sa.Column("train_artifact_id", uuid, sa.ForeignKey("dataset_artifacts.id")),
        sa.Column("test_artifact_id", uuid, sa.ForeignKey("dataset_artifacts.id")),
        sa.Column("target_column", sa.String(255)), sa.Column("method", sa.String(16)),
        sa.Column("strategy", sa.String(32)), sa.Column("group_column", sa.String(255)),
        sa.Column("stratify_column", sa.String(255)), sa.Column("test_size", sa.Float()), sa.Column("seed", sa.Integer()),
        sa.Column("legacy", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("id", "dataset_id", name="uq_dataset_splits_id_dataset"),
        if_not_exists=True,
    )
    op.create_index("ix_dataset_splits_dataset_id", "dataset_splits", ["dataset_id"], if_not_exists=True)
    op.add_column("registered_datasets", sa.Column("raw_artifact_id", uuid), if_not_exists=True)
    op.add_column("registered_datasets", sa.Column("current_split_id", uuid), if_not_exists=True)
    create_foreign_key("fk_dataset_raw_artifact", "registered_datasets", "dataset_artifacts", ["raw_artifact_id"], ["id"])
    create_foreign_key(
        "fk_dataset_current_split", "registered_datasets", "dataset_splits",
        ["current_split_id", "id"], ["id", "dataset_id"],
    )
    if not context.is_offline_mode():
        from pathlib import Path
        from asaree.config import get_settings
        from asaree.migrations.dataset_splits import preserve_legacy_splits

        if has_column("registered_datasets", "train_path"):
            preserve_legacy_splits(op.get_bind(), Path(get_settings().dataset_storage_dir))
    else:
        # File verification cannot be rendered as SQL. Refuse silently losing legacy evidence.
        op.execute("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM registered_datasets WHERE raw_path IS NOT NULL
                       OR train_path IS NOT NULL OR test_path IS NOT NULL) THEN
                RAISE EXCEPTION 'Legacy file preservation requires an online upgrade with dataset storage mounted';
            END IF;
        END $$""")
    for name in LEGACY_COLUMNS:
        drop_column("registered_datasets", name)


def downgrade() -> None:
    for name, type_ in LEGACY_COLUMNS.items():
        op.add_column("registered_datasets", sa.Column(name, type_), if_not_exists=True)
    if not context.is_offline_mode() and has_table("dataset_artifacts"):
        from pathlib import Path
        from asaree.config import get_settings
        from asaree.migrations.dataset_splits import restore_legacy_paths

        restore_legacy_paths(op.get_bind(), Path(get_settings().dataset_storage_dir))
    elif context.is_offline_mode():
        op.execute("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM dataset_artifacts) THEN
                RAISE EXCEPTION 'Artifact path restoration requires an online downgrade';
            END IF;
        END $$""")
    op.drop_constraint("fk_dataset_current_split", "registered_datasets", type_="foreignkey", if_exists=True)
    op.drop_constraint("fk_dataset_raw_artifact", "registered_datasets", type_="foreignkey", if_exists=True)
    drop_column("registered_datasets", "current_split_id")
    drop_column("registered_datasets", "raw_artifact_id")
    op.drop_table("dataset_splits", if_exists=True)
    op.drop_table("dataset_artifacts", if_exists=True)
