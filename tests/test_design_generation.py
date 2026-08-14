"""Tests for services.design_generation -- pure combinatorics (generate_design/
cell_label_for) plus the replicate-loop/randomization-seed behavior added to
generate_design_cells for the experiment Design tab. Same real-Postgres,
throwaway-user fixture as tests/test_experiments.py."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for the FK
from asaree.models.database import dispose_engine, get_session
from asaree.models.user import User
from asaree.services.design_generation import (
    DesignValidationError,
    cell_label_for,
    generate_design,
    generate_design_cells,
)
from asaree.services.experiments import create_experiment
from asaree.services.factorial_cells import list_cells

_FACTORS = [{"name": "tier", "levels": ["small", "large"]}, {"name": "effort", "levels": ["low", "high"]}]


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def owner_id() -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        user = User(
            email=f"design-gen-test-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="Design Generation Test User",
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


def test_generate_design_is_the_cross_product() -> None:
    combos = generate_design(_FACTORS)
    assert len(combos) == 4
    assert {"tier": "small", "effort": "low"} in combos
    assert {"tier": "large", "effort": "high"} in combos


def test_generate_design_rejects_empty_factors() -> None:
    with pytest.raises(DesignValidationError, match="non-empty list"):
        generate_design([])


def test_cell_label_for_replicate_one_has_no_suffix() -> None:
    combo = {"tier": "small", "effort": "low"}
    assert cell_label_for(combo) == cell_label_for(combo, replicate=1)
    assert "rep" not in cell_label_for(combo, replicate=1)


def test_cell_label_for_replicate_two_gets_suffix() -> None:
    combo = {"tier": "small", "effort": "low"}
    assert cell_label_for(combo, replicate=2) == f"{cell_label_for(combo)}__rep2"


async def test_generate_design_cells_default_replicate_matches_today(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"design-gen-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id

    async with get_session() as db:
        cells = await generate_design_cells(db, experiment_id=experiment_id, factors=_FACTORS)
        assert len(cells) == 4
        assert all("rep" not in c.cell_label for c in cells)

    async with get_session() as db:
        await db.delete(await db.get(type(experiment), experiment_id))


async def test_generate_design_cells_with_replicates_creates_n_copies(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"design-gen-reps-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id

    async with get_session() as db:
        cells = await generate_design_cells(db, experiment_id=experiment_id, factors=_FACTORS, replicates=3)
        assert len(cells) == 12  # 4 combinations * 3 replicates
        labels = {c.cell_label for c in cells}
        assert len(labels) == 12  # every replicate gets a distinct label

    async with get_session() as db:
        await db.delete(await db.get(type(experiment), experiment_id))


async def _mark_scored(db, cell_id: uuid.UUID) -> None:
    from asaree.models.factorial_cell_result import FactorialCellResult

    cell = await db.get(FactorialCellResult, cell_id)
    cell.metric_values = {"scored": True}
    await db.flush()


async def test_generate_design_cells_raising_replicates_preserves_original_cell(owner_id: uuid.UUID) -> None:
    """Idempotency: increasing replicates only adds new cells (2, 3, ...) --
    the original replicate-1 cell (and any results already on it) is
    untouched, matching the existing "widen a factor's levels" guarantee."""
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"design-gen-widen-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id

    async with get_session() as db:
        first_pass = await generate_design_cells(db, experiment_id=experiment_id, factors=_FACTORS, replicates=1)
        rep1_id = next(c.id for c in first_pass if c.factor_values == {"tier": "small", "effort": "low"})
        await _mark_scored(db, rep1_id)

    async with get_session() as db:
        second_pass = await generate_design_cells(db, experiment_id=experiment_id, factors=_FACTORS, replicates=2)
        assert len(second_pass) == 8  # 4 combinations * 2 replicates
        rep1_after = next(c for c in second_pass if c.id == rep1_id)
        assert rep1_after.metric_values == {"scored": True}  # untouched

    async with get_session() as db:
        await db.delete(await db.get(type(experiment), experiment_id))


async def test_generate_design_cells_rejects_zero_replicates(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"design-gen-zero-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id

    async with get_session() as db:
        with pytest.raises(DesignValidationError, match="at least 1"):
            await generate_design_cells(db, experiment_id=experiment_id, factors=_FACTORS, replicates=0)

    async with get_session() as db:
        await db.delete(await db.get(type(experiment), experiment_id))


async def test_generate_design_cells_randomization_seed_shuffles_order_deterministically(
    owner_id: uuid.UUID,
) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"design-gen-seed-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id

    async with get_session() as db:
        cells_a = await generate_design_cells(
            db, experiment_id=experiment_id, factors=_FACTORS, replicates=1, randomization_seed=42
        )
        order_a = [c.cell_label for c in cells_a]

    async with get_session() as db:
        cells_b = await generate_design_cells(
            db, experiment_id=experiment_id, factors=_FACTORS, replicates=1, randomization_seed=42
        )
        order_b = [c.cell_label for c in cells_b]

    assert order_a == order_b  # same seed -> same order
    assert set(order_a) == set(await _labels(experiment_id))

    async with get_session() as db:
        await db.delete(await db.get(type(experiment), experiment_id))


async def _labels(experiment_id: uuid.UUID) -> list[str]:
    async with get_session() as db:
        cells = await list_cells(db, experiment_id=experiment_id)
        return [c.cell_label for c in cells]
