"""Agent Skills — a thin layer over motoro.services.skill_service.

The format (frontmatter parsing, the name/description rules, per-owner
uniqueness, soft delete) is core's, the same way MCP registration is; see
Motoro's `docs/DESIGN.md` §"Skills: a file, not a directory". ASAREE's job is
to resolve ``owner_id`` from ``CurrentUser`` and call through.

A skill is uploaded as a **single `.md` file** — that is the Agent Skills
format's own degenerate case (a directory whose only required member is
`SKILL.md`, with no bundled level-3 resources), not a simplification of it.
Bundled *scripts* are the part core genuinely can't run: an agent's only
side-channel is an MCP tool call, so a skill that needs to execute code should
be registered as an MCP server instead.

Reading is scoped to the caller's own skills plus any global system skill
(``is_system=True``, ``owner_id=None``); mutating one is owner-only, matching
`api/mcp_servers.py`.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from motoro.schemas.skill import SkillCreate, SkillListResponse, SkillResponse, SkillUpdate
from motoro.services import skill_service
from motoro.services.skill_service import SkillFormatError

from asaree.deps import CurrentUser

router = APIRouter(prefix="/skills", tags=["skills"])


def _readable(skill: Any, user: Any) -> bool:
    """Readable if the caller owns it, or it's a global system skill."""
    return bool(skill.owner_id == user.id or skill.is_system)


def _decode(raw: bytes) -> str:
    """Decode an uploaded `.md` file as UTF-8.

    422 rather than a 500: a file that isn't text at all is the caller's
    mistake, and the same status the format errors below use.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Skill file must be UTF-8 text") from exc


@router.post("", response_model=SkillResponse, status_code=201)
async def create_skill_endpoint(body: SkillCreate, user: CurrentUser) -> SkillResponse:
    """Create a skill from separated fields (the form path, not the upload path)."""
    try:
        skill = await skill_service.create_skill(
            name=body.name,
            description=body.description,
            body=body.body,
            owner_id=user.id,
        )
    except SkillFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SkillResponse.model_validate(skill)


@router.post("/upload", response_model=SkillResponse, status_code=201)
async def upload_skill_endpoint(
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
) -> SkillResponse:
    """Register a skill from an uploaded ``SKILL.md``.

    ``name``/``description`` are optional overrides for a file whose
    frontmatter is missing or wrong — supplied ones win over the parsed ones,
    and are validated by the same rules either way.
    """
    text = _decode(await file.read())
    try:
        if name is None and description is None:
            skill = await skill_service.create_skill_from_markdown(
                text, owner_id=user.id, source_filename=file.filename
            )
        else:
            parsed = skill_service.parse_skill_markdown(text)
            skill = await skill_service.create_skill(
                name=name or parsed.name,
                description=description or parsed.description,
                body=parsed.body,
                owner_id=user.id,
                source_filename=file.filename,
            )
    except SkillFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SkillResponse.model_validate(skill)


@router.get("", response_model=SkillListResponse)
async def list_skills_endpoint(user: CurrentUser, limit: int = 100) -> SkillListResponse:
    skills = await skill_service.list_skills(owner_id=user.id, limit=limit)
    items = [SkillResponse.model_validate(s) for s in skills]
    return SkillListResponse(items=items, total=len(items))


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill_endpoint(skill_id: uuid.UUID, user: CurrentUser) -> SkillResponse:
    skill = await skill_service.get_skill(skill_id)
    if skill is None or not _readable(skill, user):
        raise HTTPException(status_code=404, detail="No such skill")
    return SkillResponse.model_validate(skill)


@router.get("/{skill_id}/markdown")
async def get_skill_markdown_endpoint(skill_id: uuid.UUID, user: CurrentUser) -> dict[str, str]:
    """The skill rendered back out as a ``SKILL.md`` document.

    So the file a user uploaded is also the thing they can read, edit, and
    re-upload — the stored row is a parse of that document, not a replacement
    for it.
    """
    skill = await skill_service.get_skill(skill_id)
    if skill is None or not _readable(skill, user):
        raise HTTPException(status_code=404, detail="No such skill")
    return {"markdown": skill_service.render_skill_md(skill)}


@router.patch("/{skill_id}", response_model=SkillResponse)
async def update_skill_endpoint(skill_id: uuid.UUID, body: SkillUpdate, user: CurrentUser) -> SkillResponse:
    existing = await skill_service.get_skill(skill_id)
    if existing is None or existing.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such skill")
    try:
        skill = await skill_service.update_skill(
            skill_id, name=body.name, description=body.description, body=body.body
        )
    except SkillFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assert skill is not None  # existence already checked above
    return SkillResponse.model_validate(skill)


@router.put("/{skill_id}/markdown", response_model=SkillResponse)
async def replace_skill_markdown_endpoint(
    skill_id: uuid.UUID,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
) -> SkillResponse:
    """Replace a skill's contents from a re-uploaded ``SKILL.md``."""
    existing = await skill_service.get_skill(skill_id)
    if existing is None or existing.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such skill")
    try:
        skill = await skill_service.update_skill_from_markdown(skill_id, _decode(await file.read()))
    except SkillFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assert skill is not None
    return SkillResponse.model_validate(skill)


@router.delete("/{skill_id}", status_code=204)
async def delete_skill_endpoint(skill_id: uuid.UUID, user: CurrentUser) -> None:
    existing = await skill_service.get_skill(skill_id)
    if existing is None or existing.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such skill")
    await skill_service.delete_skill(skill_id)
