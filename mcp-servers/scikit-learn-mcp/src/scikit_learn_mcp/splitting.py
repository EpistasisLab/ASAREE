"""How a dataset is divided into train and test, as an explicit, hashable spec.

Splitting used to be three implicit arguments (``test_size``, ``random_seed``,
stratify-if-classification) buried in each tool. That is the right default and
the wrong ceiling: a random row-wise split silently inflates every metric
downstream of it whenever rows are not independent -- repeated measures on the
same patient, several rows per site, anything time-ordered -- and an AUC ruined
that way looks *better*, not worse, so nothing in the results hints at the
problem.

So the strategy is named rather than assumed, the realized split is audited
(:func:`audit`) for the known ways it goes wrong, and it is hashed
(:func:`spec_sha256`) on every call. Two runs reporting the same
``split_sha256`` were scored on the same rows; two that don't, weren't, and
their AUCs are not comparable however close they look.

The spec stays a value, not a stored artifact: a small JSON object that
deterministically reproduces the same split from the same file, so nothing has
to be materialized to disk or carried between calls as an id.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import (
    GroupKFold,
    GroupShuffleSplit,
    KFold,
    StratifiedGroupKFold,
    StratifiedKFold,
    train_test_split,
)

from scikit_learn_mcp import profile
from scikit_learn_mcp.data import DataError, load_frame

STRATEGIES = ("random", "group", "time", "predefined")
_MARKER = "__split__"


class SplitError(Exception):
    """Raised when the requested split can't be built from this dataset."""


