"""Tests for design revisions -- services.design_revisions plus the part of
services.design_generation that decides when a regenerate supersedes the
current design instead of adding to it.

The bug these exist for: generation used to be purely additive, so a design
that shrank from 6 cells to 2 left all 6 behind -- "0/6 scored" forever, and
"run all cells" launching 6 runs for a 2-cell design. Same real-Postgres,
throwaway-user fixture as tests/test_design_generation.py.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for the FK
from asaree.models.database import dispose_engine, get_session
from asaree.models.experiment import ResearchExperiment
from asaree.models.factorial_cell_result import FactorialCellResult
from asaree.models.user import User
from asaree.services.design_generation import generate_design_cells
from asaree.services.design_revisions import (
    DesignRevisionError,
    delete_revision,
    get_current_revision,
    list_revision_summaries,
    list_revisions,
)
from asaree.services.experiments import create_experiment
from asaree.services.factorial_cells import list_cells, upsert_cell

_TWO_BY_THREE = [
    {"name": "tier", "levels": ["small", "large"]},
    {"name": "effort", "levels": ["low", "medium", "high"]},
]
_TWO_BY_ONE = [
    {"name": "tier", "levels": ["small", "large"]},
    {"name": "effort", "levels": ["low"]},
]
_ONE_BY_ONE = [
    {"name": "tier", "levels": ["small"]},
    {"name": "effort", "levels": ["low"]},
]


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def owner_id() -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        user = User(
            email=f"design-rev-test-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="Design Revision Test User",
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        uid = user.id
    yield uid
    async with get_session() as db:
        db_user = await db.get(User, uid)
        if db_user is not None:
            await db.delete(db_user)


@pytest_asyncio.fixture
async def experiment_id(owner_id: uuid.UUID) -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"design-rev-{uuid.uuid4().hex}", owner_id=owner_id)
        eid = experiment.id
    yield eid
    async with get_session() as db:
        db_experiment = await db.get(ResearchExperiment, eid)
        if db_experiment is not None:
            await db.delete(db_experiment)


async def test_first_generate_creates_revision_one(experiment_id: uuid.UUID) -> None:
    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_ONE)

    async with get_session() as db:
        revisions = await list_revisions(db, experiment_id=experiment_id)
        assert [r.revision for r in revisions] == [1]
        assert revisions[0].superseded_at is None


async def test_shrinking_a_design_leaves_the_old_cells_in_history(experiment_id: uuid.UUID) -> None:
    """The reported bug, end to end: 6 cells then 2 must read as 2, not 6."""
    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_THREE)

    async with get_session() as db:
        assert len(await list_cells(db, experiment_id=experiment_id)) == 6

    async with get_session() as db:
        cells = await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_ONE)
        assert len(cells) == 2

    async with get_session() as db:
        # The current design is 2 cells...
        assert len(await list_cells(db, experiment_id=experiment_id)) == 2
        # ...and the 6 old ones are still on disk, under the superseded revision.
        summaries = await list_revision_summaries(db, experiment_id=experiment_id)
        assert [(s.revision.revision, s.cell_count) for s in summaries] == [(2, 2), (1, 6)]
        assert summaries[0].revision.superseded_at is None
        assert summaries[1].revision.superseded_at is not None


async def test_results_for_surviving_cells_carry_forward(experiment_id: uuid.UUID) -> None:
    async with get_session() as db:
        cells = await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_THREE)
        survivor = next(c for c in cells if c.factor_values == {"tier": "small", "effort": "low"})
        survivor_label = survivor.cell_label
        dropped_label = next(c for c in cells if c.factor_values == {"tier": "small", "effort": "high"}).cell_label
        await upsert_cell(
            db, experiment_id=experiment_id, cell_label=survivor_label, fields={"metric_values": {"roc_auc": 0.9}}
        )
        await upsert_cell(
            db, experiment_id=experiment_id, cell_label=dropped_label, fields={"metric_values": {"roc_auc": 0.1}}
        )

    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_ONE)

    async with get_session() as db:
        current = {c.cell_label: c for c in await list_cells(db, experiment_id=experiment_id)}
        assert current[survivor_label].metric_values == {"roc_auc": 0.9}
        assert dropped_label not in current
        summaries = await list_revision_summaries(db, experiment_id=experiment_id)
        # The carried-forward copy is the new revision's only scored cell;
        # history keeps both originals, the dropped one included -- carrying a
        # result forward copies it, it doesn't move it out of the record.
        assert [(s.revision.revision, s.scored_count) for s in summaries] == [(2, 1), (1, 2)]


async def test_widening_a_design_reuses_the_current_revision(experiment_id: uuid.UUID) -> None:
    """Adding levels orphans nothing, so it must not churn out a revision --
    history is for designs that actually discarded something."""
    async with get_session() as db:
        first = await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_ONE)
        first_ids = {c.id for c in first}

    async with get_session() as db:
        second = await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_THREE)
        assert len(second) == 6
        assert first_ids <= {c.id for c in second}  # same rows, not copies

    async with get_session() as db:
        assert [r.revision for r in await list_revisions(db, experiment_id=experiment_id)] == [1]


async def test_regenerating_an_unchanged_design_reuses_the_current_revision(experiment_id: uuid.UUID) -> None:
    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_ONE)
    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_ONE, randomization_seed=7)

    async with get_session() as db:
        assert [r.revision for r in await list_revisions(db, experiment_id=experiment_id)] == [1]


async def test_upsert_without_a_design_creates_revision_one(experiment_id: uuid.UUID) -> None:
    """The notebook path: cells written directly, generate-design never called.
    They still need a revision to hang off, created on demand."""
    async with get_session() as db:
        cell = await upsert_cell(
            db, experiment_id=experiment_id, cell_label="tier_small", fields={"metric_values": {"roc_auc": 0.5}}
        )
        assert cell.design_revision_id is not None

    async with get_session() as db:
        current = await get_current_revision(db, experiment_id)
        assert current is not None and current.revision == 1
        assert len(await list_cells(db, experiment_id=experiment_id)) == 1


async def test_deleting_a_superseded_revision_cascades_to_its_cells(experiment_id: uuid.UUID) -> None:
    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_THREE)
    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_ONE)

    async with get_session() as db:
        superseded = next(r for r in await list_revisions(db, experiment_id=experiment_id) if r.superseded_at)
        superseded_id = superseded.id
        await delete_revision(db, superseded_id)

    async with get_session() as db:
        assert [r.revision for r in await list_revisions(db, experiment_id=experiment_id)] == [2]
        orphans = [
            c
            for c in (await db.execute(_cells_of(experiment_id))).scalars().all()
            if c.design_revision_id == superseded_id
        ]
        assert orphans == []
        # The current design is untouched by its history being cleared.
        assert len(await list_cells(db, experiment_id=experiment_id)) == 2


async def test_deleting_the_current_revision_is_refused(experiment_id: uuid.UUID) -> None:
    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_ONE)

    async with get_session() as db:
        current = await get_current_revision(db, experiment_id)
        assert current is not None
        with pytest.raises(DesignRevisionError):
            await delete_revision(db, current.id)


async def test_revision_numbers_are_never_reused(experiment_id: uuid.UUID) -> None:
    """Deleting revision 1 must not let the next design call itself 1 again --
    two different designs sharing a label would make the history unreadable."""
    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_THREE)
    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_ONE)
    async with get_session() as db:
        superseded = next(r for r in await list_revisions(db, experiment_id=experiment_id) if r.superseded_at)
        await delete_revision(db, superseded.id)
    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_ONE_BY_ONE)

    async with get_session() as db:
        # 2 shrank into 3 -- never back to 1, even though 1 is now free.
        assert [r.revision for r in await list_revisions(db, experiment_id=experiment_id)] == [3, 2]


def _cells_of(experiment_id: uuid.UUID):  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    return select(FactorialCellResult).where(FactorialCellResult.experiment_id == experiment_id)
