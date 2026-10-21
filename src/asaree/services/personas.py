"""Owner-scoped persona templates. Execution never resolves these live records."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.persona import Persona

MAX_PROMPT_BYTES = 1_000_000


class PersonaRevisionConflictError(ValueError):
    """A concurrent library edit must not be overwritten silently."""


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def persona_name(name: str | None, source_filename: str | None, prompt: str) -> str:
    if name and name.strip():
        return name.strip()[:255]
    if source_filename:
        return PurePosixPath(source_filename).stem.replace("_", " ").replace("-", " ").strip()[:255] or "Persona"
    suggestion = next((line.strip().lstrip("# ") for line in prompt.splitlines() if line.strip()), "")
    return (suggestion or "Persona")[:80]


def decode_persona_upload(filename: str | None, raw: bytes) -> tuple[str, str]:
    filename = PurePosixPath((filename or "persona.md").replace("\\", "/")).name
    if not filename.lower().endswith(".md"):
        raise ValueError("Choose a Markdown (.md) file.")
    if len(raw) > MAX_PROMPT_BYTES:
        raise ValueError("Persona files must be no larger than 1 MB.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Persona files must contain UTF-8 text.") from exc
    return filename[:255], text


async def list_personas(db: AsyncSession, *, owner_id: uuid.UUID) -> Sequence[Persona]:
    statement = select(Persona).where(Persona.owner_id == owner_id).order_by(Persona.name, Persona.id)
    return (await db.scalars(statement)).all()


async def get_persona(db: AsyncSession, persona_id: uuid.UUID, *, owner_id: uuid.UUID) -> Persona | None:
    return await db.scalar(select(Persona).where(Persona.id == persona_id, Persona.owner_id == owner_id))


async def create_persona(
    db: AsyncSession, *, owner_id: uuid.UUID, prompt: str, name: str | None = None,
    description: str | None = None, source_filename: str | None = None, traits: dict[str, Any] | None = None,
) -> Persona:
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError("Persona text must be no larger than 1 MB.")
    persona = Persona(
        owner_id=owner_id, name=persona_name(name, source_filename, prompt), prompt=prompt,
        description=description, source_filename=source_filename, traits=traits or {}, revision=1,
    )
    db.add(persona)
    await db.flush()
    await db.refresh(persona)
    return persona


async def update_persona(
    db: AsyncSession, persona_id: uuid.UUID, *, owner_id: uuid.UUID, expected_revision: int, fields: dict[str, Any],
) -> Persona | None:
    persona = await db.scalar(
        select(Persona).where(Persona.id == persona_id, Persona.owner_id == owner_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if persona is None:
        return None
    if persona.revision != expected_revision:
        raise PersonaRevisionConflictError("This persona changed. Reload it before saving again.")
    prompt = fields.get("prompt", persona.prompt)
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError("Persona text must be no larger than 1 MB.")
    for key in ("prompt", "description", "traits"):
        if key in fields:
            setattr(persona, key, fields[key])
    if "name" in fields:
        persona.name = persona_name(fields["name"], persona.source_filename, prompt)
    persona.revision += 1
    await db.flush()
    await db.refresh(persona)
    return persona


async def delete_persona(db: AsyncSession, persona_id: uuid.UUID, *, owner_id: uuid.UUID) -> bool:
    persona = await get_persona(db, persona_id, owner_id=owner_id)
    if persona is None:
        return False
    await db.delete(persona)
    await db.flush()
    return True
