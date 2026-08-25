"""record how a dataset's split was produced, not just its hashes

``registered_datasets`` stored train/test paths and hashes, which identify the
files a split produced but say nothing about the procedure -- whether the
holdout was grouped, how large it was, or which seed would reproduce it.
``quick_split_dataset`` already accepted exactly those parameters and threw
them away once the parquet files were written.

All four columns are nullable, and stay null for every dataset split before
this migration. That's a permanent, valid state rather than something to
backfill: the parameters were never recorded, so there is nothing to recover
them from (the same reasoning models/dataset.py's docstring gives for a
pre-migration ``raw_path``). A manual split gets ``split_method='manual'`` and
nulls for the rest -- ASAREE didn't compute it and has no honest values for
its parameters.

``split_group_column`` holds the column actually grouped on, which is why null
there is meaningful rather than merely unknown: it means the split was
stratified. See services/datasets.py's ``_split``, which now returns the group
column it used so a requested-but-absent column can't be recorded as if it had
taken effect.

Revision ID: c4e8f1a70d92
Revises: b7c2d9e14a35
Create Date: 2026-08-24 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from asaree.migrations.guards import drop_column
from alembic import op

revision: str = 'c4e8f1a70d92'
down_revision: str | None = 'b7c2d9e14a35'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("registered_datasets", sa.Column("split_method", sa.String(length=16), nullable=True), if_not_exists=True)
    op.add_column("registered_datasets", sa.Column("split_group_column", sa.String(length=255), nullable=True), if_not_exists=True)
    op.add_column("registered_datasets", sa.Column("split_test_size", sa.Float(), nullable=True), if_not_exists=True)
    op.add_column("registered_datasets", sa.Column("split_seed", sa.Integer(), nullable=True), if_not_exists=True)


def downgrade() -> None:
    drop_column("registered_datasets", "split_seed")
    drop_column("registered_datasets", "split_test_size")
    drop_column("registered_datasets", "split_group_column")
    drop_column("registered_datasets", "split_method")
