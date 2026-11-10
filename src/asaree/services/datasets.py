"""Dataset registration — the raw file in, verbatim, ASAREE-owned.

Registration stores ONLY the raw uploaded file; it never splits it. A split
is a separate, later, optional action against that same raw file --
`quick_split_dataset` (group-aware `GroupShuffleSplit` when a group column is
present and in the data, else stratified `train_test_split` -- the same logic
this module used to run inline at registration) covers the common cases as a
convenience, and `register_manual_split` accepts an already-split train/test
pair computed however the user needs. See RegisteredDataset's own module
docstring for why this is split off registration rather than baked in.
A single seed; multi-seed generation is a factorial-experiment concern
(design doc §5), not a dataset-splitting one.
"""

from __future__ import annotations

import io
import os
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from asaree_workspace_core.storage import atomic_write
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.config import get_settings
from asaree.models.dataset import RegisteredDataset
from asaree.models.dataset_split import DatasetArtifact, DatasetSplit
from asaree.security.hashing import sha256_file


class DatasetValidationError(ValueError):
    """A dataset request that fails validation before anything is written."""


class DatasetNameConflictError(ValueError):
    """A dataset name is already in use in this owner's namespace."""


def _split(
    df: pd.DataFrame, *, test_size: float, seed: int, target_column: str | None, group_column: str | None
) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    """Split *df*, returning ``(train, test, group_column_actually_used)``.

    That third element is not the ``group_column`` argument echoed back: a
    requested group column that isn't in the frame is silently ignored here
    and the split falls back to stratification. Callers persist what this
    returns (``RegisteredDataset.split_group_column``) so the recorded
    provenance describes the split that happened, not the one that was asked
    for -- the fallback is exactly the case where the difference matters.
    """
    use_groups = bool(group_column and group_column in df.columns)
    if use_groups:
        gss = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
        train_idx, test_idx = next(gss.split(df, groups=df[group_column]))
        return df.iloc[train_idx], df.iloc[test_idx], group_column

    stratify = df[target_column] if target_column and target_column in df.columns else None
    train_df, test_df = train_test_split(df, test_size=test_size, random_state=seed, stratify=stratify)
    return train_df, test_df, None


def _dataset_dir(dataset_id: uuid.UUID) -> Path:
    # Resolve the configured namespace root only when preparing/reading files.
    return Path(get_settings().dataset_storage_dir).resolve() / str(dataset_id)


def _artifact(dataset_id: uuid.UUID, path: Path, format: str) -> DatasetArtifact:
    return DatasetArtifact(
        id=uuid.uuid4(), dataset_id=dataset_id, namespace="datasets",
        storage_key=path.relative_to(Path(get_settings().dataset_storage_dir).resolve()).as_posix(),
        sha256=sha256_file(path), byte_size=path.stat().st_size, format=format, status="ready",
    )


def _verify_source(dataset: RegisteredDataset) -> str:
    raw_path = dataset.raw_path
    if raw_path is None:
        raise DatasetValidationError("this dataset has no raw file registered to split")
    try:
        observed = sha256_file(Path(raw_path))
    except OSError as exc:
        raise DatasetValidationError(f"Original source unavailable: {exc}") from exc
    if not dataset.raw_sha256 or observed != dataset.raw_sha256:
        raise DatasetValidationError("Original source hash does not match its registered identity")
    return raw_path


async def _publish_split(
    db: AsyncSession, dataset: RegisteredDataset, train: pd.DataFrame, test: pd.DataFrame, **provenance: object
) -> RegisteredDataset:
    split_id = uuid.uuid4()
    parent = _dataset_dir(dataset.id) / "splits"
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{split_id}.preparing"
    dest = parent / str(split_id)
    staging.mkdir()
    published = False
    try:
        train.to_parquet(staging / "train.parquet", index=False)
        test.to_parquet(staging / "test.parquet", index=False)
        # The directory rename publishes the complete pair; no reader sees a half pair.
        os.rename(staging, dest)
        published = True
        train_artifact = _artifact(dataset.id, dest / "train.parquet", "parquet")
        test_artifact = _artifact(dataset.id, dest / "test.parquet", "parquet")
        async with db.begin_nested():
            split = DatasetSplit(
                id=split_id, dataset_id=dataset.id, source_sha256=dataset.raw_sha256,
                train_artifact=train_artifact, test_artifact=test_artifact, **provenance,
            )
            db.add(split)
            await db.flush()
            dataset.current_split = split
            dataset.current_split_id = split_id
            await db.flush()
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        if published:
            shutil.rmtree(dest, ignore_errors=True)
        raise
    return dataset


