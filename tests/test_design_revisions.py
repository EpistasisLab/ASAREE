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
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.user import User
from asaree.services.design_generation import generate_design_cells, get_design_impact
from asaree.services.design_revisions import (
    DesignRevisionError,
    delete_revision,
    get_current_revision,
    list_revision_summaries,
    list_revisions,
)
from asaree.services.experiments import create_experiment
from asaree.services.factorial_cells import list_replicates, upsert_replicate

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
        assert len(await list_replicates(db, experiment_id=experiment_id)) == 6

    async with get_session() as db:
        cells = await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_ONE)
        assert len(cells) == 2

    async with get_session() as db:
        # The current design is 2 cells...
        assert len(await list_replicates(db, experiment_id=experiment_id)) == 2
        # ...and the 6 old ones are still on disk, under the superseded revision.
        summaries = await list_revision_summaries(db, experiment_id=experiment_id)
        assert [(s.revision.revision, s.cell_count) for s in summaries] == [(2, 2), (1, 6)]
        assert summaries[0].revision.superseded_at is None
        assert summaries[1].revision.superseded_at is not None


async def test_results_for_surviving_cells_carry_forward(experiment_id: uuid.UUID) -> None:
    async with get_session() as db:
        cells = await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_THREE)
        survivor = next(c for c in cells if c.factor_values == {"tier": "small", "effort": "low"})
        survivor_label = survivor.replicate_label
        dropped_label = next(
            replicate.replicate_label
            for replicate in cells
            if replicate.factor_values == {"tier": "small", "effort": "high"}
        )
        await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label=survivor_label,
            fields={"metric_values": {"roc_auc": 0.9}},
        )
        await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label=dropped_label,
            fields={"metric_values": {"roc_auc": 0.1}},
        )

    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_ONE)

    async with get_session() as db:
        current = {
            replicate.replicate_label: replicate
            for replicate in await list_replicates(db, experiment_id=experiment_id)
        }
        assert current[survivor_label].metric_values == {"roc_auc": 0.9}
        assert dropped_label not in current
        summaries = await list_revision_summaries(db, experiment_id=experiment_id)
        # The carried-forward copy is the new revision's only scored cell;
        # history keeps both originals, the dropped one included -- carrying a
        # result forward copies it, it doesn't move it out of the record.
        assert [(s.revision.revision, s.scored_replicate_count) for s in summaries] == [(2, 1), (1, 2)]


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


async def test_design_impact_previews_an_expansion_before_regeneration(experiment_id: uuid.UUID) -> None:
    async with get_session() as db:
        await generate_design_cells(
            db, experiment_id=experiment_id, factors=_TWO_BY_ONE, design_spec={"factors": _TWO_BY_ONE}
        )

    async with get_session() as db:
        impact = await get_design_impact(
            db, experiment_id=experiment_id, design_spec={"factors": _TWO_BY_THREE, "replicates": 1}
        )

    assert impact.has_generated_design is True
    assert impact.regeneration_required is True
    assert (impact.current_cell_count, impact.proposed_cell_count) == (2, 6)
    assert (impact.added_cell_count, impact.retained_cell_count, impact.removed_cell_count) == (4, 2, 0)
    assert (impact.current_replicate_count, impact.proposed_replicate_count) == (2, 6)
    assert (impact.added_replicate_count, impact.retained_replicate_count, impact.removed_replicate_count) == (4, 2, 0)


async def test_cells_without_a_declared_design_never_require_regeneration(experiment_id: uuid.UUID) -> None:
    """The notebook/SDK flow: cells PUT straight through upsert_replicate onto
    the revision get_or_create_current opens for them, with no design_spec ever
    declared (see services/design_revisions.py's module docstring).

    Nothing is planned, so there are no labels to compare against and no design
    to regenerate. Reporting regeneration_required here permanently 422'd
    "run all cells" on every notebook-driven experiment."""
    async with get_session() as db:
        for label in ("cell-1", "cell-2"):
            await upsert_replicate(db, experiment_id=experiment_id, replicate_label=label, fields={"factor_values": {}})

    async with get_session() as db:
        impact = await get_design_impact(db, experiment_id=experiment_id, design_spec=None)

    assert impact.regeneration_required is False
    assert (impact.current_replicate_count, impact.proposed_replicate_count) == (2, 0)


async def test_removing_the_final_factor_without_regenerating_still_requires_regeneration(
    experiment_id: uuid.UUID,
) -> None:
    """The case the empty-factors allowance must not swallow: a design that
    *had* factors and no longer declares them has genuinely drifted from its
    materialized cells, and the revision's own recorded spec is what proves it."""
    async with get_session() as db:
        await generate_design_cells(
            db, experiment_id=experiment_id, factors=_TWO_BY_ONE, design_spec={"factors": _TWO_BY_ONE}
        )

    async with get_session() as db:
        impact = await get_design_impact(db, experiment_id=experiment_id, design_spec={"factors": []})

    assert impact.regeneration_required is True
    assert (impact.current_replicate_count, impact.proposed_replicate_count) == (2, 0)


