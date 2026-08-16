"""Dataset registry resource, matching asaree.api.datasets.

Registration is a single synchronous multipart call that stores ONLY the raw
uploaded file, verbatim -- it no longer splits inline. A split is a separate,
later, optional call (``quick_split``/``register_manual_split`` below)
against that same raw file -- see ``RegisteredDataset``'s own comment in the
backend model for why splitting isn't part of registration.
"""

from __future__ import annotations

import builtins
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
        description: str | None = None,
        dictionary_json: str | None = None,
    ) -> RegisteredDataset:
        form: dict[str, Any] = {"name": name}
        if target_column is not None:
            form["target_column"] = target_column
        if description is not None:
            form["description"] = description
        if dictionary_json is not None:
            form["dictionary_json"] = dictionary_json
        with open(file_path, "rb") as f:
            data = self._client._post("/datasets", data=form, files={"file": (file_path, f)})
        return RegisteredDataset(**data)

    def quick_split(
        self,
        dataset_id: ResourceId,
        *,
        target_column: str | None = None,
        group_column: str | None = None,
        test_size: float = 0.2,
        seed: int = 0,
    ) -> RegisteredDataset:
        """ASAREE's own built-in split (group-aware when group_column is
        given and present, else stratified on target_column) -- covers the
        common case. Safe to call again (e.g. a different seed): overwrites
        whichever split currently exists rather than accumulating one per
        call."""
        form: dict[str, Any] = {"test_size": str(test_size), "seed": str(seed)}
        if target_column is not None:
            form["target_column"] = target_column
        if group_column is not None:
            form["group_column"] = group_column
        data = self._client._post(f"/datasets/{dataset_id}/split/quick", data=form)
        return RegisteredDataset(**data)

    def register_manual_split(
        self, dataset_id: ResourceId, train_file_path: str, test_file_path: str
    ) -> RegisteredDataset:
        """Register an already-split train/test pair computed however you
        needed (k-fold, time-based, a custom cohort rule, ...) -- ASAREE only
        validates that both parse as tabular data, the same "bring your own
        code" precedent the Script node already established for scoring."""
        with open(train_file_path, "rb") as train_f, open(test_file_path, "rb") as test_f:
            data = self._client._post(
                f"/datasets/{dataset_id}/split/manual",
                files={"train_file": (train_file_path, train_f), "test_file": (test_file_path, test_f)},
            )
        return RegisteredDataset(**data)

    def get_by_name(self, name: str) -> RegisteredDataset:
        data = self._client._get(f"/datasets/by-name/{name}")
        return RegisteredDataset(**data)

    def get(self, dataset_id: ResourceId) -> RegisteredDataset:
        data = self._client._get(f"/datasets/{dataset_id}")
        return RegisteredDataset(**data)

    def list(self) -> builtins.list[RegisteredDataset]:
        data = self._client._get("/datasets")
        return [RegisteredDataset(**d) for d in data]

    def delete(self, dataset_id: ResourceId) -> None:
        self._client._delete(f"/datasets/{dataset_id}")
