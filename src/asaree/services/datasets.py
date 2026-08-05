"""Dataset registration — CSV in, a train/test split registered, ASAREE-owned.

The split itself is `split_runner.py`'s logic (group-aware `GroupShuffleSplit`
when a group column is present and in the data, else stratified
`train_test_split`), minus the subprocess/arq-worker indirection — inline,
per the "no worker for v1" decision. A single seed; multi-seed generation is a
factorial-experiment concern (design doc §5), not a dataset-registration one.
"""

from __future__ import annotations

import io
import shutil
import uuid
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
) -> tuple[pd.DataFrame, pd.DataFrame]:
    use_groups = bool(group_column and group_column in df.columns)
    if use_groups:
        gss = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
        train_idx, test_idx = next(gss.split(df, groups=df[group_column]))
        return df.iloc[train_idx], df.iloc[test_idx]

    stratify = df[target_column] if target_column and target_column in df.columns else None
    train_df, test_df = train_test_split(df, test_size=test_size, random_state=seed, stratify=stratify)
    return train_df, test_df


async def create_dataset(
    db: AsyncSession,
    *,
    name: str,
    csv_bytes: bytes,
    owner_id: uuid.UUID,
    target_column: str | None = None,
    group_column: str | None = None,
    description: str | None = None,
    dictionary_json: str | None = None,
    test_size: float = 0.2,
    seed: int = 0,
) -> RegisteredDataset:
    if not 0.0 < test_size < 1.0:
        raise DatasetValidationError("test_size must be between 0 and 1")

    try:
        df = pd.read_csv(io.BytesIO(csv_bytes))
    except Exception as exc:
        raise DatasetValidationError(f"could not parse CSV: {exc}") from exc
    if target_column and target_column not in df.columns:
        raise DatasetValidationError(f"target_column '{target_column}' is not a column in this CSV")

    train_df, test_df = _split(
        df, test_size=test_size, seed=seed, target_column=target_column, group_column=group_column
    )

    dataset_id = uuid.uuid4()
    # Absolute, not just resolved relative to *this* process's cwd — the stored
    # path is read back by other processes (e.g. the workspace MCP server),
    # whose own cwd need not match this one's.
    dest = Path(get_settings().dataset_storage_dir).resolve() / str(dataset_id)
    dest.mkdir(parents=True, exist_ok=True)
    train_path = dest / "train.parquet"
    test_path = dest / "test.parquet"
    try:
        train_df.to_parquet(train_path, index=False)
        test_df.to_parquet(test_path, index=False)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    dataset = RegisteredDataset(
        id=dataset_id,
        name=name,
        train_path=str(train_path),
        test_path=str(test_path),
        train_sha256=sha256_file(train_path),
        test_sha256=sha256_file(test_path),
        target_column=target_column,
        description=description,
        dictionary_json=dictionary_json,
        owner_id=owner_id,
    )
    db.add(dataset)
    await db.flush()
    await db.refresh(dataset)
    return dataset


async def get_dataset(db: AsyncSession, dataset_id: uuid.UUID) -> RegisteredDataset | None:
    return (await db.execute(select(RegisteredDataset).where(RegisteredDataset.id == dataset_id))).scalar_one_or_none()


async def get_dataset_by_name(db: AsyncSession, name: str) -> RegisteredDataset | None:
    return (await db.execute(select(RegisteredDataset).where(RegisteredDataset.name == name))).scalar_one_or_none()


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
