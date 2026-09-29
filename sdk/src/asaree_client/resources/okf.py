"""Open Knowledge Format bundle and document resources."""

from __future__ import annotations

import builtins
import uuid
from pathlib import Path
from typing import Any

from asaree_client._multipart import multipart_directory
from asaree_client.models import DirectoryListing, OKFBundle, OKFDocument

ResourceId = uuid.UUID | str


class OKF:
    def __init__(self, client: Any) -> None:
        self._client = client

    def browse(self, path: str = "") -> DirectoryListing:
        return DirectoryListing(**self._client._get("/okf/browse", params={"path": path}))

    def list_bundles(self) -> builtins.list[OKFBundle]:
        return [OKFBundle(**item) for item in self._client._get("/okf/bundles")]

    def register_bundle(self, path: str) -> OKFBundle:
        return OKFBundle(**self._client._post("/okf/bundles", json={"path": path}))

    def upload_bundle(self, directory: str) -> OKFBundle:
        with multipart_directory(directory) as files:
            data = self._client._post("/okf/bundles/upload", files=files)
        return OKFBundle(**data)

    def refresh_bundle(self, bundle_id: ResourceId) -> OKFBundle:
        return OKFBundle(**self._client._post(f"/okf/bundles/{bundle_id}/refresh"))

    def delete_bundle(self, bundle_id: ResourceId) -> None:
        self._client._delete(f"/okf/bundles/{bundle_id}")

    def list_concepts(self, bundle_id: ResourceId) -> dict[str, Any]:
        return self._client._get(f"/okf/bundles/{bundle_id}/concepts")  # type: ignore[no-any-return]

    def list_documents(self) -> builtins.list[OKFDocument]:
        return [OKFDocument(**item) for item in self._client._get("/okf/documents")]

    def upload_document(self, file_path: str) -> OKFDocument:
        with open(file_path, "rb") as file:
            data = self._client._post("/okf/documents", files={"file": (Path(file_path).name, file)})
        return OKFDocument(**data)

    def refresh_document(self, document_id: ResourceId) -> OKFDocument:
        return OKFDocument(**self._client._post(f"/okf/documents/{document_id}/refresh"))

    def delete_document(self, document_id: ResourceId) -> None:
        self._client._delete(f"/okf/documents/{document_id}")

    def get_document_markdown(self, document_id: ResourceId) -> str:
        data = self._client._get(f"/okf/documents/{document_id}/markdown")
        return str(data["markdown"])
