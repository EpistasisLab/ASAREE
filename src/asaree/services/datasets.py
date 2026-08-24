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
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.config import get_settings
from asaree.models.dataset import RegisteredDataset
from asaree.security.hashing import sha256_file


class DatasetValidationError(ValueError):
    """A dataset request that fails validation before anything is written."""


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
    # Absolute, not just resolved relative to *this* process's cwd — the
    # stored path is read back by other processes (e.g. the workspace MCP
    # server), whose own cwd need not match this one's.
    return Path(get_settings().dataset_storage_dir).resolve() / str(dataset_id)


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
        raw_path.write_bytes(csv_bytes)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    dataset = RegisteredDataset(
        id=dataset_id,
        name=name,
        raw_path=str(raw_path),
        raw_sha256=sha256_file(raw_path),
        target_column=target_column,
        description=description,
        dictionary_json=dictionary_json,
        owner_id=owner_id,
    )
    db.add(dataset)
    await db.flush()
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
    """(Re-)split *dataset*'s own raw file with ASAREE's built-in strategy.

    Safe to call again -- e.g. to try a different seed/test_size -- each call
    overwrites whichever split currently exists (train.parquet/test.parquet
    live at a fixed, deterministic path per dataset, so a re-split simply
    overwrites them in place rather than accumulating orphaned files). Only
    ever reads/writes this dataset's own raw file; the raw file itself is
    never touched.
    """
    if not 0.0 < test_size < 1.0:
        raise DatasetValidationError("test_size must be between 0 and 1")
    if not dataset.raw_path:
        raise DatasetValidationError("this dataset has no raw file registered to split")

    effective_target = target_column or dataset.target_column
    try:
        df = pd.read_csv(dataset.raw_path)
    except Exception as exc:
        raise DatasetValidationError(f"could not read this dataset's raw file: {exc}") from exc
    if effective_target and effective_target not in df.columns:
        raise DatasetValidationError(f"target_column '{effective_target}' is not a column in this dataset")

    train_df, test_df, used_group_column = _split(
        df, test_size=test_size, seed=seed, target_column=effective_target, group_column=group_column
    )

    dest = _dataset_dir(dataset.id)
    dest.mkdir(parents=True, exist_ok=True)
    train_path = dest / "train.parquet"
    test_path = dest / "test.parquet"
    train_df.to_parquet(train_path, index=False)
    test_df.to_parquet(test_path, index=False)

    dataset.train_path = str(train_path)
    dataset.test_path = str(test_path)
    dataset.train_sha256 = sha256_file(train_path)
    dataset.test_sha256 = sha256_file(test_path)
    # Overwritten wholesale on every re-split, like the paths/hashes above --
    # these describe the split that currently exists, not a history of the
    # ones that came before it.
    dataset.split_method = "quick"
    dataset.split_group_column = used_group_column
    dataset.split_test_size = test_size
    dataset.split_seed = seed
    if target_column:
        dataset.target_column = target_column
    await db.flush()
    await db.refresh(dataset)
    return dataset


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

    dest = _dataset_dir(dataset.id)
    dest.mkdir(parents=True, exist_ok=True)
    train_path = dest / "train.parquet"
    test_path = dest / "test.parquet"
    train_df.to_parquet(train_path, index=False)
    test_df.to_parquet(test_path, index=False)

    dataset.train_path = str(train_path)
    dataset.test_path = str(test_path)
    dataset.train_sha256 = sha256_file(train_path)
    dataset.test_sha256 = sha256_file(test_path)
    dataset.split_method = "manual"
    # Cleared, not left behind: a manual split replaces whatever quick split
    # was there, and stale group/test_size/seed values would describe a split
    # that no longer exists. ASAREE didn't compute this one and has no honest
    # values to put here -- "unknown" is the true answer, and null says it.
    dataset.split_group_column = None
    dataset.split_test_size = None
    dataset.split_seed = None
    await db.flush()
    await db.refresh(dataset)
    return dataset


async def get_dataset(db: AsyncSession, dataset_id: uuid.UUID) -> RegisteredDataset | None:
    return (await db.execute(select(RegisteredDataset).where(RegisteredDataset.id == dataset_id))).scalar_one_or_none()


async def get_dataset_by_name(db: AsyncSession, name: str) -> RegisteredDataset | None:
    return (await db.execute(select(RegisteredDataset).where(RegisteredDataset.name == name))).scalar_one_or_none()


async def list_datasets(db: AsyncSession, *, owner_id: uuid.UUID) -> Sequence[RegisteredDataset]:
    return (await db.execute(select(RegisteredDataset).where(RegisteredDataset.owner_id == owner_id))).scalars().all()


async def delete_dataset(db: AsyncSession, dataset_id: uuid.UUID) -> bool:
    """Delete the row (cascading to its workspace events) and best-effort the files.

    Only the upload directory this dataset owns — never
    ``WORKSPACE_ROOT``. Cleaning up orphaned workspace *files* on disk is a
    separate, not-yet-built concern (see design doc §9); this fixes the
    DB-visibility gap, not the filesystem GC gap.
    """
    dataset = await get_dataset(db, dataset_id)
    if dataset is None:
        return False
    await db.delete(dataset)
    await db.flush()
    shutil.rmtree(Path(get_settings().dataset_storage_dir) / str(dataset_id), ignore_errors=True)
    return True
