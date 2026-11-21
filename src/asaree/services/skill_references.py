"""ASAREE's immutable-registration policy and cross-database retention checks.

Reference triggers and deletion share advisory transaction locks in ASAREE's
database. Validate AFTER a reference write: its lock then protects the Motoro
availability check until ASAREE commits. Motoro itself remains unchanged.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from motoro.models.database import system_session
from motoro.models.skill import Skill
from motoro.services import skill_service
from sqlalchemy import func, literal, or_, select, text, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from asaree.models.experiment import ResearchExperiment
from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.package_import import ImportedIdentity, PackageImport
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.models.skill_reference import SkillReference

IMMUTABLE_MESSAGE = "Registered skills are immutable in ASAREE. Register a new skill with a different name instead."


class SkillUnavailableError(ValueError):
    """An ASAREE definition tries to retain an unavailable registered skill."""


class SkillInUseError(ValueError):
    def __init__(self, uses: list[dict[str, Any]]):
        self.uses = uses
        labels = "; ".join(f"{item['kind']}: {item['name']} ({item['id']})" for item in uses[:5])
        suffix = f"; and {len(uses) - 5} more" if len(uses) > 5 else ""
        super().__init__(f"This skill is used by {len(uses)} retained records: {labels}{suffix}. "
                         "Remove those references before deleting the skill. "
                         "Published versions and retained runs count.")


def skill_projection(graph: dict[str, Any]) -> list[Any]:
    """Ignore positions and unrelated fields on the frequent canvas save path."""
    result = []
    for node in graph.get("nodes") or []:
        data = node.get("data") or {}
        bindings = data.get("factor_bindings") or {}
        if node.get("type") == "skill" or "skill_selection" in data or "skill_selection" in bindings:
            source = (data.get("config") or {}).get("skill_id") if node.get("type") == "skill" else None
            if source:
                try:
                    uuid.UUID(str(source))
                except (ValueError, TypeError, AttributeError) as exc:
                    raise SkillUnavailableError(
                        "A Skill node has an invalid skill ID. Select a registered skill.") from exc
            result.append((node.get("id"), node.get("type"), source, data.get("skill_selection"),
                           bindings.get("config"), bindings.get("config.skill_id"), bindings.get("skill_selection")))
    return sorted(result, key=lambda item: str(item[0]))


def _uses_query() -> Subquery:
    r = SkillReference
    def fields(kind: str, identifier: Any, name: Any, owner: Any) -> tuple[Any, ...]:
        return (r.skill_id, literal(kind).label("kind"), identifier.label("id"),
                name.label("name"), owner.label("owner_id"))

    return union_all(
        select(*fields("draft canvas", Protocol.id, Protocol.name, Protocol.owner_id))
            .join(Protocol, r.protocol_id == Protocol.id),
        select(*fields("published version", ProtocolRevision.id,
                       func.concat(Protocol.name, " / version ", ProtocolRevision.revision), Protocol.owner_id))
            .join(ProtocolRevision, r.publication_id == ProtocolRevision.id)
            .join(Protocol, ProtocolRevision.protocol_id == Protocol.id),
        select(*fields("run", ProtocolRun.id, Protocol.name, ProtocolRun.owner_id))
            .join(ProtocolRun, r.run_id == ProtocolRun.id).join(Protocol, ProtocolRun.protocol_id == Protocol.id),
        select(*fields("experiment factors", ResearchExperiment.id,
                       ResearchExperiment.name, ResearchExperiment.owner_id))
            .join(ResearchExperiment, r.experiment_id == ResearchExperiment.id),
        select(*fields("design revision", ExperimentDesignRevision.id,
                       func.concat(ResearchExperiment.name, " / design ", ExperimentDesignRevision.revision),
                       ResearchExperiment.owner_id))
            .join(ExperimentDesignRevision, r.design_id == ExperimentDesignRevision.id)
            .join(ResearchExperiment, ExperimentDesignRevision.experiment_id == ResearchExperiment.id),
    ).subquery()


async def skill_uses(db: AsyncSession, skill_id: uuid.UUID) -> list[dict[str, Any]]:
    uses = _uses_query()
    product = [dict(row) for row in (await db.execute(select(uses).where(uses.c.skill_id == skill_id)
        .order_by(uses.c.kind, uses.c.id))).mappings()]
    async with system_session(reason="check registered skills retained by agents and agent runs") as resources:
        runtime = [dict(row) for row in (await resources.execute(_runtime_uses_sql(),
                    {"skill_id": str(skill_id)})).mappings()]
    return product + runtime


def _runtime_uses_sql() -> Any:
    # Deletion/audit only: runtime cache rows and standalone runs are in the
    # other database, so they cannot be foreign-key parents in the ledger.
    return text("""
      WITH configurations AS (
        SELECT 'agent' AS kind, id, name, owner_id, skill_config::jsonb AS config
          FROM agents WHERE deleted_at IS NULL
        UNION ALL
        SELECT 'agent run', r.id, a.name, coalesce(r.owner_id, a.owner_id),
          coalesce(r.config_snapshot::jsonb->'skill_config', a.skill_config::jsonb)
          FROM agent_runs r JOIN agents a ON a.id=r.agent_id
      ) SELECT DISTINCT lower(value) AS skill_id, kind, id, name, owner_id
        FROM configurations, LATERAL jsonb_array_elements_text(
          CASE WHEN jsonb_typeof(config->'skill_ids')='array' THEN config->'skill_ids' ELSE '[]'::jsonb END) value
        WHERE CAST(:skill_id AS text) IS NULL OR lower(value)=CAST(:skill_id AS text)
        ORDER BY kind, id
    """)


async def validate_references(db: AsyncSession, owner_id: uuid.UUID, **scope: uuid.UUID) -> None:
    """Called after flush only when a definition's skill references change."""
    # Import registrations are uncommitted/soft-deleted until cross-database
    # activation. Its existing readiness checks gate execution and publication.
    if db.info.get("staging_skill_import"):
        return
    if len(scope) != 1 or next(iter(scope)) not in {
        "protocol_id", "publication_id", "run_id", "experiment_id", "design_id"
    }:
        raise ValueError("Exactly one skill reference scope is required")
    field, identifier = next(iter(scope.items()))
    predicate = getattr(SkillReference, field) == identifier
    if field == "experiment_id":
        predicate = or_(predicate, SkillReference.protocol_id.in_(
            select(Protocol.id).where(Protocol.experiment_id == identifier)))
    identifiers = set((await db.scalars(select(SkillReference.skill_id).where(predicate))).all())
    await _validate_ids(db, owner_id, identifiers)


