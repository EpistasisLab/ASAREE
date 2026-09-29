from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class Skill(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    body: str
    frontmatter: dict[str, Any]
    is_system: bool
    source_filename: str | None
    files: list[str]
    created_at: datetime
    updated_at: datetime


class SkillPage(BaseModel):
    items: list[Skill]
    total: int


class DiscoveredSkill(BaseModel):
    subdirectory: str
    name: str
    description: str
    file_count: int


class SkillUrlPreview(BaseModel):
    source: str
    ref: str
    skills: list[DiscoveredSkill]
