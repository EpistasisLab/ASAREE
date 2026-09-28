from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class DirectoryEntry(BaseModel):
    name: str
    path: str
    is_bundle: bool


class DirectoryListing(BaseModel):
    path: str
    absolute_path: str
    parent: str | None
    entries: list[DirectoryEntry]


class OKFBundle(BaseModel):
    id: uuid.UUID
    name: str
    path: str | None
    uploaded: bool
    status: str
    error_message: str | None
    tool_names: list[str]
    created_at: datetime


class OKFDocument(BaseModel):
    id: uuid.UUID
    name: str
    title: str | None
    description: str | None
    concept_type: str | None
    tags: list[str]
    path: str | None
    status: str
    error_message: str | None
    tool_names: list[str]
    created_at: datetime
