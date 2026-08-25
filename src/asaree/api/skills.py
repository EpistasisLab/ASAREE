"""Agent Skills — a thin layer over motoro.services.skill_service.

The format (frontmatter parsing, the name/description rules, per-owner
uniqueness, soft delete, the bundled-file rules) is core's, the same way MCP
registration is; see Motoro's `docs/DESIGN.md` §"Skills: a directory, stored as
rows". ASAREE's job is to resolve ``owner_id`` from ``CurrentUser`` and call
through.

Three upload shapes, because an Agent Skill is a *directory* whose only required
member is `SKILL.md`:

- ``POST /skills/upload`` takes that one file, for a skill with no bundled
  level-3 resources — the format's own degenerate case, not a simplification.
- ``POST /skills/upload-folder`` takes the whole directory the way
  `POST /okf/bundles/upload` does, since a browser never reveals a real path:
  each part's ``filename`` carries its ``webkitRelativePath``, and the leading
  folder segment is stripped here rather than trusted from a separate field.
- ``POST /skills/from-url/preview`` then ``POST /skills/from-url`` pull the same
  directory out of a public GitHub repo (`services/skill_sources.py`). Two calls
  rather than one because a skills repo is usually a *collection*: the preview
  says what is in there, the user ticks the ones they want, and each tick is a
  register. Where the bytes came from is an acquisition concern only — all three
  shapes end at the same ``create_skill_from_bundle`` and produce the same rows,
  so nothing downstream (the Skill node, the canvas, the engine) can tell them
  apart.

Bundled *scripts* are the part core genuinely can't run: an agent's only
side-channel is an MCP tool call, so a skill that needs to execute code should
be registered as an MCP server instead. Core rejects them at the boundary, and
that 422 is passed straight through.

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
from pydantic import BaseModel, Field

from asaree.deps import CurrentUser
from asaree.services import skill_sources
from asaree.services.skill_sources import SkillSourceError

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


async def _folder_payload(files: list[UploadFile]) -> list[tuple[str, str]]:
    """``(path relative to the skill folder, text)`` for one directory upload.

    The leading ``webkitRelativePath`` segment is the folder the user picked —
    ``code-simplification/SKILL.md`` — and core's ``parse_skill_bundle`` wants
    paths relative to the skill directory itself. Stripping it is this layer's
    job because only this layer knows the upload came from a folder picker.

    A part with no folder segment is a rejection rather than a guess: it means
    the user dragged loose files, and a skill assembled from an unknown
    directory layout is not the directory they have on disk.
    """
    payload: list[tuple[str, str]] = []
    for upload in files:
        name = upload.filename or ""
        parts = [p for p in name.replace("\\", "/").split("/") if p]
        if len(parts) < 2:
            raise HTTPException(
                status_code=422,
                detail=f"{name or 'A file'} didn't come from a folder — pick the skill's own folder.",
            )
        payload.append(("/".join(parts[1:]), _decode(await upload.read())))
    return payload


@router.post("/upload-folder", response_model=SkillResponse, status_code=201)
async def upload_skill_folder_endpoint(
    user: CurrentUser,
    files: Annotated[list[UploadFile], File()],
) -> SkillResponse:
    """Register a skill from an uploaded skill *directory*.

    ``source_filename`` records the folder the user picked, not a file: it is
    only ever shown back to them as "from code-simplification/", and the folder
    is what they chose.
    """
    payload = await _folder_payload(files)
    folder = (files[0].filename or "").replace("\\", "/").split("/")[0] if files else None
    try:
        skill = await skill_service.create_skill_from_bundle(payload, owner_id=user.id, source_filename=folder)
    except SkillFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SkillResponse.model_validate(skill)


@router.put("/{skill_id}/folder", response_model=SkillResponse)
async def replace_skill_folder_endpoint(
    skill_id: uuid.UUID,
    user: CurrentUser,
    files: Annotated[list[UploadFile], File()],
) -> SkillResponse:
    """Replace a skill's whole directory from a re-uploaded folder.

    A replacement, not a merge — a re-upload that drops ``FORMS.md`` drops it,
    because the alternative leaves the skill holding a document its own
    ``SKILL.md`` no longer mentions.
    """
    existing = await skill_service.get_skill(skill_id)
    if existing is None or existing.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such skill")
    payload = await _folder_payload(files)
    try:
        skill = await skill_service.update_skill_from_bundle(skill_id, payload)
    except SkillFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assert skill is not None
    return SkillResponse.model_validate(skill)


class SkillUrlRequest(BaseModel):
    """A pasted GitHub URL, and optionally which skill inside it."""

    url: str = Field(description="A github.com repo URL, or the URL of one skill's folder inside it.")
    # Repo-relative, and only ever a value the preview call handed back. Empty
    # means "the one the URL already points at", which is the single-skill case.
    subdirectory: str = ""


class DiscoveredSkillResponse(BaseModel):
    """One skill found in a repo — enough to tick a checkbox by, not the files."""

    subdirectory: str
    name: str
    description: str
    file_count: int


class SkillUrlPreviewResponse(BaseModel):
    """What a repo turned out to hold, plus the ref that actually answered.

    ``source`` is shown back because a bare repo URL resolves its own default
    branch here, and the user should see which one they are about to register
    from rather than find out later.
    """

    source: str
    ref: str
    skills: list[DiscoveredSkillResponse]


@router.post("/from-url/preview", response_model=SkillUrlPreviewResponse)
async def preview_skills_from_url_endpoint(body: SkillUrlRequest, user: CurrentUser) -> SkillUrlPreviewResponse:
    """List the skills in a public GitHub repo without registering any.

    Read-only and stores nothing, so it needs no ownership check beyond being
    signed in. Not a GET with a query string: the URL is a URL, and nesting one
    in another is a percent-encoding trap for no gain.
    """
    try:
        source, discovered = await skill_sources.discover_skills(body.url)
    except (SkillSourceError, SkillFormatError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SkillUrlPreviewResponse(
        source=f"{source.owner}/{source.repo}",
        ref=source.ref or "",
        skills=[DiscoveredSkillResponse(**vars(item)) for item in discovered],
    )


@router.post("/from-url", response_model=SkillResponse, status_code=201)
async def create_skill_from_url_endpoint(body: SkillUrlRequest, user: CurrentUser) -> SkillResponse:
    """Register one skill out of a public GitHub repo.

    The archive is re-fetched rather than carried over from the preview call:
    it is capped small, and a server-side cache keyed by URL is state with a TTL
    to get wrong for no gain at this size. Registering N ticked skills is N
    calls, which also means one malformed skill in a repo fails on its own
    instead of taking the others with it.

    ``source_filename`` records ``owner/repo/path@ref`` — the same slot the
    folder upload puts the picked folder in, and the only provenance kept: the
    skill is its rows from here on, not a live link to a repo that can change
    underneath it.
    """
    try:
        source, bundle = await skill_sources.fetch_skill_bundle(body.url, body.subdirectory)
        # Truncated because ``source_filename`` is a String(255) and a deeply
        # nested path in a long-named repo can pass core's own path check and
        # still overflow the column. Provenance is display text, so losing the
        # tail of it beats a 500 on an otherwise valid skill.
        provenance = f"{source.label}@{source.ref}"[:255]
        skill = await skill_service.create_skill_from_bundle(bundle, owner_id=user.id, source_filename=provenance)
    except (SkillSourceError, SkillFormatError) as exc:
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
        skill = await skill_service.update_skill(skill_id, name=body.name, description=body.description, body=body.body)
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
