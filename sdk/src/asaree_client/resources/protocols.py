"""Protocol resource, matching asaree.api.protocols."""

from __future__ import annotations

import builtins
import uuid
from typing import Any

from asaree_client.models import Protocol, ProtocolRun

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

    def run(self, protocol_id: ResourceId) -> ProtocolRun:
        """Compile and run this protocol's current graph -- 422 if it's
        empty or has a cycle. Returns immediately with status "pending";
        poll with get_run."""
        data = self._client._post(f"/protocols/{protocol_id}/runs")
        return ProtocolRun(**data)

    def get_run(self, protocol_id: ResourceId, run_id: ResourceId) -> ProtocolRun:
        data = self._client._get(f"/protocols/{protocol_id}/runs/{run_id}")
        return ProtocolRun(**data)

    def list_runs(self, protocol_id: ResourceId) -> builtins.list[ProtocolRun]:
        data = self._client._get(f"/protocols/{protocol_id}/runs")
        return [ProtocolRun(**r) for r in data]
