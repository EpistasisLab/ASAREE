"""Agent Skill library resource."""

from __future__ import annotations

import builtins
import uuid
from pathlib import Path
from typing import Any

from asaree_client._multipart import multipart_directory
from asaree_client.models import Skill, SkillPage, SkillUrlPreview

ResourceId = uuid.UUID | str


class Skills:
    def __init__(self, client: Any) -> None:
        self._client = client

    def create(self, *, name: str, description: str, body: str) -> Skill:
        data = self._client._post("/skills", json={"name": name, "description": description, "body": body})
        return Skill(**data)

    def upload(self, file_path: str, *, name: str | None = None, description: str | None = None) -> Skill:
        form: dict[str, str] = {}
        if name is not None:
            form["name"] = name
        if description is not None:
            form["description"] = description
        with open(file_path, "rb") as file:
            data = self._client._post("/skills/upload", data=form, files={"file": (Path(file_path).name, file)})
        return Skill(**data)

    def upload_folder(self, directory: str) -> Skill:
        return self._folder_request("POST", "/skills/upload-folder", directory)

    def replace_folder(self, skill_id: ResourceId, directory: str) -> Skill:
        return self._folder_request("PUT", f"/skills/{skill_id}/folder", directory)

    def _folder_request(self, method: str, path: str, directory: str) -> Skill:
        with multipart_directory(directory) as files:
            data = self._client._request(method, path, files=files)
        return Skill(**data)

    def preview_url(self, url: str) -> SkillUrlPreview:
        data = self._client._post("/skills/from-url/preview", json={"url": url})
        return SkillUrlPreview(**data)

    def create_from_url(self, url: str, subdirectory: str = "") -> Skill:
        data = self._client._post("/skills/from-url", json={"url": url, "subdirectory": subdirectory})
        return Skill(**data)

    def list(self, *, limit: int = 100) -> builtins.list[Skill]:
        return self.list_page(limit=limit).items

    def list_page(self, *, limit: int = 100) -> SkillPage:
        data = self._client._get("/skills", params={"limit": limit})
        return SkillPage(**data)

    def get(self, skill_id: ResourceId) -> Skill:
        return Skill(**self._client._get(f"/skills/{skill_id}"))

    def get_markdown(self, skill_id: ResourceId) -> str:
        data = self._client._get(f"/skills/{skill_id}/markdown")
        return str(data["markdown"])

    def update(
        self,
        skill_id: ResourceId,
        *,
        name: str | None = None,
        description: str | None = None,
        body: str | None = None,
    ) -> Skill:
        payload = {k: v for k, v in {"name": name, "description": description, "body": body}.items() if v is not None}
        return Skill(**self._client._patch(f"/skills/{skill_id}", json=payload))

    def replace_markdown(self, skill_id: ResourceId, file_path: str) -> Skill:
        with open(file_path, "rb") as file:
            data = self._client._put(f"/skills/{skill_id}/markdown", files={"file": (Path(file_path).name, file)})
        return Skill(**data)

    def delete(self, skill_id: ResourceId) -> None:
        self._client._delete(f"/skills/{skill_id}")