async def list_splits(db: AsyncSession, *, dataset_id: uuid.UUID, owner_id: uuid.UUID) -> Sequence[DatasetSplit]:
    return (await db.execute(
        select(DatasetSplit).join(RegisteredDataset, RegisteredDataset.id == DatasetSplit.dataset_id)
        .where(DatasetSplit.dataset_id == dataset_id, RegisteredDataset.owner_id == owner_id)
        .order_by(DatasetSplit.created_at, DatasetSplit.id)
    )).scalars().all()


async def get_split(
    db: AsyncSession, *, dataset_id: uuid.UUID, split_id: uuid.UUID, owner_id: uuid.UUID
) -> DatasetSplit | None:
    return (await db.execute(
        select(DatasetSplit).join(RegisteredDataset, RegisteredDataset.id == DatasetSplit.dataset_id)
        .where(
            DatasetSplit.dataset_id == dataset_id, DatasetSplit.id == split_id, RegisteredDataset.owner_id == owner_id
        )
    )).scalar_one_or_none()


async def select_split(
    db: AsyncSession, *, dataset_id: uuid.UUID, split_id: uuid.UUID, owner_id: uuid.UUID
) -> RegisteredDataset | None:
    split = await get_split(db, dataset_id=dataset_id, split_id=split_id, owner_id=owner_id)
    if split is None:
        return None
    dataset = await get_dataset(db, dataset_id)
    assert dataset is not None
    dataset.current_split = split
    dataset.current_split_id = split.id
    await db.flush()
    return dataset


async def create_dataset(
    db: AsyncSession,
    *,
    name: str,
    csv_bytes: bytes,
    owner_id: uuid.UUID,
    target_column: str | None = None,
    description: str | None = None,
    dictionary_json: str | None = None,
) -> RegisteredDataset:
    try:
        df = pd.read_csv(io.BytesIO(csv_bytes))
    except Exception as exc:
        raise DatasetValidationError(f"could not parse CSV: {exc}") from exc
    if target_column and target_column not in df.columns:
        raise DatasetValidationError(f"target_column '{target_column}' is not a column in this CSV")

    dataset_id = uuid.uuid4()
    dest = _dataset_dir(dataset_id)
    dest.mkdir(parents=True, exist_ok=True)
    raw_path = dest / "raw.csv"
    try:
        atomic_write(raw_path, csv_bytes)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    dataset = RegisteredDataset(
        id=dataset_id,
        name=name,
        raw_sha256=sha256_file(raw_path),
        target_column=target_column,
        description=description,
        dictionary_json=dictionary_json,
        owner_id=owner_id,
    )
    db.add(dataset)
    try:
        await db.flush()
        dataset.raw_artifact = _artifact(dataset_id, raw_path, "csv")
        await db.flush()
    except IntegrityError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise DatasetNameConflictError(f"Dataset name '{name}' is already in use") from exc
    await db.refresh(dataset)
    return dataset


async def quick_split_dataset(
    db: AsyncSession,
    *,
    dataset: RegisteredDataset,
    target_column: str | None = None,
    group_column: str | None = None,
    test_size: float = 0.2,
    seed: int = 0,
) -> RegisteredDataset:
    """Create and select a new immutable split of the registered source."""
    if not 0.0 < test_size < 1.0:
        raise DatasetValidationError("test_size must be between 0 and 1")
    raw_path = _verify_source(dataset)

    effective_target = target_column or dataset.target_column
    try:
        df = pd.read_csv(raw_path)
    except Exception as exc:
        raise DatasetValidationError(f"could not read this dataset's raw file: {exc}") from exc
    if effective_target and effective_target not in df.columns:
        raise DatasetValidationError(f"target_column '{effective_target}' is not a column in this dataset")

    try:
        train_df, test_df, used_group_column = _split(
            df, test_size=test_size, seed=seed, target_column=effective_target, group_column=group_column
        )
    except ValueError as exc:
        raise DatasetValidationError(str(exc)) from exc

    return await _publish_split(
        db, dataset, train_df, test_df, method="quick", target_column=effective_target,
        strategy="group_shuffle" if used_group_column else "stratified" if effective_target else "random",
        group_column=used_group_column, stratify_column=None if used_group_column else effective_target,
        test_size=test_size, seed=seed,
    )


