"""Protocol resource, matching asaree.api.protocols."""

from __future__ import annotations

import builtins
import uuid
from typing import Any

from asaree_client.models import CellRunBatch, PromptPreview, Protocol, ProtocolRevision, ProtocolRun, TestRun

ResourceId = uuid.UUID | str
_UNSET: Any = object()


class Protocols:
    def __init__(self, client: Any) -> None:
        self._client = client

    def create(
        self,
        *,
        name: str,
        description: str | None = None,
        experiment_id: ResourceId | None = None,
        graph: dict[str, Any] | None = None,
    ) -> Protocol:
        payload: dict[str, Any] = {"name": name}
        if description is not None:
            payload["description"] = description
        if experiment_id is not None:
            payload["experiment_id"] = str(experiment_id)
        if graph is not None:
            payload["graph"] = graph
        data = self._client._post("/protocols", json=payload)
        return Protocol(**data)

    def get(self, protocol_id: ResourceId) -> Protocol:
        data = self._client._get(f"/protocols/{protocol_id}")
        return Protocol(**data)

    def list(self, *, experiment_id: ResourceId | None = None) -> builtins.list[Protocol]:
        params = {"experiment_id": str(experiment_id)} if experiment_id is not None else None
        data = self._client._get("/protocols", params=params)
        return [Protocol(**p) for p in data]

    def update(
        self,
        protocol_id: ResourceId,
        *,
        name: str | None = _UNSET,
        description: str | None = _UNSET,
        experiment_id: ResourceId | None = _UNSET,
        graph: dict[str, Any] | None = _UNSET,
    ) -> Protocol:
        payload: dict[str, Any] = {}
        if name is not _UNSET:
            payload["name"] = name
        if description is not _UNSET:
            payload["description"] = description
        if experiment_id is not _UNSET:
            payload["experiment_id"] = str(experiment_id) if experiment_id else None
        if graph is not _UNSET:
            payload["graph"] = graph
        data = self._client._patch(f"/protocols/{protocol_id}", json=payload)
        return Protocol(**data)

    def delete(self, protocol_id: ResourceId) -> None:
        self._client._delete(f"/protocols/{protocol_id}")

    def publish(self, protocol_id: ResourceId) -> Protocol:
        """Freeze the autosaved draft as the version future production runs use."""
        data = self._client._post(f"/protocols/{protocol_id}/publish")
        return Protocol(**data)

    def get_revision(self, protocol_id: ResourceId, revision_id: ResourceId) -> ProtocolRevision:
        """Return the immutable canvas snapshot an execution references."""
        data = self._client._get(f"/protocols/{protocol_id}/revisions/{revision_id}")
        return ProtocolRevision(**data)

    def run(
        self,
        protocol_id: ResourceId,
        *,
        replicate_label: str | None = None,
    ) -> ProtocolRun:
        """Run this protocol's immutable published revision -- 409 if it has
        never been published, and 422 if the published graph is invalid,
        empty, or has a cycle. Returns immediately with status "pending";
        poll with get_run. ``replicate_label`` runs that one already-generated
        replicate (its cell's factor_values substituted in) instead of
        today's ad-hoc, un-substituted whole-graph run."""
        payload = {"replicate_label": replicate_label} if replicate_label is not None else {}
        data = self._client._post(f"/protocols/{protocol_id}/runs", json=payload)
        return ProtocolRun(**data)

    def run_node(self, protocol_id: ResourceId, node_id: str) -> ProtocolRun:
        """The canvas's per-node Play icon -- runs one Agent node in
        isolation, no factor substitution. 422 if the node has upstream
        input or isn't a runnable Agent (validate_single_node_runnable)."""
        data = self._client._post(f"/protocols/{protocol_id}/nodes/{node_id}/run")
        return ProtocolRun(**data)

    def preview_prompt(
        self, protocol_id: ResourceId, node_id: str, *, graph: dict[str, Any] | None = None
    ) -> PromptPreview:
        payload = {"graph": graph} if graph is not None else {}
        data = self._client._post(f"/protocols/{protocol_id}/nodes/{node_id}/prompt-preview", json=payload)
        return PromptPreview(**data)

    def run_cells(
        self,
        protocol_id: ResourceId,
        *,
        replicate_labels: builtins.list[str] | None = None,
        rerun_replicate_labels: builtins.list[str] | None = None,
    ) -> CellRunBatch:
        """ "Run all cells" -- one ProtocolRun per not-yet-completed replicate under
        this protocol's linked experiment, each replicate's cell factor_values
        substituted in. 422 if there's no linked experiment or the graph
        doesn't have exactly one final node."""
        payload: dict[str, Any] = {}
        if replicate_labels is not None:
            payload["replicate_labels"] = replicate_labels
        if rerun_replicate_labels is not None:
            payload["rerun_replicate_labels"] = rerun_replicate_labels
        kwargs = {"json": payload} if payload else {}
        data = self._client._post(f"/protocols/{protocol_id}/cell-runs", **kwargs)
        return CellRunBatch(**data)

    def start_test_run(self, protocol_id: ResourceId) -> TestRun:
        data = self._client._post(f"/protocols/{protocol_id}/test-runs")
        return TestRun(**data)

    def get_latest_test_run(self, protocol_id: ResourceId) -> TestRun:
        data = self._client._get(f"/protocols/{protocol_id}/test-runs/latest")
        return TestRun(**data)

    def get_run(self, protocol_id: ResourceId, run_id: ResourceId) -> ProtocolRun:
        data = self._client._get(f"/protocols/{protocol_id}/runs/{run_id}")
        return ProtocolRun(**data)

    def list_runs(self, protocol_id: ResourceId) -> builtins.list[ProtocolRun]:
        data = self._client._get(f"/protocols/{protocol_id}/runs")
        return [ProtocolRun(**r) for r in data]

    def cancel_run(self, protocol_id: ResourceId, run_id: ResourceId) -> ProtocolRun:
        data = self._client._post(f"/protocols/{protocol_id}/runs/{run_id}/cancel")
        return ProtocolRun(**data)
