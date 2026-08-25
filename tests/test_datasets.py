"""Tests for services.datasets -- registration stores the raw file only;
quick_split_dataset/register_manual_split are the two separate, later ways
to actually produce a train/test split against it. Same real-Postgres,
throwaway-user fixture as tests/test_experiment_artifacts.py; dataset
directories are real files on disk, cleaned up via delete_dataset (which
already best-effort-removes them) in fixture teardown."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pandas as pd
import pytest
import pytest_asyncio

from asaree.models.database import dispose_engine, get_session
from asaree.models.user import User
from asaree.services.datasets import (
    DatasetValidationError,
    create_dataset,
    delete_dataset,
    get_dataset,
    quick_split_dataset,
    register_manual_split,
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
        await delete_dataset(db, dataset.id)


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
        first_train_sha = first.train_sha256
        second = await quick_split_dataset(db, dataset=dataset, seed=1)
        # Same fixed path each time (no orphaned prior-split files), but a
        # different seed produces a genuinely different split.
        assert second.train_path == first.train_path
        assert second.train_sha256 != first_train_sha

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
        # Stratified, not grouped -- null here is an answer, not a gap.
        assert split.split_group_column is None
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