async def register_manual_split(
    db: AsyncSession,
    *,
    dataset: RegisteredDataset,
    train_csv_bytes: bytes,
    test_csv_bytes: bytes,
) -> RegisteredDataset:
    """Register an already-split train/test pair the user computed however
    they needed (k-fold collapsed to one fold, a time-based split, a custom
    cohort rule, ...) -- ASAREE only validates that both parse as tabular
    data and stores/hashes them, the same "bring your own code" precedent
    the Script node already established for scoring: ASAREE doesn't need to
    be the one true implementation of every splitting strategy a science
    project might need, only a trustworthy place to keep whichever one was
    used. Re-serialized to parquet (not stored as the uploaded CSVs verbatim)
    so train_path/test_path stay the one format every downstream reader
    already expects, regardless of which path produced them.
    """
    try:
        train_df = pd.read_csv(io.BytesIO(train_csv_bytes))
    except Exception as exc:
        raise DatasetValidationError(f"could not parse the train CSV: {exc}") from exc
    try:
        test_df = pd.read_csv(io.BytesIO(test_csv_bytes))
    except Exception as exc:
        raise DatasetValidationError(f"could not parse the test CSV: {exc}") from exc

    # Association records the supplied origin, without claiming source-row membership.
    return await _publish_split(db, dataset, train_df, test_df, method="manual", target_column=dataset.target_column)


async def get_dataset(db: AsyncSession, dataset_id: uuid.UUID) -> RegisteredDataset | None:
    return (await db.execute(select(RegisteredDataset).where(RegisteredDataset.id == dataset_id))).scalar_one_or_none()


async def get_dataset_by_name(
    db: AsyncSession, name: str, *, owner_id: uuid.UUID
) -> RegisteredDataset | None:
    return (
        await db.execute(
            select(RegisteredDataset).where(
                RegisteredDataset.owner_id == owner_id,
                RegisteredDataset.name == name,
            )
        )
    ).scalar_one_or_none()


async def list_datasets(db: AsyncSession, *, owner_id: uuid.UUID) -> Sequence[RegisteredDataset]:
    return (await db.execute(select(RegisteredDataset).where(RegisteredDataset.owner_id == owner_id))).scalars().all()


async def delete_dataset(db: AsyncSession, dataset_id: uuid.UUID) -> bool:
    """Delete the row (cascading to its workspace events) and best-effort the files.

    Only the upload directory this dataset owns — never
    ``WORKSPACE_ROOT``. Cleaning up orphaned workspace *files* on disk is a
    separate, not-yet-built concern (see design doc §9); this fixes the
    DB-visibility gap, not the filesystem GC gap.
    """
    dataset = await db.scalar(select(RegisteredDataset).where(
        RegisteredDataset.id == dataset_id,
    ).with_for_update(of=RegisteredDataset))
    if dataset is None:
        return False
    from asaree.services.dataset_row_retention import required_by_job

    if await required_by_job(db, dataset_id, dataset.owner_id):
        raise DatasetValidationError("Dataset is required by a retained result job and cannot be deleted")
    from asaree.models.dataset_input_reference import DatasetInputReference

    if await db.scalar(select(DatasetInputReference.id).where(
        DatasetInputReference.dataset_id == dataset_id
    ).limit(1)) is not None:
        raise DatasetValidationError(
            "Dataset is referenced by published inputs or stored attempts and cannot be deleted"
        )
    from asaree.services.managed_storage import workspace_references

    artifacts = (await db.execute(select(DatasetArtifact).where(
        DatasetArtifact.dataset_id == dataset_id
    ))).scalars().all()
    if workspace_references({Path(artifact.path).resolve() for artifact in artifacts}):
        raise DatasetValidationError("Dataset is referenced by a retained workspace and cannot be deleted")
    await db.delete(dataset)
    await db.flush()
    shutil.rmtree(Path(get_settings().dataset_storage_dir) / str(dataset_id), ignore_errors=True)
    return True
