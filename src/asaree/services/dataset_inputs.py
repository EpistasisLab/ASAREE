"""Freeze dataset selections and resolve only the immutable identities recorded."""

from __future__ import annotations

import hashlib
import itertools
import json
import uuid
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.dataset import RegisteredDataset
from asaree.models.dataset_input_reference import DatasetInputReference
from asaree.models.dataset_split import DatasetArtifact, DatasetSplit
from asaree.services.dataset_row_inputs import DatasetRowInputError
from asaree.services.datasets import get_dataset_by_name


class DatasetInputError(ValueError):
    """An unavailable, unverified or incompatible pinned input."""


def _verify_file(path: str | None, digest: str | None) -> None:
    if not path or not digest:
        raise DatasetInputError("dataset_input.source_unavailable: input path or hash is missing")
    try:
        with Path(path).open("rb") as stream:
            observed = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise DatasetInputError("dataset_input.source_unavailable: pinned input file is missing") from exc
    if observed != digest:
        raise DatasetInputError("dataset_input.hash_mismatch: pinned input bytes changed")


def _artifact_pin(artifact: DatasetArtifact | None) -> dict[str, Any] | None:
    if artifact is None:
        return None
    if artifact.status != "ready":
        raise DatasetInputError("dataset_input.source_unavailable: artifact is not ready")
    _verify_file(artifact.path, artifact.sha256)
    return {"id": str(artifact.id), "sha256": artifact.sha256}


