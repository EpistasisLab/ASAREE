"""Preserve legacy file evidence without rewriting files or inventing history."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from asaree.security.hashing import sha256_file

logger = logging.getLogger(__name__)


def legacy_artifact(
    *, dataset_id: uuid.UUID, path: str, expected_hash: str | None, format: str, root: Path
) -> dict[str, Any]:
    file = Path(path).resolve()
    root = root.resolve()
    artifact_id = uuid.uuid4()
    external = not file.is_relative_to(root)
    if external:
        try:
            file.stat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError(
                f"Legacy dataset file {file} is outside dataset storage {root}; configure its original root"
            )
        # No bytes exist to import. Reserve a managed key, retaining the old
        # location only as evidence; never resolve a managed reference outside root.
        key = f"{dataset_id}/unavailable/{artifact_id}/{file.name}"
    else:
        key = file.relative_to(root).as_posix()
    artifact: dict[str, Any] = dict(
        id=artifact_id, dataset_id=dataset_id, namespace="datasets", storage_key=key,
        legacy_path=path if external else None,
        sha256=expected_hash, byte_size=None, format=format, status="missing",
    )
    if external:
        logger.warning("Legacy dataset %s artifact %s: missing (outside managed storage)", dataset_id, path)
        return artifact
    try:
        observed = sha256_file(file)
        artifact.update(byte_size=file.stat().st_size, sha256=expected_hash or observed)
        artifact["status"] = "hash_mismatch" if expected_hash and expected_hash != observed else "ready"
    except OSError:
        artifact["status"] = "missing"
    if artifact["status"] != "ready":
        logger.warning("Legacy dataset %s artifact %s: %s", dataset_id, path, artifact["status"])
    return artifact


def preserve_legacy_splits(connection: Connection, root: Path) -> None:
    metadata = sa.MetaData()
    datasets = sa.Table("registered_datasets", metadata, autoload_with=connection)
    artifacts = sa.Table("dataset_artifacts", metadata, autoload_with=connection)
    splits = sa.Table("dataset_splits", metadata, autoload_with=connection)
    # Only metadata is read; file hashing is streamed by sha256_file.
    for row in connection.execute(sa.select(datasets)).mappings():
        updates: dict[str, Any] = {}
        source_hash = row["raw_sha256"]
        if row["raw_path"] and not row["raw_artifact_id"]:
            artifact = legacy_artifact(
                dataset_id=row["id"], path=row["raw_path"], expected_hash=source_hash, format="csv", root=root
            )
            connection.execute(artifacts.insert().values(**artifact))
            source_hash = source_hash or artifact["sha256"]
            updates.update(raw_artifact_id=artifact["id"], raw_sha256=source_hash, raw_path=None)
        if not row["current_split_id"] and (row["train_path"] or row["test_path"]):
            partition_ids: dict[str, uuid.UUID | None] = {}
            for partition in ("train", "test"):
                path = row[f"{partition}_path"]
                partition_ids[f"{partition}_artifact_id"] = None
                if path:
                    artifact = legacy_artifact(
                        dataset_id=row["id"], path=path, expected_hash=row[f"{partition}_sha256"],
                        format="parquet", root=root,
                    )
                    connection.execute(artifacts.insert().values(**artifact))
                    partition_ids[f"{partition}_artifact_id"] = artifact["id"]
            split_id = uuid.uuid4()
            connection.execute(splits.insert().values(
                id=split_id, dataset_id=row["id"], source_sha256=source_hash, legacy=True,
                method=row["split_method"], group_column=row["split_group_column"],
                test_size=row["split_test_size"], seed=row["split_seed"], **partition_ids,
            ))
            # Effective target, stratification and creation time were never independently recorded.
            updates["current_split_id"] = split_id
        if updates:
            connection.execute(datasets.update().where(datasets.c.id == row["id"]).values(**updates))


def restore_legacy_paths(connection: Connection, root: Path) -> None:
    """Restore current compatibility paths for rollback; retain every file on disk."""
    metadata = sa.MetaData()
    datasets = sa.Table("registered_datasets", metadata, autoload_with=connection)
    artifacts = sa.Table("dataset_artifacts", metadata, autoload_with=connection)
    splits = sa.Table("dataset_splits", metadata, autoload_with=connection)

    def artifact_path(artifact_id: uuid.UUID | None) -> tuple[str | None, str | None]:
        if artifact_id is None:
            return None, None
        artifact = connection.execute(sa.select(artifacts).where(artifacts.c.id == artifact_id)).mappings().one()
        if artifact.get("legacy_path"):
            return artifact["legacy_path"], artifact["sha256"]
        path = (root.resolve() / artifact["storage_key"]).resolve()
        if artifact["namespace"] != "datasets" or not path.is_relative_to(root.resolve()):
            raise ValueError("Invalid dataset artifact reference during downgrade")
        return str(path), artifact["sha256"]

    for row in connection.execute(sa.select(datasets)).mappings():
        updates: dict[str, Any] = {}
        if row["raw_artifact_id"]:
            updates["raw_path"], _ = artifact_path(row["raw_artifact_id"])
        if row["current_split_id"]:
            split = connection.execute(sa.select(splits).where(splits.c.id == row["current_split_id"])).mappings().one()
            for part in ("train", "test"):
                updates[f"{part}_path"], updates[f"{part}_sha256"] = artifact_path(split[f"{part}_artifact_id"])
            updates.update(
                split_method=split["method"], split_group_column=split["group_column"],
                split_test_size=split["test_size"], split_seed=split["seed"],
            )
            if not split["legacy"]:
                updates["target_column"] = split["target_column"]
        if updates:
            connection.execute(datasets.update().where(datasets.c.id == row["id"]).values(**updates))
