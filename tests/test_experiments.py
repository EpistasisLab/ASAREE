"""Tests for services.experiments' update_experiment -- covers the rename
capability just added (name/description), alongside attaching datasets
(now a many-to-many, see models/experiment_dataset.py). Same real-Postgres,
throwaway-user fixture as tests/test_protocols.py."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for the FK
import asaree.services.experiments as experiments_service
from asaree.api import experiments as experiments_api
from asaree.models.database import dispose_engine, get_session
from asaree.models.experiment import ResearchExperiment
from asaree.models.user import User
from asaree.services.datasets import create_dataset, delete_dataset
from asaree.services.experiments import (
    create_experiment,
    create_untitled_experiment,
    get_dataset_ids_by_experiment,
    get_experiment,
    get_experiment_dataset_ids,
    list_experiments,
    set_experiment_datasets,
    update_experiment,
)


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def owner_id() -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        user = User(
            email=f"experiment-test-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="Experiment Test User",
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


async def test_rename_and_set_description(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name="Untitled Experiment", owner_id=owner_id)
        experiment_id = experiment.id

    async with get_session() as db:
        updated = await update_experiment(
            db, experiment_id, fields={"name": "spinal-fusion-sweep", "description": "renamed from the GUI"}
        )
        assert updated is not None
        assert updated.name == "spinal-fusion-sweep"
        assert updated.description == "renamed from the GUI"

    async with get_session() as db:
        fetched = await get_experiment(db, experiment_id)
        assert fetched is not None
        assert fetched.name == "spinal-fusion-sweep"
        await db.delete(fetched)


async def test_set_design_spec(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name="Untitled Experiment", owner_id=owner_id)
        experiment_id = experiment.id

    spec = {"factors": [{"name": "temperature", "levels": [0.2, 0.8]}]}
    async with get_session() as db:
        updated = await update_experiment(db, experiment_id, fields={"design_spec": spec})
        assert updated is not None
        assert updated.design_spec == spec

    async with get_session() as db:
        fetched = await get_experiment(db, experiment_id)
        assert fetched is not None
        assert fetched.design_spec == spec
        await db.delete(fetched)


async def test_create_experiment_returns_a_persisted_recommended_measurement_plan(owner_id: uuid.UUID) -> None:
    expected_metrics = [
        {
            "id": "runtime-cost",
            "catalogKey": "cost_usd",
            "name": "Cost",
            "description": "Estimated provider cost for the run.",
            "kind": "runtime",
            "valueType": "number",
            "direction": "minimize",
            "aggregation": "sum",
            "primary": False,
            "unit": "USD",
        },
        {
            "id": "runtime-duration",
            "catalogKey": "duration_seconds",
            "name": "Duration",
            "description": "Wall-clock duration of the run.",
            "kind": "runtime",
            "valueType": "number",
            "direction": "minimize",
            "aggregation": "sum",
            "primary": False,
            "unit": "seconds",
        },
        {
            "id": "runtime-total-tokens",
            "catalogKey": "total_tokens",
            "name": "Total tokens",
            "description": "Combined input and output tokens for the run.",
            "kind": "runtime",
            "valueType": "number",
            "direction": "minimize",
            "aggregation": "sum",
            "primary": False,
            "unit": "tokens",
        },
        {
            "id": "runtime-tool-calls",
            "catalogKey": "tool_calls",
            "name": "Tool calls",
            "description": "Recorded tool-call attempts across attributed Agent runs.",
            "kind": "runtime",
            "valueType": "number",
            "direction": "minimize",
            "aggregation": "sum",
            "primary": False,
            "unit": "calls",
        },
    ]
    expected_plan = {
        "metrics": [
            {
                "id": "runtime-cost",
                "name": "Cost",
                "value_type": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": False,
                "description": "Estimated provider cost for the run.",
                "unit": "USD",
            },
            {
                "id": "runtime-duration",
                "name": "Duration",
                "value_type": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": False,
                "description": "Wall-clock duration of the run.",
                "unit": "seconds",
            },
            {
                "id": "runtime-total-tokens",
                "name": "Total tokens",
                "value_type": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": False,
                "description": "Combined input and output tokens for the run.",
                "unit": "tokens",
            },
            {
                "id": "runtime-tool-calls",
                "name": "Tool calls",
                "value_type": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": False,
                "description": "Recorded tool-call attempts across attributed Agent runs.",
                "unit": "calls",
            },
        ],
        "producers": [
            {
                "id": "runtime",
                "producer_id": "asaree.runtime",
                "kind": "runtime",
                "outputs": {
                    "cost_usd": "runtime-cost",
                    "duration_seconds": "runtime-duration",
                    "total_tokens": "runtime-total-tokens",
                    "tool_calls": "runtime-tool-calls",
                },
                "artifacts": [],
                "config": {},
            }
        ],
        "inputs": [
            {
                "producer_binding_id": "runtime",
                "input_key": "facts",
                "source_key": "attempt.runtime",
            }
        ],
    }

    async with get_session() as db:
        owner = await db.get(User, owner_id)
        assert owner is not None
        response = await experiments_api.create_experiment_endpoint(
            experiments_api.CreateExperimentRequest(name=f"baseline-{uuid.uuid4().hex}"), owner, db
        )
        experiment_id = response.id

    try:
        assert response.design_spec == {"metrics": expected_metrics}
        assert response.measurement_plan == expected_plan
        assert response.metric_recommendations == {
            "applied_version": 1,
            "dismissed_version": None,
            "intentionally_removed_keys": [],
        }
        assert response.metric_recommendation_set_version == 1
        async with get_session() as db:
            persisted = await get_experiment(db, experiment_id)
            assert persisted is not None
            assert persisted.design_spec == {"metrics": expected_metrics}
            assert persisted.measurement_plan == expected_plan
            assert persisted.metric_recommendations == {
                "applied_version": 1,
                "dismissed_version": None,
                "intentionally_removed_keys": [],
            }
    finally:
        async with get_session() as db:
            persisted = await get_experiment(db, experiment_id)
            if persisted is not None:
                await db.delete(persisted)


async def test_create_experiment_preserves_a_caller_supplied_measurement_plan(owner_id: uuid.UUID) -> None:
    supplied_plan = {
        "metrics": [
            {
                "id": "duration",
                "name": "Elapsed time",
                "value_type": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": False,
                "unit": "seconds",
            }
        ],
        "producers": [
            {
                "id": "runtime",
                "producer_id": "asaree.runtime",
                "kind": "runtime",
                "outputs": {"duration_seconds": "duration"},
                "artifacts": [],
                "config": {},
            }
        ],
        "inputs": [
            {
                "producer_binding_id": "runtime",
                "input_key": "facts",
                "source_key": "attempt.runtime",
            }
        ],
    }
    async with get_session() as db:
        owner = await db.get(User, owner_id)
        assert owner is not None
        response = await experiments_api.create_experiment_endpoint(
            experiments_api.CreateExperimentRequest(
                name=f"caller-plan-{uuid.uuid4().hex}", measurement_plan=supplied_plan
            ),
            owner,
            db,
        )
        experiment_id = response.id

    try:
        assert response.measurement_plan == supplied_plan
        assert response.design_spec == {
            "metrics": [
                {
                    "id": "duration",
                    "catalogKey": "duration_seconds",
                    "name": "Elapsed time",
                    "description": "Wall-clock duration of the run.",
                    "kind": "runtime",
                    "valueType": "number",
                    "direction": "minimize",
                    "aggregation": "sum",
                    "primary": False,
                    "unit": "seconds",
                }
            ]
        }
    finally:
        async with get_session() as db:
            persisted = await get_experiment(db, experiment_id)
            if persisted is not None:
                await db.delete(persisted)


async def test_explicit_analysis_requires_the_stored_primary_metric(owner_id: uuid.UUID) -> None:
    plan = {
        "metrics": [
            {
                "id": "duration",
                "name": "Duration",
                "value_type": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": False,
            }
        ],
        "producers": [],
        "inputs": [],
    }
    async with get_session() as db:
        owner = await db.get(User, owner_id)
        assert owner is not None
        experiment = await create_experiment(
            db,
            name=f"no-primary-{uuid.uuid4().hex}",
            owner_id=owner_id,
            design_spec={"metrics": [{"id": "duration", "name": "Duration", "primary": False}]},
            measurement_plan=plan,
        )
        request = experiments_api.AnalyzeFactorialRequest(
            condition_factors=[],
            positive_levels={},
            reference_condition={},
            primary_metric="Duration",
        )
        with pytest.raises(experiments_api.HTTPException, match="no declared primary metric") as exc_info:
            await experiments_api.analyze_factorial_endpoint(experiment.id, request, owner, db)
        assert exc_info.value.status_code == 422

        experiment.design_spec = {
            "metrics": [
                {"id": "duration", "name": "Duration", "primary": True},
                {"id": "cost", "name": "Cost", "primary": False},
            ]
        }
        await db.flush()
        mismatched_request = request.model_copy(update={"primary_metric": "Cost"})
        with pytest.raises(experiments_api.HTTPException, match="not the experiment's declared primary metric"):
            await experiments_api.analyze_factorial_endpoint(experiment.id, mismatched_request, owner, db)

        await db.delete(experiment)


async def test_creating_an_experiment_does_not_backfill_preexisting_experiments(owner_id: uuid.UUID) -> None:
    empty_plan = {"metrics": [], "producers": [], "inputs": []}
    configured_plan = {
        "metrics": [
            {
                "id": "quality",
                "name": "Quality",
                "value_type": "number",
                "direction": "maximize",
                "aggregation": "mean",
                "primary": False,
            }
        ],
        "producers": [],
        "inputs": [],
    }
    async with get_session() as db:
        null_plan = ResearchExperiment(
            name=f"preexisting-{uuid.uuid4().hex}",
            owner_id=owner_id,
            design_spec={},
            measurement_plan=None,
        )
        explicit_empty = ResearchExperiment(
            name=f"preexisting-empty-{uuid.uuid4().hex}",
            owner_id=owner_id,
            design_spec={"metrics": []},
            measurement_plan=empty_plan,
        )
        configured = ResearchExperiment(
            name=f"preexisting-configured-{uuid.uuid4().hex}",
            owner_id=owner_id,
            design_spec={"metrics": [{"id": "quality", "name": "Quality"}]},
            measurement_plan=configured_plan,
        )
        db.add_all([null_plan, explicit_empty, configured])
        await db.flush()
        existing_ids = [null_plan.id, explicit_empty.id, configured.id]
        created = await create_experiment(db, name=f"new-{uuid.uuid4().hex}", owner_id=owner_id)
        created_id = created.id

    try:
        async with get_session() as db:
            unchanged = [await get_experiment(db, experiment_id) for experiment_id in existing_ids]
            assert unchanged[0] is not None and unchanged[0].measurement_plan is None
            assert unchanged[1] is not None and unchanged[1].measurement_plan == empty_plan
            assert unchanged[2] is not None and unchanged[2].measurement_plan == configured_plan
            assert all(experiment is not None and experiment.metric_recommendations is None for experiment in unchanged)
            owner = await db.get(User, owner_id)
            assert owner is not None
            responses = [
                await experiments_api.get_experiment_endpoint(experiment_id, owner, db)
                for experiment_id in existing_ids
            ]
            assert [response.measurement_plan for response in responses] == [None, empty_plan, configured_plan]
    finally:
        async with get_session() as db:
            for experiment_id in (*existing_ids, created_id):
                experiment = await get_experiment(db, experiment_id)
                if experiment is not None:
                    await db.delete(experiment)


async def test_metric_recommendation_metadata_round_trips_without_changing_the_plan(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = ResearchExperiment(
            name=f"recommendation-metadata-{uuid.uuid4().hex}",
            owner_id=owner_id,
            design_spec={"metrics": []},
            measurement_plan={"metrics": [], "producers": [], "inputs": []},
        )
        db.add(experiment)
        await db.flush()
        experiment_id = experiment.id
        original_plan = experiment.measurement_plan

    try:
        async with get_session() as db:
            owner = await db.get(User, owner_id)
            assert owner is not None
            response = await experiments_api.update_experiment_endpoint(
                experiment_id,
                experiments_api.UpdateExperimentRequest(
                    metric_recommendations={
                        "applied_version": None,
                        "dismissed_version": 1,
                        "intentionally_removed_keys": [],
                        "contextual_suggestion_dismissals": {
                            "tool_error_rate": '[{"source":"tools","target":"writer"}]'
                        },
                    }
                ),
                owner,
                db,
            )

        assert response.metric_recommendations == {
            "applied_version": None,
            "dismissed_version": 1,
            "intentionally_removed_keys": [],
            "contextual_suggestion_dismissals": {
                "tool_error_rate": '[{"source":"tools","target":"writer"}]'
            },
        }
        assert response.measurement_plan == original_plan

        async with get_session() as db:
            persisted = await get_experiment(db, experiment_id)
            assert persisted is not None
            assert persisted.metric_recommendations == response.metric_recommendations
            assert persisted.measurement_plan == original_plan
    finally:
        async with get_session() as db:
            persisted = await get_experiment(db, experiment_id)
            if persisted is not None:
                await db.delete(persisted)


async def test_experiment_owns_a_measurement_plan_separate_from_its_design(owner_id: uuid.UUID) -> None:
    first_plan = {
        "metrics": [
            {
                "id": "quality",
                "name": "Quality",
                "value_type": "number",
                "direction": "maximize",
                "aggregation": "mean",
                "primary": False,
            }
        ],
        "producers": [
            {
                "id": "runtime",
                "producer_id": "asaree.runtime",
                "kind": "runtime",
                "outputs": {"elapsed_seconds": "quality"},
                "artifacts": [],
                "config": {},
            }
        ],
        "inputs": [{"producer_binding_id": "runtime", "input_key": "facts", "source_key": "attempt.runtime"}],
    }
    replacement_plan = {
        **first_plan,
        "metrics": [
            {
                **first_plan["metrics"][0],
                "name": "Answer quality",
            }
        ],
    }
    async with get_session() as db:
        experiment = await create_experiment(
            db,
            name=f"measurement-plan-{uuid.uuid4().hex}",
            owner_id=owner_id,
            design_spec={"factors": []},
            measurement_plan=first_plan,
        )
        experiment_id = experiment.id
        assert experiment.measurement_plan == first_plan
        assert experiment.design_spec == {"factors": []}

    try:
        async with get_session() as db:
            updated = await update_experiment(db, experiment_id, fields={"measurement_plan": replacement_plan})
            assert updated is not None
            assert updated.measurement_plan == replacement_plan
    finally:
        async with get_session() as db:
            updated = await get_experiment(db, experiment_id)
            if updated is not None:
                await db.delete(updated)


_CSV = b"age,label,group\n10,0,a\n20,1,a\n30,0,b\n40,1,b\n"


async def test_experiment_holds_several_datasets_in_wiring_order(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        first = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        second = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        experiment = await create_experiment(
            db, name=f"multi-{uuid.uuid4().hex}", owner_id=owner_id, dataset_ids=[second.id, first.id]
        )
        experiment_id, first_id, second_id = experiment.id, first.id, second.id

    try:
        async with get_session() as db:
            # Order is the caller's, not the rows' -- position, not insertion luck.
            assert await get_experiment_dataset_ids(db, experiment_id) == [second_id, first_id]
            assert await get_dataset_ids_by_experiment(db, [experiment_id]) == {experiment_id: [second_id, first_id]}

        async with get_session() as db:
            # A full replacement, matching how the canvas PATCHes design_spec:
            # this both detaches `second` and reorders what's left.
            assert await set_experiment_datasets(db, experiment_id, [first_id]) == [first_id]

        async with get_session() as db:
            assert await get_experiment_dataset_ids(db, experiment_id) == [first_id]
            # Deleting a dataset cascades the link away rather than orphaning it.
            await delete_dataset(db, first_id)

        async with get_session() as db:
            assert await get_experiment_dataset_ids(db, experiment_id) == []
    finally:
        async with get_session() as db:
            for dataset_id in (first_id, second_id):
                await delete_dataset(db, dataset_id)
            e = await get_experiment(db, experiment_id)
            if e is not None:
                await db.delete(e)


async def test_set_experiment_datasets_dedupes(owner_id: uuid.UUID) -> None:
    # Two Dataset nodes naming the same registered dataset is a legal graph;
    # the composite PK would reject the second row, so it's dropped up front.
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        experiment = await create_experiment(db, name=f"dupe-{uuid.uuid4().hex}", owner_id=owner_id)
        assert await set_experiment_datasets(db, experiment.id, [dataset.id, dataset.id]) == [dataset.id]
        assert await get_experiment_dataset_ids(db, experiment.id) == [dataset.id]
        await db.delete(experiment)
        await delete_dataset(db, dataset.id)


async def test_dataset_id_is_no_longer_a_settable_field(owner_id: uuid.UUID) -> None:
    # It's join-table rows now, so the generic setattr path must refuse it
    # rather than silently writing an attribute the model no longer has.
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"legacy-{uuid.uuid4().hex}", owner_id=owner_id)
        with pytest.raises(ValueError, match="not settable"):
            await update_experiment(db, experiment.id, fields={"dataset_id": uuid.uuid4()})
        await db.delete(experiment)


async def test_update_unknown_field_rejected(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name="Untitled Experiment", owner_id=owner_id)
        with pytest.raises(ValueError, match="not settable"):
            await update_experiment(db, experiment.id, fields={"owner_id": uuid.uuid4()})
        await db.delete(experiment)


async def test_archive_and_unarchive(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name="Untitled Experiment", owner_id=owner_id)
        experiment_id = experiment.id
        assert experiment.archived_at is None

    now = datetime.now(UTC)
    async with get_session() as db:
        archived = await update_experiment(db, experiment_id, fields={"archived_at": now})
        assert archived is not None
        assert archived.archived_at is not None

    async with get_session() as db:
        unarchived = await update_experiment(db, experiment_id, fields={"archived_at": None})
        assert unarchived is not None
        assert unarchived.archived_at is None

    async with get_session() as db:
        fetched = await get_experiment(db, experiment_id)
        assert fetched is not None
        await db.delete(fetched)


async def test_list_experiments_excludes_archived_by_default(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        active = await create_experiment(db, name=f"active-{uuid.uuid4().hex}", owner_id=owner_id)
        archived = await create_experiment(db, name=f"archived-{uuid.uuid4().hex}", owner_id=owner_id)
        await update_experiment(db, archived.id, fields={"archived_at": datetime.now(UTC)})
        active_id, archived_id = active.id, archived.id

    try:
        async with get_session() as db:
            default_list = await list_experiments(db, owner_id=owner_id)
            assert {e.id for e in default_list} == {active_id}

        async with get_session() as db:
            full_list = await list_experiments(db, owner_id=owner_id, include_archived=True)
            assert {e.id for e in full_list} == {active_id, archived_id}
    finally:
        async with get_session() as db:
            for eid in (active_id, archived_id):
                e = await get_experiment(db, eid)
                if e is not None:
                    await db.delete(e)


async def test_untitled_names_are_numbered_and_skip_reserved_ones(owner_id: uuid.UUID) -> None:
    """The GUI's one-click create: the server picks the name, counting archived
    experiments (whose names the unique index still reserves) and a legacy
    un-numbered "Untitled Experiment" as taken."""
    created: list[uuid.UUID] = []
    try:
        async with get_session() as db:
            # A bare legacy name counts as 1; an archived one still holds its
            # name; a merely prefixed name is not ours to number.
            legacy = await create_experiment(db, name="Untitled Experiment", owner_id=owner_id)
            archived = await create_experiment(db, name="Untitled Experiment 2", owner_id=owner_id)
            await update_experiment(db, archived.id, fields={"archived_at": datetime.now(UTC)})
            unrelated = await create_experiment(db, name="Untitled Experiment Old", owner_id=owner_id)
            created += [legacy.id, archived.id, unrelated.id]

            first = await create_untitled_experiment(db, owner_id=owner_id)
            assert first.name == "Untitled Experiment 3"
            second = await create_untitled_experiment(db, owner_id=owner_id)
            assert second.name == "Untitled Experiment 4"
            created += [first.id, second.id]
    finally:
        async with get_session() as db:
            for eid in created:
                e = await get_experiment(db, eid)
                if e is not None:
                    await db.delete(e)


async def test_untitled_allocation_retries_when_it_loses_the_name_race(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing the insert race is the whole reason this lives server-side, so
    prove the retry: force the allocator to pick a name that is already taken
    (what a concurrent create would cause) and it must roll back just that
    insert, re-read, and land on a free name -- with the caller's earlier work
    in the same transaction still intact."""
    created: list[uuid.UUID] = []
    try:
        async with get_session() as db:
            taken = await create_experiment(db, name="Untitled Experiment 1", owner_id=owner_id)
            created.append(taken.id)

            real_next_number = experiments_service._next_untitled_number
            attempts = 0

            async def _collide_once(session: object, owner: uuid.UUID) -> int:
                nonlocal attempts
                attempts += 1
                return 1 if attempts == 1 else await real_next_number(session, owner)  # type: ignore[arg-type]

            monkeypatch.setattr(experiments_service, "_next_untitled_number", _collide_once)

            allocated = await create_untitled_experiment(db, owner_id=owner_id)
            created.append(allocated.id)
            assert attempts == 2, "should have retried exactly once after the violation"
            assert allocated.name == "Untitled Experiment 2"

        # The savepoint rollback must not have taken the pre-existing row or the
        # freshly allocated one down with it -- both survive the commit.
        async with get_session() as db:
            names = {e.name for e in await list_experiments(db, owner_id=owner_id)}
            assert {"Untitled Experiment 1", "Untitled Experiment 2"} <= names
    finally:
        async with get_session() as db:
            for eid in created:
                e = await get_experiment(db, eid)
                if e is not None:
                    await db.delete(e)
