"""Tests for services.experiment_artifacts -- create/get/list/delete, all
create-once/append-style (no upsert-by-name the way a cell has). Same
real-Postgres, throwaway-user fixture as tests/test_experiments.py."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio

import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for the FK
from asaree.models.database import dispose_engine, get_session
from asaree.models.user import User
from asaree.services.experiment_artifacts import create_artifact, delete_artifact, get_artifact, list_artifacts
from asaree.services.experiments import create_experiment, delete_experiment


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def owner_id() -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        user = User(
            email=f"artifact-test-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="Artifact Test User",
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
        experiment = await create_experiment(db, name=f"artifact-test-exp-{uuid.uuid4().hex}", owner_id=owner_id)
        eid = experiment.id
    yield eid
    async with get_session() as db:
        await delete_experiment(db, eid)


async def test_create_and_get_artifact(experiment_id: uuid.UUID) -> None:
    async with get_session() as db:
        created = await create_artifact(
            db, experiment_id=experiment_id, name="analysis", kind="analyze_result", content='{"p_value": 0.03}'
        )
        assert created.name == "analysis"
        assert created.kind == "analyze_result"
        assert created.content == '{"p_value": 0.03}'

        fetched = await get_artifact(db, experiment_id=experiment_id, artifact_id=created.id)
        assert fetched is not None
        assert fetched.id == created.id


async def test_get_artifact_returns_none_for_wrong_experiment(experiment_id: uuid.UUID, owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        other_experiment = await create_experiment(db, name=f"other-exp-{uuid.uuid4().hex}", owner_id=owner_id)
        created = await create_artifact(
            db, experiment_id=experiment_id, name="analysis", kind="analyze_result", content="{}"
        )
        assert await get_artifact(db, experiment_id=other_experiment.id, artifact_id=created.id) is None
        await delete_experiment(db, other_experiment.id)


async def test_list_artifacts_does_not_collapse_same_kind_rows(experiment_id: uuid.UUID) -> None:
    """Proves the deliberate absence of a (experiment_id, name) unique
    constraint is load-bearing -- re-running an analysis should produce a
    NEW row, never silently overwrite the last one the way upsert_replicate's
    own merge-by-cell_label would."""
    async with get_session() as db:
        await create_artifact(
            db, experiment_id=experiment_id, name="analysis", kind="analyze_result", content='{"run": 1}'
        )
        await create_artifact(
            db, experiment_id=experiment_id, name="analysis", kind="analyze_result", content='{"run": 2}'
        )

        artifacts = await list_artifacts(db, experiment_id=experiment_id)
        assert len(artifacts) == 2
        assert {a.content for a in artifacts} == {'{"run": 1}', '{"run": 2}'}


async def test_list_artifacts_empty_when_none_created(experiment_id: uuid.UUID) -> None:
    async with get_session() as db:
        assert await list_artifacts(db, experiment_id=experiment_id) == []


async def test_delete_artifact_removes_it(experiment_id: uuid.UUID) -> None:
    async with get_session() as db:
        created = await create_artifact(
            db, experiment_id=experiment_id, name="csv", kind="csv_export", content="a,b\n1,2"
        )
        await delete_artifact(db, artifact_id=created.id)
        assert await get_artifact(db, experiment_id=experiment_id, artifact_id=created.id) is None


async def test_delete_artifact_is_a_noop_for_unknown_id() -> None:
    # Matches upsert_replicate's own "best-effort, no raise on the missing case"
    # posture -- deleting something already gone isn't an error.
    async with get_session() as db:
        await delete_artifact(db, artifact_id=uuid.uuid4())