async def test_design_counts_cells_separately_from_replicates(experiment_id: uuid.UUID) -> None:
    async with get_session() as db:
        await generate_design_cells(
            db,
            experiment_id=experiment_id,
            factors=_TWO_BY_ONE,
            replicates=3,
            design_spec={"factors": _TWO_BY_ONE, "replicates": 3},
        )

    async with get_session() as db:
        impact = await get_design_impact(
            db,
            experiment_id=experiment_id,
            design_spec={"factors": _TWO_BY_ONE, "replicates": 3},
        )
        summary = (await list_revision_summaries(db, experiment_id=experiment_id))[0]

    assert impact.current_cell_count == impact.proposed_cell_count == 2
    assert impact.current_replicate_count == impact.proposed_replicate_count == 6
    assert summary.cell_count == 2
    assert summary.replicate_count == 6


async def test_removing_the_final_factor_retires_every_current_cell(experiment_id: uuid.UUID) -> None:
    async with get_session() as db:
        await generate_design_cells(
            db, experiment_id=experiment_id, factors=_TWO_BY_ONE, design_spec={"factors": _TWO_BY_ONE}
        )

    async with get_session() as db:
        cleared = await generate_design_cells(db, experiment_id=experiment_id, factors=[], design_spec={"factors": []})
        assert cleared == []

    async with get_session() as db:
        assert await list_replicates(db, experiment_id=experiment_id) == []
        summaries = await list_revision_summaries(db, experiment_id=experiment_id)
        assert [(summary.revision.revision, summary.cell_count) for summary in summaries] == [(2, 0), (1, 2)]


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
        replicate = await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="tier_small",
            fields={"metric_values": {"roc_auc": 0.5}},
        )
        assert replicate.design_revision_id is not None

    async with get_session() as db:
        current = await get_current_revision(db, experiment_id)
        assert current is not None and current.revision == 1
        assert len(await list_replicates(db, experiment_id=experiment_id)) == 1


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
        assert len(await list_replicates(db, experiment_id=experiment_id)) == 2


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


def _spec(factors: list[dict], slug: str | None = None) -> dict:
    spec: dict = {"factors": factors}
    if slug is not None:
        spec["coordination_strategy"] = {"slug": slug, "params": {}}
    return spec


async def test_changing_the_coordination_strategy_supersedes_the_revision(experiment_id: uuid.UUID) -> None:
    """A strategy change drops no cell, so nothing else in the supersede
    condition catches it -- and merging the new cells in beside the old ones
    would leave one results table holding two incomparable execution models."""
    async with get_session() as db:
        await generate_design_cells(
            db, experiment_id=experiment_id, factors=_TWO_BY_ONE, design_spec=_spec(_TWO_BY_ONE, "sequential")
        )

    async with get_session() as db:
        cells = await generate_design_cells(
            db, experiment_id=experiment_id, factors=_TWO_BY_ONE, design_spec=_spec(_TWO_BY_ONE, "critic_gate")
        )
        assert len(cells) == 2

    async with get_session() as db:
        summaries = await list_revision_summaries(db, experiment_id=experiment_id)
        assert [(s.revision.revision, s.cell_count) for s in summaries] == [(2, 2), (1, 2)]


async def test_a_strategy_change_does_not_carry_results_forward(experiment_id: uuid.UUID) -> None:
    """Unlike a shrunk design, where a surviving label's result is as valid as
    it was. Same label, different execution semantics, different observation --
    so re-running it is the point, not a cost to be avoided."""
    async with get_session() as db:
        cells = await generate_design_cells(
            db, experiment_id=experiment_id, factors=_TWO_BY_ONE, design_spec=_spec(_TWO_BY_ONE, "sequential")
        )
        scored_label = cells[0].replicate_label
        await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label=scored_label,
            fields={"metric_values": {"roc_auc": 0.9}},
        )

    async with get_session() as db:
        await generate_design_cells(
            db, experiment_id=experiment_id, factors=_TWO_BY_ONE, design_spec=_spec(_TWO_BY_ONE, "peer_collaboration")
        )

    async with get_session() as db:
        current = {r.replicate_label: r for r in await list_replicates(db, experiment_id=experiment_id)}
        assert current[scored_label].metric_values in (None, {})
        # The original is still readable under the strategy that produced it.
        summaries = await list_revision_summaries(db, experiment_id=experiment_id)
        assert [(s.revision.revision, s.scored_replicate_count) for s in summaries] == [(2, 0), (1, 1)]


