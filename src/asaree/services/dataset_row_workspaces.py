"""Materialize private, run-scoped CSV snapshots for Dataset row inputs."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from asaree.config import get_settings
from asaree.services.dataset_row_csv import (
    DatasetRowCsvError,
    RowSource,
    materialize_row_csv,
    project_row,
)


def row_attempt_workspace_id(run_id: uuid.UUID) -> str:
    """Return the opaque workspace id for this immutable protocol attempt."""
    if not isinstance(run_id, uuid.UUID):
        raise DatasetRowCsvError("invalid_run_id", "run id must be a UUID")
    return f"_protocol_runs/{run_id}"


def _workspace_root() -> Path:
    # Keep attempt data beside registered datasets while separating it from
    # their immutable originals and the shared whole-dataset workspaces.
    return Path(get_settings().dataset_storage_dir).resolve().parent


def _node_directory(root: Path, workspace_id: str, agent_node_id: str) -> Path:
    key = hashlib.sha256(agent_node_id.encode("utf-8")).hexdigest()
    attempt_root = (root / workspace_id).resolve()
    directory = (attempt_root / "agents" / key).resolve()
    if not directory.is_relative_to(attempt_root):
        raise DatasetRowCsvError("invalid_agent_node_id", "Agent node directory escapes attempt root")
    return directory


def prepare_agent_row_inputs(
    *,
    run_id: uuid.UUID,
    agent_node_id: str,
    source: RowSource,
    bindings: Sequence[dict],
) -> dict[str, Any]:
    """Create exact one-row CSV views for the requested Agent's driver edges."""
    workspace_id = row_attempt_workspace_id(run_id)
    if not isinstance(agent_node_id, str) or not agent_node_id:
        raise DatasetRowCsvError("invalid_agent_node_id", "Agent node id must be a nonempty string")
    try:
        source_id = str(uuid.UUID(source.dataset_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise DatasetRowCsvError("source_identity_mismatch", "row source dataset id must be a UUID") from exc

    selected: list[dict] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            raise DatasetRowCsvError("invalid_binding", "row input binding must be an object")
        if binding.get("agent_node_id") != agent_node_id:
            continue
        try:
            binding_id = str(uuid.UUID(binding.get("dataset_id")))
        except (ValueError, AttributeError, TypeError) as exc:
            raise DatasetRowCsvError("source_identity_mismatch", "binding dataset id must be a UUID") from exc
        if binding_id != source_id:
            raise DatasetRowCsvError("source_identity_mismatch", "binding dataset does not match row source")
        selected.append(binding)

    if not selected:
        return {"workspace_id": workspace_id, "row_inputs": []}

    views: dict[str, dict] = {}
    for binding in selected:
        name = binding.get("dataset_name")
        columns = binding.get("columns")
        target = binding.get("target_column")
        if not isinstance(name, str):
            raise DatasetRowCsvError("invalid_binding", "dataset_name must be a string")
        if target is None:
            target = ""
        if not isinstance(target, str):
            raise DatasetRowCsvError("invalid_binding", "target_column must be a string or null")
        view = project_row(source, row_index=binding.get("row_index"), columns=columns)
        if target not in view["columns"]:
            target = ""
        identity = json.dumps(
            {"dataset_id": binding_id, "row_index": view["row_index"], "columns": view["columns"], "target": target},
            sort_keys=True,
            separators=(",", ":"),
        )
        record = {"binding": binding, "view": view, "target": target, "name": name}
        previous = views.get(identity)
        if previous is not None:
            if previous["name"] != name:
                raise DatasetRowCsvError("conflicting_agent_views", "duplicate row views have different names")
            continue
        # One Agent gets one unambiguous view of a driver for this run.
        if views:
            raise DatasetRowCsvError("conflicting_agent_views", "Agent row bindings select different views")
        views[identity] = record

    directory = _node_directory(_workspace_root(), workspace_id, agent_node_id)
    row_inputs: list[dict] = []
    for record in views.values():
        view = record["view"]
        token = hashlib.sha256(
            json.dumps(
                {
                    "dataset_id": source_id,
                    "raw_sha256": source.raw_sha256,
                    "row_index": view["row_index"],
                    "columns": view["columns"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        destination = directory / f"{token}.csv"
        manifest = directory / "row-view.json"
        manifest_value = json.dumps(
            {
                "dataset_id": source_id,
                "raw_sha256": source.raw_sha256,
                "row_index": view["row_index"],
                "columns": view["columns"],
                "values": view["values"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        if manifest.exists():
            try:
                if manifest.read_text(encoding="utf-8") != manifest_value:
                    raise DatasetRowCsvError(
                        "workspace_conflict", "Agent workspace already contains a different source view"
                    )
            except OSError as exc:
                raise DatasetRowCsvError("source_unavailable", "Agent workspace cannot be verified") from exc
        elif directory.exists() and any(directory.iterdir()):
            raise DatasetRowCsvError("workspace_conflict", "Agent workspace contains unrecognized files")
        # Let the CSV writer quote fields, then compare with an independently
        # materialized candidate so existing same-run files are never trusted.
        if destination.exists():
            temp_view = directory / f".{token}.verify.csv"
            try:
                materialize_row_csv(view, destination=temp_view)
                expected_bytes = temp_view.read_bytes()
                temp_view.unlink()
            except OSError as exc:
                raise DatasetRowCsvError("source_unavailable", "row snapshot cannot be verified") from exc
            if destination.read_bytes() != expected_bytes:
                raise DatasetRowCsvError(
                    "workspace_conflict", "existing row snapshot differs from requested source view"
                )
        else:
            materialize_row_csv(view, destination=destination)
        if not manifest.exists():
            try:
                manifest.write_text(manifest_value, encoding="utf-8")
            except OSError as exc:
                raise DatasetRowCsvError("source_unavailable", "Agent workspace metadata cannot be written") from exc
        row_inputs.append(
            {
                "name": record["name"],
                "dataset_id": source_id,
                "raw_sha256": source.raw_sha256,
                "row_index": view["row_index"],
                "columns": view["columns"],
                "values": view["values"],
                "path": str(destination),
                "mode": "per_row",
                "target_column": record["target"],
            }
        )
    return {"workspace_id": workspace_id, "row_inputs": row_inputs}
