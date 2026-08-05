"""Dataset registry resource, matching asaree.api.datasets.

Registration is a single synchronous multipart call — ASAREE splits
train/test inline on the request, there is no job to poll (unlike ARES's
``datasets.process``).
"""

from __future__ import annotations

import uuid
from typing import Any

from asaree_client.models import RegisteredDataset

ResourceId = uuid.UUID | str


class Datasets:
    def __init__(self, client: Any) -> None:
        self._client = client

    def create(
        self,
        name: str,
        file_path: str,
        *,
        target_column: str | None = None,
        group_column: str | None = None,
        description: str | None = None,
        dictionary_json: str | None = None,
        test_size: float = 0.2,
        seed: int = 0,
    ) -> RegisteredDataset:
        form: dict[str, Any] = {"name": name, "test_size": str(test_size), "seed": str(seed)}
        if target_column is not None:
            form["target_column"] = target_column
        if group_column is not None:
            form["group_column"] = group_column
        if description is not None:
            form["description"] = description
        if dictionary_json is not None:
            form["dictionary_json"] = dictionary_json
        with open(file_path, "rb") as f:
            data = self._client._post("/datasets", data=form, files={"file": (file_path, f)})
        return RegisteredDataset(**data)

    def get_by_name(self, name: str) -> RegisteredDataset:
        data = self._client._get(f"/datasets/by-name/{name}")
        return RegisteredDataset(**data)

    def delete(self, dataset_id: ResourceId) -> None:
        self._client._delete(f"/datasets/{dataset_id}")
