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
from asaree.services.skill_references import IMMUTABLE_MESSAGE


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


@pytest.mark.parametrize("operation", ["fields", "markdown", "folder"])
async def test_registered_skill_modification_is_rejected(monkeypatch, operation):
    from unittest.mock import AsyncMock

    from motoro.schemas.skill import SkillUpdate

    owner_id, skill_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(skills.skill_service, "get_skill", AsyncMock(
        return_value=SimpleNamespace(id=skill_id, owner_id=owner_id)))
    monkeypatch.setattr(skills, "resource_active", AsyncMock(return_value=True))
    writers = [AsyncMock() for _ in range(3)]
    for name, writer in zip(("update_skill", "update_skill_from_markdown", "update_skill_from_bundle"),
                            writers, strict=True):
        monkeypatch.setattr(skills.skill_service, name, writer)
    user = SimpleNamespace(id=owner_id)
    file = UploadFile(BytesIO(b"replacement"), filename="skill/SKILL.md")
    with pytest.raises(HTTPException) as error:
        if operation == "fields":
            await skills.update_skill_endpoint(skill_id, SkillUpdate(body="Changed"), user)
        elif operation == "markdown":
            await skills.replace_skill_markdown_endpoint(skill_id, user, file)
        else:
            await skills.replace_skill_folder_endpoint(skill_id, user, [file])
    assert error.value.status_code == 409
    assert error.value.detail == IMMUTABLE_MESSAGE
    for writer in writers:
        writer.assert_not_awaited()


async def test_update_does_not_disclose_foreign_skill(monkeypatch):
    from unittest.mock import AsyncMock

    from motoro.schemas.skill import SkillUpdate

    monkeypatch.setattr(skills.skill_service, "get_skill", AsyncMock(
        return_value=SimpleNamespace(owner_id=uuid.uuid4())))
    with pytest.raises(HTTPException) as error:
        await skills.update_skill_endpoint(uuid.uuid4(), SkillUpdate(body="Changed"), SimpleNamespace(id=uuid.uuid4()))
    assert error.value.status_code == 404


async def test_delete_reports_retained_usages_as_conflict(monkeypatch):
    from unittest.mock import AsyncMock

    from asaree.services.skill_references import SkillInUseError

    owner_id, skill_id = uuid.uuid4(), uuid.uuid4()
    publication_id = uuid.uuid4()
    monkeypatch.setattr(skills.skill_service, "get_skill", AsyncMock(
        return_value=SimpleNamespace(id=skill_id, owner_id=owner_id)))
    monkeypatch.setattr(skills, "resource_active", AsyncMock(return_value=True))
    monkeypatch.setattr(skills, "delete_registered_skill", AsyncMock(side_effect=SkillInUseError([
        {"kind": "published version", "name": "Experiment A", "id": publication_id},
    ])))
    with pytest.raises(HTTPException) as error:
        await skills.delete_skill_endpoint(skill_id, SimpleNamespace(id=owner_id), AsyncMock())
    assert error.value.status_code == 409
    assert "Published experiment version: Experiment A" in error.value.detail
    assert str(publication_id) not in error.value.detail


def test_skill_usage_message_hides_internal_canvas_agent_names():
    from asaree.services.skill_references import SkillInUseError

    protocol_id, node_id = uuid.uuid4(), uuid.uuid4()
    internal_name = f"protocol-{protocol_id}-{node_id}"
    message = str(SkillInUseError([
        {"kind": "agent run", "name": internal_name, "id": uuid.uuid4()},
    ]))
    assert "Saved agent run from an experiment canvas" in message
    assert "can’t be deleted while it’s in use" in message
    assert internal_name not in message


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
