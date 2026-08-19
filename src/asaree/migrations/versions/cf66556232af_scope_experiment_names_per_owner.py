"""scope research_experiments.name uniqueness to owner_id

Was unique per installation (ix_research_experiments_name) -- a name is a
namespace shared by every researcher, not by design so much as by never
having been revisited. Two different researchers creating an experiment
with the same, often notebook-hardcoded, title (e.g. a shared use-case's
fixed experiment name) collided with a raw 409 instead of each researcher
getting their own row -- forcing a fresh-per-run title suffix just to work
around the collision.

owner_id here is a real NOT NULL FK (ResearchExperiment.owner_id), unlike
Motoro's opaque owner_id tag, so there's no NULL-owner edge case to
reason about the way there is for Agent/MCPServerConfig.

Revision ID: cf66556232af
Revises: 0eda81db4d05
Create Date: 2026-08-05 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "cf66556232af"
down_revision: str | None = "0eda81db4d05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_research_experiments_name", table_name="research_experiments")
    op.create_index(
        "uq_research_experiments_owner_name", "research_experiments", ["owner_id", "name"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_research_experiments_owner_name", table_name="research_experiments")
    op.create_index("ix_research_experiments_name", "research_experiments", ["name"], unique=True)