def _dataset_edges(
    graph: dict[str, Any],
) -> Iterator[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    nodes = {node["id"]: node for node in graph.get("nodes") or []}
    for edge in graph.get("edges") or []:
        node = nodes.get(edge.get("source"), {})
        agent = nodes.get(edge.get("target"), {})
        if (node.get("type") == "dataset" and agent.get("type") in {"agent", "sub_agent"}
                and edge.get("targetHandle") in {"dataset", "resource", "tool"}):
            yield edge, node, agent


async def freeze_dataset_inputs(
    db: AsyncSession, graph: dict[str, Any], *, owner_id: uuid.UUID, design_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pin connected alternatives without enumerating the full factorial design."""
    factors = {factor["name"]: factor.get("levels") or [] for factor in (design_spec or {}).get("factors") or []}
    inputs: list[dict[str, Any]] = []
    bindings = []
    seen = set()
    from asaree.services.protocol_execution import apply_factor_bindings

    for edge, node, agent in _dataset_edges(graph):
        edge_input = (edge.get("data") or {}).get("dataset_input") or {"mode": "whole_dataset"}
        bindings.append({"dataset_node_id": node["id"], "agent_node_id": agent["id"], **deepcopy(edge_input)})
        names = list(dict.fromkeys((node.get("data") or {}).get("factor_bindings", {}).values()))
        choices = [factors.get(name, []) for name in names]
        if any(not choice for choice in choices):
            raise DatasetInputError("dataset_input.unresolved_factor: dataset factor has no declared alternatives")
        for values in itertools.product(*choices):
            variant = apply_factor_bindings({"nodes": [node], "edges": []}, dict(zip(names, values, strict=True)))
            config = (variant["nodes"][0].get("data") or {}).get("config") or {}
            connector_factor = (agent.get("data") or {}).get("factor_bindings", {}).get("dataset_selection")
            if not config.get("enabled", True) and not connector_factor:
                continue
            identity = config.get("dataset_id")
            name = config.get("dataset_name")
            if identity:
                try:
                    registration = await db.scalar(select(RegisteredDataset).where(
                        RegisteredDataset.id == uuid.UUID(identity), RegisteredDataset.owner_id == owner_id,
                    ))
                except (ValueError, TypeError, AttributeError):
                    registration = None
            else:
                registration = await get_dataset_by_name(db, name, owner_id=owner_id) if name else None
            if registration is None:
                raise DatasetRowInputError("source_unavailable", "registered dataset source is unavailable")
            if name and name != registration.name:
                raise DatasetRowInputError("invalid_dataset_config", "dataset_id and dataset_name do not agree")
            mode = edge_input["mode"]
            key = (node["id"], str(registration.id), mode)
            if key in seen:
                continue
            seen.add(key)
            _verify_file(registration.raw_path, registration.raw_sha256)
            raw = _artifact_pin(registration.raw_artifact)
            if raw and (raw["sha256"] != registration.raw_sha256 or registration.raw_artifact is None
                        or registration.raw_artifact.dataset_id != registration.id):
                raise DatasetInputError("dataset_input.identity_mismatch: source artifact does not match registration")
            split = registration.current_split if mode == "whole_dataset" else None
            if split and (split.dataset_id != registration.id or split.source_sha256 != registration.raw_sha256):
                raise DatasetInputError("dataset_input.identity_mismatch: split does not match source")
            train = _artifact_pin(split.train_artifact) if split else None
            test = _artifact_pin(split.test_artifact) if split else None
            if split and (train is None or test is None):
                raise DatasetInputError("dataset_input.source_unavailable: split artifacts are missing")
            if split and any(artifact is None or artifact.dataset_id != registration.id for artifact in (
                split.train_artifact, split.test_artifact
            )):
                raise DatasetInputError("dataset_input.identity_mismatch: split artifacts belong to another source")
            inputs.append({
                "dataset_node_id": node["id"], "dataset_id": str(registration.id),
                "dataset_name": registration.name, "mode": mode, "raw_sha256": registration.raw_sha256,
                "raw_artifact": raw, "split_id": str(split.id) if split else None,
                "train_artifact": train, "test_artifact": test,
                "target_column": (split.target_column if split else registration._target_column) or "",
                "description": registration.description, "dictionary_json": registration.dictionary_json,
            })
    return {"schema_version": 1, "provenance": "pinned", "inputs": inputs, "bindings": bindings}


def resolve_attempt_inputs(
    manifest: dict[str, Any] | None, graph: dict[str, Any], *,
    dataset_row: dict[str, Any] | None = None, target_node_id: str | None = None,
) -> dict[str, Any] | None:
    """Select from publication pins after applying this cell's factor values."""
    if manifest is None:
        return None
    inputs = []
    bindings = []
    seen = set()
    for edge, node, agent in _dataset_edges(graph):
        if target_node_id is not None and agent["id"] != target_node_id:
            continue
        data = agent.get("data") or {}
        config = (node.get("data") or {}).get("config") or {}
        selection = data.get("dataset_selection") if data.get("factor_bindings", {}).get("dataset_selection") else None
        if selection is not None:
            if config.get("dataset_id") not in selection:
                continue
        elif not config.get("enabled", True):
            continue
        mode = ((edge.get("data") or {}).get("dataset_input") or {}).get("mode", "whole_dataset")
        pin = next((pin for pin in manifest["inputs"] if pin["dataset_node_id"] == node["id"]
                    and pin["mode"] == mode
                    and (pin["dataset_id"] == str(config["dataset_id"]) if config.get("dataset_id")
                         else pin["dataset_name"] == config.get("dataset_name"))), None)
        if pin is None:
            raise DatasetInputError("dataset_input.unpinned_selection: selected dataset was not pinned; republish")
        key = (agent["id"], pin["dataset_id"])
        if key in seen:
            continue
        seen.add(key)
        if pin not in inputs:
            inputs.append(deepcopy(pin))
        binding = {"agent_node_id": agent["id"], "dataset_node_id": node["id"],
                   "dataset_id": pin["dataset_id"], "mode": mode}
        if mode == "per_row":
            columns = (edge["data"]["dataset_input"])["columns"]
            if (dataset_row is None or dataset_row["dataset_id"] != pin["dataset_id"]
                    or dataset_row["raw_sha256"] != pin["raw_sha256"]):
                raise DatasetInputError("dataset_input.identity_mismatch: row snapshot differs from pinned source")
            binding.update(row_index=dataset_row["row_index"], columns=list(columns),
                           values={column: dataset_row["values"][column] for column in columns})
        bindings.append(binding)
    return {"schema_version": 1, "provenance": "pinned", "inputs": inputs, "bindings": bindings}


def retain_inputs(db: AsyncSession, manifest: dict[str, Any] | None, *,
                  publication_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None) -> None:
    if manifest is None:
        return
    seen = set()
    for pin in manifest["inputs"]:
        artifacts = [pin[key] for key in ("raw_artifact", "train_artifact", "test_artifact") if pin[key]] or [None]
        for artifact in artifacts:
            key = (pin["dataset_id"], pin["split_id"], artifact["id"] if artifact else None)
            if key in seen:
                continue
            seen.add(key)
            db.add(DatasetInputReference(
                publication_id=publication_id, run_id=run_id, dataset_id=uuid.UUID(pin["dataset_id"]),
                split_id=uuid.UUID(pin["split_id"]) if pin["split_id"] else None,
                artifact_id=uuid.UUID(artifact["id"]) if artifact else None,
            ))


async def resolve_pinned_registration(db: AsyncSession, pin: dict[str, Any], owner_id: uuid.UUID) -> dict[str, Any]:
    registration = await db.scalar(select(RegisteredDataset).where(
        RegisteredDataset.id == uuid.UUID(pin["dataset_id"]), RegisteredDataset.owner_id == owner_id,
    ))
    if registration is None or registration.raw_sha256 != pin["raw_sha256"]:
        raise DatasetInputError("dataset_input.identity_mismatch: pinned source is missing or changed")
    result = {key: pin[key] for key in ("description", "dictionary_json", "target_column")}
    result.update(raw_path=None, train_path=None, test_path=None)
    for key, path_key in (("raw_artifact", "raw_path"), ("train_artifact", "train_path"),
                          ("test_artifact", "test_path")):
        ref = pin[key]
        if ref is None:
            continue
        artifact = await db.get(DatasetArtifact, uuid.UUID(ref["id"]))
        if (artifact is None or artifact.dataset_id != registration.id or artifact.status != "ready"
                or artifact.sha256 != ref["sha256"]):
            raise DatasetInputError("dataset_input.identity_mismatch: pinned artifact is missing or changed")
        _verify_file(artifact.path, ref["sha256"])
        result[path_key] = artifact.path
    if pin["raw_artifact"] is None:
        # Pre-artifact registrations still have a stable dataset UUID + source hash.
        _verify_file(registration.raw_path, pin["raw_sha256"])
        result["raw_path"] = registration.raw_path
    if pin["split_id"] is not None:
        split = await db.get(DatasetSplit, uuid.UUID(pin["split_id"]))
        if (split is None or split.dataset_id != registration.id or split.source_sha256 != pin["raw_sha256"]
                or str(split.train_artifact_id) != pin["train_artifact"]["id"]
                or str(split.test_artifact_id) != pin["test_artifact"]["id"]):
            raise DatasetInputError("dataset_input.identity_mismatch: pinned split is missing or changed")
    return result


def validate_workspace_inputs(workspace_id: str, manifest: dict[str, Any], scope: dict[str, Any]) -> None:
    """Allow reuse only within the same attempt and input manifest."""
    from asaree_workspace_core import Workspace

    ws = Workspace(workspace_id)
    path = ws.dir / "dataset_inputs.json"
    expected = {"scope": scope, "dataset_inputs": manifest}
    ws.dir.mkdir(parents=True, exist_ok=True)
    if not path.exists() and ws.state_path.exists():
        raise DatasetInputError("dataset_input.workspace_conflict: existing lineage has unknown inputs")
    try:
        with path.open("x") as stream:
            json.dump(expected, stream, sort_keys=True)
    except FileExistsError:
        try:
            recorded = json.loads(path.read_text())
        except (ValueError, OSError) as exc:
            raise DatasetInputError("dataset_input.workspace_conflict: input manifest cannot be read") from exc
        if recorded != expected:
            raise DatasetInputError(
                "dataset_input.workspace_conflict: input identities or execution scope differ"
            ) from None


def attempt_workspace_id(experiment_id: uuid.UUID | None, run_id: uuid.UUID) -> str:
    return f"{experiment_id or 'attempts'}/{run_id}"


async def prepare_attempt_graph(db: AsyncSession, run: Any, graph: dict[str, Any]) -> dict[str, Any]:
    """Worker preflight, shared by queued delivery and resumed attempts."""
    from asaree.services.protocol_execution import apply_factor_bindings

    graph = apply_factor_bindings(graph, run.factor_values or {})
    manifest = run.dataset_inputs
    if manifest is None:
        for edge, node, agent in _dataset_edges(graph):
            config = (node.get("data") or {}).get("config") or {}
            data = agent.get("data") or {}
            selection = (data.get("dataset_selection")
                         if data.get("factor_bindings", {}).get("dataset_selection") else None)
            if selection is not None:
                if config.get("dataset_id") not in selection:
                    continue
            elif not config.get("enabled", True):
                continue
            mode = ((edge.get("data") or {}).get("dataset_input") or {}).get("mode", "whole_dataset")
            # Existing row snapshots are recorded evidence. Their original
            # source/hash and projections are verified by the row executor;
            # they say nothing about any historical whole-dataset selection.
            if mode == "per_row" and run.dataset_row is not None:
                continue
            raise DatasetInputError("dataset_input.legacy_unknown: historical inputs were not recorded; republish")
        return graph
    selected = resolve_attempt_inputs(manifest, graph, dataset_row=run.dataset_row, target_node_id=run.target_node_id)
    if selected != manifest:
        raise DatasetInputError("dataset_input.identity_mismatch: attempt bindings no longer match its inputs")
    registrations = {}
    for pin in manifest["inputs"]:
        registrations[pin["dataset_name"]] = await resolve_pinned_registration(db, pin, run.owner_id)
    graph["_pinned_registrations"] = registrations
    graph["_attempt_dataset_inputs"] = deepcopy(manifest)
    graph["_dataset_input_scope"] = {"run_id": str(run.id), "owner_id": str(run.owner_id),
                                    "publication_id": str(run.protocol_revision_id),
                                    "design_revision_id": str(run.design_revision_id),
                                    "replicate_result_id": str(run.replicate_result_id),
                                    "row_result_id": str(run.row_result_id)}
    return graph
