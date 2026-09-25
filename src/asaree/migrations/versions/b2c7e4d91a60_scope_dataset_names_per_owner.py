"""scope registered dataset names per owner

Revision ID: b2c7e4d91a60
Revises: a6c4e2f8190b
Create Date: 2026-09-23 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c7e4d91a60"
down_revision: str | None = "a6c4e2f8190b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_registered_datasets_name", table_name="registered_datasets", if_exists=True)
    op.create_index(
        "uq_registered_datasets_owner_name",
        "registered_datasets",
        ["owner_id", "name"],
        unique=True,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM registered_datasets
                    GROUP BY name
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'cannot downgrade dataset name scoping: duplicate names exist across owners';
                END IF;
            END
            $$
            """
        )
    )
    op.drop_index("uq_registered_datasets_owner_name", table_name="registered_datasets", if_exists=True)
    op.create_index(
        "ix_registered_datasets_name",
        "registered_datasets",
        ["name"],
        unique=True,
        if_not_exists=True,
    )