async def test_design_impact_reports_a_strategy_change_as_the_reason(experiment_id: uuid.UUID) -> None:
    """The one regeneration reason the counts cannot explain: it adds and
    removes nothing, so without a reason the tab would show "no change" beside
    a banner demanding an update."""
    async with get_session() as db:
        await generate_design_cells(
            db, experiment_id=experiment_id, factors=_TWO_BY_ONE, design_spec=_spec(_TWO_BY_ONE, "sequential")
        )

    async with get_session() as db:
        impact = await get_design_impact(
            db, experiment_id=experiment_id, design_spec=_spec(_TWO_BY_ONE, "critic_gate")
        )
        assert impact.regeneration_required is True
        assert impact.regeneration_reasons == ("coordination_strategy_changed",)
        assert (impact.added_replicate_count, impact.removed_replicate_count) == (0, 0)


async def test_design_impact_reports_a_stage_plan_change_as_the_reason(experiment_id: uuid.UUID) -> None:
    """Same shape as the strategy change and for the same reason: a different
    staged pipeline adds and removes no cell, but cells staged through two
    different pipelines have different artifacts and are not comparable."""
    spec = _spec(_TWO_BY_ONE, "sequential")
    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_ONE, design_spec=spec)

    async with get_session() as db:
        impact = await get_design_impact(
            db,
            experiment_id=experiment_id,
            design_spec={
                **spec,
                "stage_plan": {"name": "custom", "stages": [{"id": "ingest", "label": "Ingest"}]},
            },
        )
        assert impact.regeneration_required is True
        assert impact.regeneration_reasons == ("stage_plan_changed",)
        assert (impact.added_replicate_count, impact.removed_replicate_count) == (0, 0)


async def test_an_absent_stage_plan_matches_the_preset_by_any_name(experiment_id: uuid.UUID) -> None:
    """Every experiment predating the field has no entry and staged through the
    hardcoded dc/fte/fs triple, which is what the ``tabular_ml`` preset names.
    An inline copy of it is the same declaration too -- if any of these
    compared unequal, opening a legacy experiment's Design tab would demand a
    regeneration it doesn't need."""
    from asaree_workspace_core import TABULAR_ML

    spec = _spec(_TWO_BY_ONE, "sequential")
    async with get_session() as db:
        await generate_design_cells(db, experiment_id=experiment_id, factors=_TWO_BY_ONE, design_spec=spec)

    for declared in ("tabular_ml", {"preset": "tabular_ml"}, TABULAR_ML.as_dict()):
        async with get_session() as db:
            impact = await get_design_impact(
                db, experiment_id=experiment_id, design_spec={**spec, "stage_plan": declared}
            )
            assert impact.regeneration_reasons == (), declared


async def test_an_absent_strategy_matches_an_explicit_sequential(experiment_id: uuid.UUID) -> None:
    """Every experiment saved before the field existed has no entry, and its
    cells ran the plain pipeline walk -- which is what 'sequential' names. If
    those compared unequal, opening any legacy experiment's Design tab would
    demand a regeneration it doesn't need."""
    async with get_session() as db:
        await generate_design_cells(
            db, experiment_id=experiment_id, factors=_TWO_BY_ONE, design_spec=_spec(_TWO_BY_ONE)
        )

    async with get_session() as db:
        impact = await get_design_impact(
            db, experiment_id=experiment_id, design_spec=_spec(_TWO_BY_ONE, "sequential")
        )
        assert impact.regeneration_required is False
        assert impact.regeneration_reasons == ()

    async with get_session() as db:
        await generate_design_cells(
            db, experiment_id=experiment_id, factors=_TWO_BY_ONE, design_spec=_spec(_TWO_BY_ONE, "sequential")
        )

    async with get_session() as db:
        assert [r.revision for r in await list_revisions(db, experiment_id=experiment_id)] == [1]


def _cells_of(experiment_id: uuid.UUID):  # type: ignore[no-untyped-def]
    from sqlalchemy import select
    from sqlalchemy.orm import contains_eager

    # contains_eager, not a bare join: every interesting attribute of a
    # replicate (design_revision_id, experiment_id, factor_values) is a property
    # that reads through .cell, and a lazy load can't fire from sync property
    # access under asyncio -- it raises MissingGreenlet. The join is already
    # there; this just tells the ORM to populate the relationship from it.
    return (
        select(FactorialReplicateResult)
        .join(FactorialReplicateResult.cell)
        .options(contains_eager(FactorialReplicateResult.cell))
        .where(FactorialCell.experiment_id == experiment_id)
    )
