"""Loading a dataset from a locator, and splitting it into train/test.

The locator-not-payload choice is the whole reason this package can be
standalone. A tool takes a ``data_path`` and reads the file itself; it never
receives the rows inline (a dataset large enough to be worth modeling does not
fit in a tool call, and paying to serialize it through the model's context
twice is absurd), and it never reaches into a host application's private
on-disk layout (which is what would couple it to one deployment).

pandas' readers accept fsspec URIs, so ``s3://bucket/train.parquet`` or an
``https://`` URL already work wherever the corresponding fsspec extra is
installed -- extending reach costs a dependency, not a change to any tool
signature.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from sklearn.model_selection import train_test_split


class DataError(Exception):
    """Raised when the dataset can't be read, or doesn't have the target column."""


# Extensions dispatched to a non-CSV reader. Anything else falls through to
# read_csv, which is the format an agent is most likely to hand over and the
# only one worth guessing at.
_READERS = {
    ".parquet": pd.read_parquet,
    ".pq": pd.read_parquet,
    ".json": pd.read_json,
    ".jsonl": lambda p: pd.read_json(p, lines=True),
}


def is_remote(locator: str) -> bool:
    return urlparse(locator).scheme in {"s3", "gs", "gcs", "http", "https", "abfs", "az"}


def load_frame(data_path: str) -> pd.DataFrame:
    """Read *data_path* into a DataFrame, dispatching on its extension."""
    locator = data_path.strip()
    if not locator:
        raise DataError("data_path is required")
    if not is_remote(locator) and not Path(locator).is_file():
        raise DataError(f"no such file: {locator!r}")
    reader = _READERS.get(Path(urlparse(locator).path).suffix.lower(), pd.read_csv)
    try:
        frame = reader(locator)
    except Exception as e:  # noqa: BLE001 -- surfaced to the caller as a tool error
        raise DataError(f"could not read {locator!r}: {type(e).__name__}: {e}") from e
    if frame.empty:
        raise DataError(f"{locator!r} has no rows")
    return frame


def xy(frame: pd.DataFrame, target_column: str) -> tuple[pd.DataFrame, pd.Series]:
    """Split *frame* into (features, target), validating the target column."""
    if target_column not in frame.columns:
        cols = ", ".join(map(str, frame.columns[:25]))
        raise DataError(f"target column {target_column!r} not in dataset; columns are: {cols}")
    features = frame.drop(columns=[target_column])
    if features.shape[1] == 0:
        raise DataError("dataset has no feature columns besides the target")
    return features, frame[target_column]


def split_xy(
    frame: pd.DataFrame,
    target_column: str,
    test_size: float,
    random_seed: int,
    stratify: bool,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    """Split *frame* into (X_train, y_train, X_test, y_test).

    Stratification is requested by the caller (classification) rather than
    inferred, but is dropped silently when the target has a class too rare to
    appear on both sides of the split -- a hard failure there would be a
    confusing way to learn that one class has a single row.
    """
    features, target = xy(frame, target_column)
    strat = target if stratify and target.value_counts().min() >= 2 else None
    X_train, X_test, y_train, y_test = train_test_split(  # noqa: N806
        features, target, test_size=test_size, random_state=random_seed, stratify=strat
    )
    return (
        X_train.reset_index(drop=True),
        y_train.reset_index(drop=True),
        X_test.reset_index(drop=True),
        y_test.reset_index(drop=True),
    )


def frame_sha256(frame: pd.DataFrame) -> str:
    """A stable content hash of the loaded data, for provenance in the result."""
    return hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()
