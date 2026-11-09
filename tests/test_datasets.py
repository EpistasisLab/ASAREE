"""Tests for services.datasets -- registration stores the raw file only;
quick_split_dataset/register_manual_split are the two separate, later ways
to actually produce a train/test split against it. Same real-Postgres,
throwaway-user fixture as tests/test_experiment_artifacts.py; dataset
directories are real files on disk, cleaned up via delete_dataset (which
already best-effort-removes them) in fixture teardown."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from asaree.api.datasets import _split_response, router
from asaree.deps import get_current_user
from asaree.models.database import dispose_engine, get_session
from asaree.models.database import get_db as api_db
from asaree.models.user import User
from asaree.services.datasets import (
    DatasetNameConflictError,
    DatasetValidationError,
    create_dataset,
    delete_dataset,
    get_dataset,
    get_dataset_by_name,
    get_split,
    list_splits,
    quick_split_dataset,
    register_manual_split,
    select_split,
)

_CSV = b"age,group,label\n10,a,0\n20,a,1\n30,b,0\n40,b,1\n50,a,0\n60,b,1\n70,a,0\n80,b,1\n"


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def owner_id() -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        user = User(
            email=f"dataset-test-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="Dataset Test User",
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


async def test_create_dataset_stores_raw_file_without_splitting(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(
            db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id, target_column="label"
        )
        assert dataset.raw_path is not None
        assert dataset.raw_sha256 is not None
        assert dataset.train_path is None
        assert dataset.test_path is None
        assert dataset.train_sha256 is None
        assert dataset.test_sha256 is None
        assert dataset.current_split_id is None
        assert dataset.raw_artifact_id is not None
        assert dataset.raw_artifact.status == "ready"
        assert not Path(dataset.raw_artifact.storage_key).is_absolute()
        await delete_dataset(db, dataset.id)


async def test_dataset_names_are_scoped_per_owner(owner_id: uuid.UUID) -> None:
    name = f"shared-dataset-{uuid.uuid4().hex}"
    async with get_session() as db:
        other = User(
            email=f"dataset-other-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="Other Dataset User",
        )
        db.add(other)
        await db.flush()
        other_owner_id = other.id

    dataset_ids: list[uuid.UUID] = []
    try:
        for current_owner_id in (owner_id, other_owner_id):
            async with get_session() as db:
                dataset = await create_dataset(db, name=name, csv_bytes=_CSV, owner_id=current_owner_id)
                dataset_ids.append(dataset.id)

        async with get_session() as db:
            first = await get_dataset_by_name(db, name, owner_id=owner_id)
            second = await get_dataset_by_name(db, name, owner_id=other_owner_id)
            assert first is not None and first.id == dataset_ids[0]
            assert second is not None and second.id == dataset_ids[1]
    finally:
        for dataset_id in dataset_ids:
            async with get_session() as db:
                await delete_dataset(db, dataset_id)
        async with get_session() as db:
            other = await db.get(User, other_owner_id)
            if other is not None:
                await db.delete(other)


async def test_dataset_names_remain_unique_within_one_owner(owner_id: uuid.UUID) -> None:
    name = f"duplicate-dataset-{uuid.uuid4().hex}"
    async with get_session() as db:
        dataset = await create_dataset(db, name=name, csv_bytes=_CSV, owner_id=owner_id)
        dataset_id = dataset.id

    try:
        with pytest.raises(DatasetNameConflictError, match="already in use"):
            async with get_session() as db:
                await create_dataset(db, name=name, csv_bytes=_CSV, owner_id=owner_id)
    finally:
        async with get_session() as db:
            await delete_dataset(db, dataset_id)


async def test_create_dataset_rejects_unparseable_csv(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        with pytest.raises(DatasetValidationError, match="could not parse CSV"):
            await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=b"", owner_id=owner_id)


async def test_create_dataset_rejects_missing_target_column(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        with pytest.raises(DatasetValidationError, match="not a column"):
            await create_dataset(
                db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id, target_column="nope"
            )


async def test_quick_split_dataset_produces_train_test_files(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(
            db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id, target_column="label"
        )
        split = await quick_split_dataset(db, dataset=dataset, test_size=0.25, seed=0)
        assert split.train_path is not None
        assert split.test_path is not None
        assert split.train_sha256 is not None
        assert split.test_sha256 is not None

        train_df = pd.read_parquet(split.train_path)
        test_df = pd.read_parquet(split.test_path)
        assert len(train_df) + len(test_df) == 8
        assert len(test_df) == 2

        await delete_dataset(db, dataset.id)


async def test_quick_split_dataset_is_re_runnable_with_a_different_seed(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        first = await quick_split_dataset(db, dataset=dataset, seed=0)
        first_id = first.current_split_id
        first_train_path = first.train_path
        first_bytes = Path(first_train_path).read_bytes()
        first_train_sha = first.train_sha256
        second = await quick_split_dataset(db, dataset=dataset, seed=1)
        assert second.current_split_id != first_id
        assert second.train_path != first_train_path
        assert second.train_sha256 != first_train_sha
        assert Path(first_train_path).read_bytes() == first_bytes
        history = await list_splits(db, dataset_id=dataset.id, owner_id=owner_id)
        assert len(history) == 2
        await select_split(db, dataset_id=dataset.id, split_id=first_id, owner_id=owner_id)
        assert dataset.train_path == first_train_path
        assert dataset.train_sha256 == first_train_sha
        assert dataset.split_seed == 0

        await delete_dataset(db, dataset.id)


async def test_quick_split_dataset_rejects_invalid_test_size(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        with pytest.raises(DatasetValidationError, match="test_size must be between 0 and 1"):
            await quick_split_dataset(db, dataset=dataset, test_size=1.5)
        await delete_dataset(db, dataset.id)


async def test_quick_split_dataset_group_aware_when_group_column_present(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        split = await quick_split_dataset(db, dataset=dataset, group_column="group", test_size=0.25, seed=0)

        train_groups = set(pd.read_parquet(split.train_path)["group"])
        test_groups = set(pd.read_parquet(split.test_path)["group"])
        assert not (train_groups & test_groups)
        assert split.split_group_column == "group"
        assert split.current_split.strategy == "group_shuffle"
        assert split.current_split.stratify_column is None

        await delete_dataset(db, dataset.id)


async def test_quick_split_dataset_records_its_parameters(owner_id: uuid.UUID) -> None:
    # The parameters are the only record of HOW a split was made -- the hashes
    # say which files came out, not whether re-running would reproduce them.
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        split = await quick_split_dataset(db, dataset=dataset, test_size=0.25, seed=7)
        assert split.split_method == "quick"
        assert split.split_test_size == 0.25
        assert split.split_seed == 7
        assert split.current_split.source_sha256 == dataset.raw_sha256
        assert split.current_split.strategy == "random"
        # Stratified, not grouped -- null here is an answer, not a gap.
        assert split.split_group_column is None
        await delete_dataset(db, dataset.id)


async def test_split_failure_keeps_previous_selection(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        await quick_split_dataset(db, dataset=dataset)
        selected = dataset.current_split_id
        original = pd.DataFrame.to_parquet
        calls = 0

        def fail_second_write(frame: pd.DataFrame, *args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected test partition failure")
            original(frame, *args, **kwargs)

        with patch.object(pd.DataFrame, "to_parquet", fail_second_write), pytest.raises(OSError, match="injected"):
            await quick_split_dataset(db, dataset=dataset, seed=1)
        assert dataset.current_split_id == selected
        assert len(await list_splits(db, dataset_id=dataset.id, owner_id=owner_id)) == 1
        assert len(list(Path(dataset.train_path).parent.parent.iterdir())) == 1
        await delete_dataset(db, dataset.id)


async def test_database_failure_does_not_publish_split(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        await quick_split_dataset(db, dataset=dataset)
        selected = dataset.current_split_id
        dataset_id = dataset.id
        splits_dir = Path(dataset.train_path).parent.parent
        flush = db.flush
        calls = 0

        async def fail_selection_flush(*args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                # Cause a real PostgreSQL error after records have been inserted.
                await db.execute(text("SELECT 1 / 0"))
            await flush(*args, **kwargs)

        with patch.object(db, "flush", fail_selection_flush), pytest.raises(DBAPIError, match="division by zero"):
            await quick_split_dataset(db, dataset=dataset, seed=1)
        await db.refresh(dataset)
        assert dataset.current_split_id == selected
        assert len(await list_splits(db, dataset_id=dataset_id, owner_id=owner_id)) == 1
        assert len(list(splits_dir.iterdir())) == 1
        await delete_dataset(db, dataset_id)


async def test_source_hash_mismatch_rejects_new_splits(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        await quick_split_dataset(db, dataset=dataset)
        selected = dataset.current_split_id
        Path(dataset.raw_path).write_bytes(b"changed source")
        with pytest.raises(DatasetValidationError, match="hash"):
            await quick_split_dataset(db, dataset=dataset)
        assert dataset.current_split_id == selected
        await delete_dataset(db, dataset.id)


async def test_manual_split_keeps_unknown_legacy_source_provenance(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        dataset.raw_path = None
        dataset.raw_sha256 = None
        await db.flush()
        await register_manual_split(db, dataset=dataset, train_csv_bytes=b"x\n1\n", test_csv_bytes=b"x\n2\n")
        assert dataset.current_split.source_sha256 is None
        assert dataset.current_split.method == "manual"
        await delete_dataset(db, dataset.id)


async def test_current_split_constraint_rejects_another_dataset(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        first = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        second = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        await quick_split_dataset(db, dataset=second)
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(text("UPDATE registered_datasets SET current_split_id = :split WHERE id = :id"),
                                 {"split": second.current_split_id, "id": first.id})
        await delete_dataset(db, first.id)
        await delete_dataset(db, second.id)


async def test_split_http_history_selection_and_owner_scope(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        await quick_split_dataset(db, dataset=dataset, target_column="label", test_size=0.25)
        first = dataset.current_split
        original = _split_response(first).model_dump(mode="json")
        await quick_split_dataset(db, dataset=dataset, seed=1)
        second_id = dataset.current_split_id
        user = SimpleNamespace(id=owner_id)
        app = FastAPI()
        app.include_router(router)

        async def current_user() -> SimpleNamespace:
            return user

        async def current_db() -> AsyncIterator[object]:
            yield db

        app.dependency_overrides[get_current_user] = current_user
        app.dependency_overrides[api_db] = current_db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            prefix = f"/datasets/{dataset.id}/splits"
            assert len((await client.get(prefix)).json()) == 2
            assert (await client.get(f"{prefix}/{first.id}")).json() == original
            selected = await client.put(f"{prefix}/{first.id}/selection")
            assert selected.status_code == 200
            assert selected.json()["current_split_id"] == str(first.id)
            assert selected.json()["target_column"] == "label"
            assert (await client.get(f"{prefix}/{first.id}")).json() == original
            user.id = uuid.uuid4()
            assert (await client.get(prefix)).status_code == 404
            assert (await client.get(f"{prefix}/{first.id}")).status_code == 404
            assert (await client.put(f"{prefix}/{second_id}/selection")).status_code == 404
            user.id = owner_id
        await delete_dataset(db, dataset.id)


async def test_split_readers_enforce_owner_and_dataset_scope(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        await quick_split_dataset(db, dataset=dataset)
        split_id = dataset.current_split_id
        assert await list_splits(db, dataset_id=dataset.id, owner_id=uuid.uuid4()) == []
        assert await get_split(db, dataset_id=dataset.id, split_id=split_id, owner_id=uuid.uuid4()) is None
        assert await select_split(db, dataset_id=uuid.uuid4(), split_id=split_id, owner_id=owner_id) is None
        assert await select_split(db, dataset_id=dataset.id, split_id=split_id, owner_id=uuid.uuid4()) is None
        assert dataset.current_split_id == split_id
        await delete_dataset(db, dataset.id)


async def test_quick_split_preserves_effective_target_and_fallback(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        await quick_split_dataset(db, dataset=dataset, target_column="label", group_column="absent", test_size=0.25)
        split = dataset.current_split
        assert split.target_column == "label"
        assert split.strategy == "stratified"
        assert split.stratify_column == "label"
        assert split.group_column is None
        await register_manual_split(db, dataset=dataset, train_csv_bytes=b"x\n1\n", test_csv_bytes=b"x\n2\n")
        manual = dataset.current_split
        assert manual.method == "manual"
        assert manual.strategy is None and manual.stratify_column is None
        assert manual.seed is None and manual.test_size is None
        assert split.strategy == "stratified" and split.seed == 0
        await select_split(db, dataset_id=dataset.id, split_id=split.id, owner_id=owner_id)
        assert dataset.target_column == "label"
        await delete_dataset(db, dataset.id)


async def test_quick_split_dataset_records_no_group_column_when_the_requested_one_is_absent(
    owner_id: uuid.UUID,
) -> None:
    # _split silently falls back to a stratified split when the named column
    # isn't in the frame; recording the REQUEST would claim a group-aware
    # holdout that never happened.
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        split = await quick_split_dataset(db, dataset=dataset, group_column="not-a-column", test_size=0.25)
        assert split.split_group_column is None
        await delete_dataset(db, dataset.id)


async def test_quick_split_dataset_raises_without_a_raw_file(owner_id: uuid.UUID) -> None:
    # Simulates a dataset registered before raw_path existed (permanently
    # null, per RegisteredDataset's own module docstring) -- there's
    # nothing left to split.
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        dataset.raw_path = None
        await db.flush()
        with pytest.raises(DatasetValidationError, match="no raw file"):
            await quick_split_dataset(db, dataset=dataset)
        await delete_dataset(db, dataset.id)


async def test_register_manual_split_stores_train_test_parquet(owner_id: uuid.UUID) -> None:
    train_csv = b"age,label\n10,0\n20,1\n30,0\n"
    test_csv = b"age,label\n40,1\n"
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        split = await register_manual_split(db, dataset=dataset, train_csv_bytes=train_csv, test_csv_bytes=test_csv)
        assert split.train_sha256 is not None
        assert split.test_sha256 is not None

        assert len(pd.read_parquet(split.train_path)) == 3
        assert len(pd.read_parquet(split.test_path)) == 1

        await delete_dataset(db, dataset.id)


async def test_register_manual_split_clears_a_previous_quick_split_s_parameters(owner_id: uuid.UUID) -> None:
    # Otherwise the retired quick split's group/test_size/seed would sit
    # alongside the manual split's hashes, describing a split that no longer
    # exists.
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        await quick_split_dataset(db, dataset=dataset, group_column="group", test_size=0.25, seed=3)
        split = await register_manual_split(
            db, dataset=dataset, train_csv_bytes=b"age,label\n10,0\n", test_csv_bytes=b"age,label\n40,1\n"
        )
        assert split.split_method == "manual"
        assert split.split_group_column is None
        assert split.split_test_size is None
        assert split.split_seed is None
        await delete_dataset(db, dataset.id)


async def test_register_manual_split_rejects_unparseable_train_csv(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        with pytest.raises(DatasetValidationError, match="could not parse the train CSV"):
            await register_manual_split(db, dataset=dataset, train_csv_bytes=b"", test_csv_bytes=b"age\n1\n")
        await delete_dataset(db, dataset.id)


async def test_delete_dataset_removes_the_row(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        dataset = await create_dataset(db, name=f"ds-{uuid.uuid4().hex}", csv_bytes=_CSV, owner_id=owner_id)
        dataset_id = dataset.id
        assert await delete_dataset(db, dataset_id) is True
        assert await get_dataset(db, dataset_id) is None