async def _validate_ids(db: AsyncSession, owner_id: uuid.UUID, identifiers: set[uuid.UUID]) -> None:
    if not identifiers:
        return
    if await db.scalar(select(ImportedIdentity.local_id).join(PackageImport).where(
        ImportedIdentity.kind == "skill", ImportedIdentity.local_id.in_(identifiers),
        PackageImport.status != "completed",
    ).limit(1)):
        raise SkillUnavailableError("A selected skill belongs to an incomplete package import.")
    async with system_session(reason="validate ASAREE registered skill references") as resources:
        # Column selection deliberately avoids eagerly loading bundled files.
        available = set((await resources.scalars(select(Skill.id).where(
            Skill.id.in_(identifiers), Skill.deleted_at.is_(None),
            (Skill.owner_id == owner_id) | Skill.is_system.is_(True),
        ))).all())
    if available != identifiers:
        raise SkillUnavailableError("A skill reference is unavailable. Register and select an available skill; "
                                    "existing missing skill content cannot be reconstructed.")


@asynccontextmanager
async def protect_agent_run(agent: Any, owner_id: uuid.UUID) -> AsyncIterator[None]:
    """Close the standalone-run creation/deletion race without changing Motoro."""
    identifiers = set(skill_service.skill_ids_from_config(getattr(agent, "skill_config", None)))
    if not identifiers:
        yield
        return
    from asaree.models.database import get_session

    async with get_session() as db:
        for identifier in sorted(identifiers):
            await db.execute(select(func.pg_advisory_xact_lock(1936419180, func.hashtext(str(identifier)))))
        await _validate_ids(db, owner_id, identifiers)
        yield


async def delete_registered_skill(db: AsyncSession, skill_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
    await db.execute(select(func.pg_advisory_xact_lock(1936419180, func.hashtext(str(skill_id)))))
    skill = await skill_service.get_skill(skill_id)
    if skill is None or skill.owner_id != owner_id:
        return False
    uses = await skill_uses(db, skill_id)
    if uses:
        # Legacy graphs could contain foreign IDs before attachment validation
        # existed. Protect those records without exposing another user's data.
        visible = [use if use["owner_id"] == owner_id else {
            **use, "name": "another user's retained record", "id": "private",
        } for use in uses]
        raise SkillInUseError(visible)
    # Keep the ASAREE lock until its transaction ends. A later attachment waits,
    # then validates the now-deleted Motoro row and rolls back its reference.
    return await skill_service.delete_skill(skill_id)


async def audit_references(db: AsyncSession) -> list[dict[str, Any]]:
    """Report pre-existing missing/deleted/foreign sources without changing history."""
    uses = _uses_query()
    issues = []
    async def inspect_batch(batch: Any) -> None:
        normalized = []
        for row in batch:
            try:
                normalized.append({**dict(row), "skill_id": uuid.UUID(str(row["skill_id"]))})
            except (ValueError, TypeError):
                issues.append({**dict(row), "reason": "invalid skill ID"})
        identifiers = {row["skill_id"] for row in normalized}
        async with system_session(reason="audit ASAREE skill availability") as resources:
            sources = {row.id: row for row in (await resources.execute(select(
                Skill.id, Skill.owner_id, Skill.is_system, Skill.deleted_at,
            ).where(Skill.id.in_(identifiers))))}
        for row in normalized:
            source = sources.get(row["skill_id"])
            reason = ("missing" if source is None else "deleted" if source.deleted_at else
                      "not accessible" if source.owner_id != row["owner_id"] and not source.is_system else None)
            if reason:
                issues.append({**row, "reason": reason})

    stream = await db.stream(select(uses).order_by(uses.c.skill_id))
    try:
        async for batch in stream.mappings().partitions(500):
            await inspect_batch(batch)
    finally:
        await stream.close()
    async with system_session(reason="audit skill references on agents and agent runs") as resources:
        stream = await resources.stream(_runtime_uses_sql(), {"skill_id": None})
        try:
            async for batch in stream.mappings().partitions(500):
                await inspect_batch(batch)
        finally:
            await stream.close()
    return issues


async def _audit() -> int:
    from motoro.models.database import dispose_engine as dispose_motoro

    from asaree.models.database import dispose_engine, get_session

    try:
        async with get_session() as db:
            issues = await audit_references(db)
            print(json.dumps({"unavailable_references": issues, "count": len(issues)}, default=str, indent=2))
            return bool(issues)
    finally:
        await dispose_engine()
        await dispose_motoro()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_audit()))
