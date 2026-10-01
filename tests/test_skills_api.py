"""API error translation for registered Agent Skills."""

from __future__ import annotations

import uuid
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from motoro.schemas.skill import SkillCreate
from sqlalchemy.exc import IntegrityError

from asaree.api import skills


async def test_registering_duplicate_skill_name_returns_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _duplicate(**_kwargs: object) -> None:
        raise IntegrityError(
            "INSERT INTO skills",
            {},
            Exception('duplicate key value violates unique constraint "uq_skills_owner_name_active"'),
        )

    monkeypatch.setattr(skills.skill_service, "create_skill", _duplicate)

    with pytest.raises(HTTPException) as exc_info:
        await skills.create_skill_endpoint(
            SkillCreate(name="duplicate-skill", description="A duplicate skill.", body="Instructions."),
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "A skill with this name is already registered to your account."


async def test_uploading_duplicate_skill_folder_returns_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _duplicate(*_args: object, **_kwargs: object) -> None:
        raise IntegrityError(
            "INSERT INTO skills",
            {},
            Exception('duplicate key value violates unique constraint "uq_skills_owner_name_active"'),
        )

    monkeypatch.setattr(skills.skill_service, "create_skill_from_bundle", _duplicate)
    upload = UploadFile(
        BytesIO(b"---\nname: duplicate-skill\ndescription: A duplicate skill.\n---\n"),
        filename="duplicate-skill/SKILL.md",
    )

    with pytest.raises(HTTPException) as exc_info:
        await skills.upload_skill_folder_endpoint(SimpleNamespace(id=uuid.uuid4()), [upload])

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "A skill with this name is already registered to your account."
