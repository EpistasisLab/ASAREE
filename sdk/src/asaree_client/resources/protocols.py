"""Protocol resource, matching asaree.api.protocols."""

from __future__ import annotations

import builtins
import uuid
from typing import Any

from asaree_client.models import CellRunBatch, Protocol, ProtocolRevision, ProtocolRun

ResourceId = uuid.UUID | str


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
        name: str | None = None,
        description: str | None = None,
        experiment_id: ResourceId | None = None,
        graph: dict[str, Any] | None = None,
    ) -> Protocol:
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if experiment_id is not None:
            payload["experiment_id"] = str(experiment_id)
        if graph is not None:
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
        cell_label: str | None = None,
    ) -> ProtocolRun:
        """Compile and run this protocol's current graph -- 422 if it's
        empty or has a cycle. Returns immediately with status "pending";
        poll with get_run. ``replicate_label`` runs that one already-generated
        replicate (its cell's factor_values substituted in) instead of
        today's ad-hoc, un-substituted whole-graph run."""
        if replicate_label is not None and cell_label is not None and replicate_label != cell_label:
            raise ValueError("replicate_label and deprecated cell_label disagree")
        effective_label = replicate_label if replicate_label is not None else cell_label
        payload = {"replicate_label": effective_label} if effective_label is not None else {}
        data = self._client._post(f"/protocols/{protocol_id}/runs", json=payload)
        return ProtocolRun(**data)

    def run_node(self, protocol_id: ResourceId, node_id: str) -> ProtocolRun:
        """The canvas's per-node Play icon -- runs one Agent node in
        isolation, no factor substitution. 422 if the node has upstream
        input or isn't a runnable Agent (validate_single_node_runnable)."""
        data = self._client._post(f"/protocols/{protocol_id}/nodes/{node_id}/run")
        return ProtocolRun(**data)

    def run_cells(self, protocol_id: ResourceId) -> CellRunBatch:
        """"Run all cells" -- one ProtocolRun per not-yet-scored cell under
        this protocol's linked experiment, each cell's own factor_values
        substituted in. 422 if there's no linked experiment or the graph
        doesn't have exactly one final node."""
        data = self._client._post(f"/protocols/{protocol_id}/cell-runs")
        return CellRunBatch(**data)

    def get_run(self, protocol_id: ResourceId, run_id: ResourceId) -> ProtocolRun:
        data = self._client._get(f"/protocols/{protocol_id}/runs/{run_id}")
        return ProtocolRun(**data)

    def list_runs(self, protocol_id: ResourceId) -> builtins.list[ProtocolRun]:
        data = self._client._get(f"/protocols/{protocol_id}/runs")
        return [ProtocolRun(**r) for r in data]