@dataclass(frozen=True)
class SplitSpec:
    """A reproducible description of one train/test division."""

    strategy: str = "random"
    test_size: float = 0.2
    random_seed: int = 42
    stratify: bool = True
    group_column: str = ""
    time_column: str = ""
    split_column: str = ""
    test_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The canonical form that gets hashed and echoed back in every result."""
        base: dict[str, Any] = {
            "strategy": self.strategy,
            "test_size": self.test_size,
            "random_seed": self.random_seed,
            "stratify": self.stratify,
        }
        for key in ("group_column", "time_column", "split_column", "test_path"):
            value = getattr(self, key)
            if value and value != _MARKER:
                base[key] = value
        return base


@dataclass(frozen=True)
class Split:
    """One realized division of a dataset, plus how it was arrived at."""

    x_train: pd.DataFrame
    y_train: pd.Series
    x_test: pd.DataFrame
    y_test: pd.Series
    groups_train: pd.Series | None
    groups_test: pd.Series | None
    info: dict[str, Any] = field(default_factory=dict)


def parse_spec(
    split_json: str,
    *,
    test_size: float = 0.2,
    random_seed: int = 42,
    stratify: bool = True,
) -> SplitSpec:
    """Build a spec from the JSON argument, defaulting to the plain-args split.

    The bare ``test_size``/``random_seed`` arguments stay as the zero-effort
    path -- most tabular files really are IID rows, and a caller shouldn't have
    to write JSON to say so. ``split_json`` overrides them field by field.
    """
    overrides: dict[str, Any] = {}
    if split_json.strip():
        try:
            overrides = json.loads(split_json)
        except json.JSONDecodeError as e:
            raise SplitError(f"split_json is not valid JSON: {e}") from e
        if not isinstance(overrides, dict):
            raise SplitError(f"split_json must be a JSON object, got {type(overrides).__name__}")

    known = set(SplitSpec.__dataclass_fields__)
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise SplitError(f"unknown split_json keys {unknown}; supported keys are {sorted(known)}")

    spec = SplitSpec(
        strategy=str(overrides.get("strategy", "random")).lower(),
        test_size=float(overrides.get("test_size", test_size)),
        random_seed=int(overrides.get("random_seed", random_seed)),
        stratify=bool(overrides.get("stratify", stratify)),
        group_column=str(overrides.get("group_column", "")),
        time_column=str(overrides.get("time_column", "")),
        split_column=str(overrides.get("split_column", "")),
        test_path=str(overrides.get("test_path", "")),
    )
    _validate(spec)
    return spec


def _validate(spec: SplitSpec) -> None:
    if spec.strategy not in STRATEGIES:
        raise SplitError(f"strategy must be one of {list(STRATEGIES)}, got {spec.strategy!r}")
    if spec.strategy != "predefined" and not 0.0 < spec.test_size < 1.0:
        raise SplitError(f"test_size must be strictly between 0 and 1, got {spec.test_size}")
    if spec.strategy == "group" and not spec.group_column:
        raise SplitError("strategy 'group' requires group_column")
    if spec.strategy == "time" and not spec.time_column:
        raise SplitError("strategy 'time' requires time_column")
    if spec.strategy == "predefined" and not (spec.split_column or spec.test_path):
        raise SplitError("strategy 'predefined' requires split_column or test_path")


def spec_sha256(spec: SplitSpec, data_sha256: str) -> str:
    """Identity of *this split of this file* -- the spec alone isn't enough.

    Hashing the data digest in too makes the value mean "these exact rows,
    divided this exact way". A spec hash that ignored the file would match
    across two different datasets and quietly license comparing metrics that
    have nothing to do with each other.
    """
    payload = json.dumps({"spec": spec.as_dict(), "data_sha256": data_sha256}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_for_spec(data_path: str, spec: SplitSpec) -> tuple[pd.DataFrame, SplitSpec]:
    """Read the dataset, folding a separate ``test_path`` file in as a marked column.

    Collapsing the two-file case into the one-column case up front means
    :func:`apply` has a single predefined code path, and the audit and hashing
    below see one frame regardless of how the caller supplied the split.
    """
    frame = load_frame(data_path)
    if spec.strategy != "predefined" or not spec.test_path:
        return frame, spec

    try:
        test_frame = load_frame(spec.test_path)
    except DataError as e:
        raise SplitError(f"test_path: {e}") from e
    missing = sorted(set(frame.columns) - set(test_frame.columns))
    if missing:
        raise SplitError(f"test_path is missing column(s) present in the training file: {missing[:10]}")
    combined = pd.concat(
        [
            frame.assign(**{_MARKER: "train"}),
            test_frame.reindex(columns=frame.columns).assign(**{_MARKER: "test"}),
        ],
        ignore_index=True,
    )
    return combined, replace(spec, split_column=_MARKER)


def _column(frame: pd.DataFrame, name: str, role: str) -> pd.Series:
    if name not in frame.columns:
        cols = ", ".join(map(str, frame.columns[:25]))
        raise SplitError(f"{role} {name!r} not in dataset; columns are: {cols}")
    return frame[name]


def reserved_columns(spec: SplitSpec, target_column: str) -> set[str]:
    """Columns that are bookkeeping, not features.

    A group or split-marker column left in X is a leak in its own right -- a
    one-hot encoded patient id is a lookup table for that patient's outcome --
    and a raw timestamp used as a feature by a model that will be applied to
    later dates is extrapolation dressed up as a variable. All three are
    removed here rather than trusted to the caller.
    """
    return {c for c in (target_column, spec.group_column, spec.split_column, spec.time_column) if c}


def apply_spec(frame: pd.DataFrame, target_column: str, spec: SplitSpec, *, classification: bool) -> Split:
    """Divide *frame* per *spec* into a :class:`Split`."""
    if target_column not in frame.columns:
        cols = ", ".join(map(str, frame.columns[:25]))
        raise SplitError(f"target column {target_column!r} not in dataset; columns are: {cols}")

    handler = {"random": _random, "group": _group, "time": _time, "predefined": _predefined}[spec.strategy]
    train_idx, test_idx, info = handler(frame, target_column, spec, classification)
    if len(train_idx) == 0 or len(test_idx) == 0:
        raise SplitError(f"split produced an empty side (train={len(train_idx)}, test={len(test_idx)})")

    reserved = reserved_columns(spec, target_column)
    features = [c for c in frame.columns if c not in reserved]
    if not features:
        raise SplitError(
            "dataset has no feature columns left after excluding the target and split "
            f"bookkeeping columns {sorted(reserved)}"
        )
    info["excluded_from_features"] = sorted(str(c) for c in reserved - {target_column})
    if spec.strategy != "group":
        # The caller who most needs to hear that an entity straddles the split
        # is the one who never mentioned a group column, so the check runs
        # unprompted against anything that looks like an id.
        info["unrequested_group_overlap"] = [
            {"column": name, "n_shared": shared, "n_groups": int(frame[name].nunique())}
            for name in profile.likely_group_columns(frame)
            if name not in reserved
            and (shared := len(set(frame.loc[train_idx, name]) & set(frame.loc[test_idx, name])))
        ]

    groups = frame[spec.group_column] if spec.group_column and spec.group_column in frame.columns else None
    return Split(
        x_train=frame.loc[train_idx, features].reset_index(drop=True),
        y_train=frame.loc[train_idx, target_column].reset_index(drop=True),
        x_test=frame.loc[test_idx, features].reset_index(drop=True),
        y_test=frame.loc[test_idx, target_column].reset_index(drop=True),
        groups_train=None if groups is None else groups.loc[train_idx].reset_index(drop=True),
        groups_test=None if groups is None else groups.loc[test_idx].reset_index(drop=True),
        info=info,
    )


def _random(
    frame: pd.DataFrame, target_column: str, spec: SplitSpec, classification: bool
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    target = frame[target_column]
    # Stratification is dropped, not fatal, when a class has a single row -- a
    # hard error there is a confusing way to learn that one class has n=1.
    can_stratify = classification and spec.stratify and target.value_counts().min() >= 2
    train_idx, test_idx = train_test_split(
        frame.index.to_numpy(),
        test_size=spec.test_size,
        random_state=spec.random_seed,
        stratify=target if can_stratify else None,
    )
    return train_idx, test_idx, {"strategy": "random", "stratified": bool(can_stratify)}


def _group(
    frame: pd.DataFrame, target_column: str, spec: SplitSpec, classification: bool
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    groups = _column(frame, spec.group_column, "group_column")
    n_groups = int(groups.nunique(dropna=False))
    if n_groups < 2:
        raise SplitError(f"group_column {spec.group_column!r} has only {n_groups} distinct value(s)")

    target = frame[target_column]
    stratified = classification and spec.stratify and target.value_counts().min() >= 2
    if stratified:
        # StratifiedGroupKFold keeps whole groups together AND balances the
        # classes; one of its k folds is the holdout, so k is derived from the
        # requested test_size rather than asked for as a separate argument.
        n_splits = int(np.clip(round(1.0 / spec.test_size), 2, n_groups))
        splitter: Any = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=spec.random_seed)
    else:
        splitter = GroupShuffleSplit(n_splits=1, test_size=spec.test_size, random_state=spec.random_seed)
    train_pos, test_pos = next(iter(splitter.split(frame, target, groups=groups)))

    index = frame.index.to_numpy()
    return (
        index[train_pos],
        index[test_pos],
        {
            "strategy": "group",
            "group_column": spec.group_column,
            "stratified": bool(stratified),
            "n_groups": n_groups,
            # Whole groups can't be sliced, so the realized fraction lands near
            # test_size rather than on it. Reported so nobody reads a 0.24
            # holdout as the 0.20 they asked for.
            "requested_test_size": spec.test_size,
            "achieved_test_size": round(len(test_pos) / len(frame), 4),
        },
    )


def _time(
    frame: pd.DataFrame, target_column: str, spec: SplitSpec, classification: bool
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    raw = _column(frame, spec.time_column, "time_column")
    order = raw if pd.api.types.is_numeric_dtype(raw) else pd.to_datetime(raw, errors="coerce")
    if order.isna().any():
        raise SplitError(
            f"time_column {spec.time_column!r} has {int(order.isna().sum())} value(s) that are "
            "neither numbers nor parseable dates"
        )

    ordered = order.sort_values(kind="stable")
    cut = int(np.clip(np.floor(len(ordered) * (1.0 - spec.test_size)), 1, len(ordered) - 1))
    # Rows sharing the boundary value must not straddle it: the same instant on
    # both sides is a leak with a timestamp on it. The cut moves forward to the
    # next distinct value, the conservative direction (more training data, a
    # strictly-later test set).
    later = ordered[ordered > ordered.iloc[cut]]
    if later.empty:
        raise SplitError(
            f"time_column {spec.time_column!r} has no values after the cutoff -- "
            "too few distinct timestamps for a temporal split"
        )
    test_idx = later.index.to_numpy()
    train_idx = np.setdiff1d(ordered.index.to_numpy(), test_idx, assume_unique=True)
    return (
        train_idx,
        test_idx,
        {
            "strategy": "time",
            "time_column": spec.time_column,
            "stratified": False,
            "train_period": [str(order.loc[train_idx].min()), str(order.loc[train_idx].max())],
            "test_period": [str(later.min()), str(later.max())],
            "requested_test_size": spec.test_size,
            "achieved_test_size": round(len(test_idx) / len(ordered), 4),
        },
    )


def _predefined(
    frame: pd.DataFrame, target_column: str, spec: SplitSpec, classification: bool
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    marker = _column(frame, spec.split_column, "split_column").astype(str).str.strip().str.lower()
    seen = set(marker.unique())
    if not seen <= {"train", "test"}:
        raise SplitError(
            f"split_column {spec.split_column!r} must contain only 'train'/'test', found {sorted(seen)}"
        )
    index = frame.index.to_numpy()
    return (
        index[(marker == "train").to_numpy()],
        index[(marker == "test").to_numpy()],
        {
            "strategy": "predefined",
            "source": "test_path" if spec.split_column == _MARKER else spec.split_column,
            "stratified": False,
        },
    )


def audit(split: Split, spec: SplitSpec, *, classification: bool) -> dict[str, Any]:
    """Leakage checks on the realized split, reported with every fit.

    Cheap to compute, and the only thing standing between a caller and a 0.99
    AUC they believe. Each entry is a fact; ``warnings`` collects the ones that
    are a known way for a split to be wrong.
    """
    warnings: list[str] = []
    report: dict[str, Any] = {
        "n_train": int(len(split.x_train)),
        "n_test": int(len(split.x_test)),
        **{k: v for k, v in split.info.items() if k != "excluded_from_features"},
        "excluded_from_features": split.info.get("excluded_from_features", []),
    }

    if classification:
        report["train_class_distribution"] = {str(k): int(v) for k, v in split.y_train.value_counts().items()}
        report["test_class_distribution"] = {str(k): int(v) for k, v in split.y_test.value_counts().items()}
        if split.y_test.nunique() < 2:
            warnings.append("the test split contains a single class -- ROC-AUC is undefined on it")
        unseen = sorted(set(split.y_test.unique()) - set(split.y_train.unique()))
        if unseen:
            warnings.append(f"class(es) {unseen} appear only in the test split and cannot be predicted")
        rarest = int(split.y_train.value_counts().min())
        if rarest < 10:
            warnings.append(f"the rarest training class has only {rarest} rows -- the fit will be unstable")

    # Identical feature rows on both sides: the same record scored twice, once
    # to learn from and once to be graded on.
    train_hashes = set(pd.util.hash_pandas_object(split.x_train, index=False))
    overlap = int(pd.util.hash_pandas_object(split.x_test, index=False).isin(train_hashes).sum())
    report["duplicate_feature_rows_in_both_splits"] = overlap
    if overlap:
        warnings.append(
            f"{overlap} of {len(split.x_test)} test rows have feature values identical to a training row "
            "-- metrics are inflated; deduplicate, or use a group/time split"
        )

    for candidate in split.info.get("unrequested_group_overlap", []):
        warnings.append(
            f"{candidate['n_shared']} of {candidate['n_groups']} value(s) of {candidate['column']!r} "
            f"appear in BOTH splits -- that column looks like an entity id, so this split may be leaking; "
            f'use split_json {{"strategy": "group", "group_column": "{candidate["column"]}"}} if so'
        )

    if split.groups_train is not None and split.groups_test is not None:
        shared = sorted(set(split.groups_train) & set(split.groups_test))
        report["groups_in_both_splits"] = len(shared)
        report["n_groups_train"] = int(split.groups_train.nunique())
        report["n_groups_test"] = int(split.groups_test.nunique())
        if shared:
            warnings.append(
                f"{len(shared)} value(s) of {spec.group_column!r} appear in BOTH splits "
                f"(e.g. {shared[:3]}) -- use strategy 'group' to keep them together"
            )

    if len(split.x_test) < 30:
        warnings.append(f"the test split has only {len(split.x_test)} rows -- any metric from it is very noisy")

    report["warnings"] = warnings
    return report


def fold_indices(
    features: pd.DataFrame,
    y: pd.Series,
    spec: SplitSpec,
    n_splits: int,
    *,
    groups: pd.Series | None = None,
    classification: bool = True,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Cross-validation folds that respect the same grouping the holdout does.

    A grouped holdout with plain k-fold CV underneath it is a half-measure: the
    CV numbers -- and any threshold or hyperparameter chosen from them -- come
    back leak-inflated even though the final test split is clean. So the fold
    generator is derived from the same spec.
    """
    stratify = classification and spec.stratify and int(y.value_counts().min()) >= n_splits
    if groups is not None:
        if int(groups.nunique()) < n_splits:
            raise SplitError(
                f"{groups.nunique()} distinct {spec.group_column!r} values cannot fill {n_splits} folds"
            )
        splitter: Any = (
            StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=spec.random_seed)
            if stratify
            else GroupKFold(n_splits=n_splits)
        )
        return list(splitter.split(features, y, groups=groups))
    if stratify:
        return list(StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=spec.random_seed).split(features, y))
    return list(KFold(n_splits=n_splits, shuffle=True, random_state=spec.random_seed).split(features))
