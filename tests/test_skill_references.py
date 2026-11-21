"""Immutable ASAREE registrations and retention with real PostgreSQL/Motoro."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from motoro.services import skill_service
from sqlalchemy import delete, func, select, text
from test_result_reads import results_db as _results_db

from asaree.models.database import dispose_engine, get_session
from asaree.models.experiment import ResearchExperiment
from asaree.models.protocol import Protocol
from asaree.models.skill_reference import SkillReference
from asaree.models.user import User
from asaree.services.design_revisions import get_or_create_current
from asaree.services.experiments import create_experiment, update_experiment
from asaree.services.protocol_revisions import publish_protocol
from asaree.services.protocol_runs import create_protocol_run
from asaree.services.protocols import create_protocol, update_protocol
from asaree.services.skill_references import (
    SkillInUseError,
    SkillUnavailableError,
    audit_references,
    delete_registered_skill,
    skill_uses,
)

pytestmark = pytest.mark.skipif(not os.environ.get("ASAREE_RESULTS_TEST_DATABASE_URL"),
                                reason="Disposable ASAREE and Motoro databases are required")
results_db = _results_db


def graph(skill_id):
    return {"nodes": [{"id": "skill", "type": "skill", "position": {"x": 0, "y": 0},
                       "data": {"config": {"skill_id": str(skill_id)}}}], "edges": []}


@pytest_asyncio.fixture
async def owner():
    async with get_session() as db:
        user = User(email=f"skill-policy-{uuid.uuid4().hex}@example.com",
                    hashed_password="unused", display_name="Policy")
        db.add(user)
        await db.flush()
        identifier = user.id
    yield identifier
    from motoro.models.agent import Agent
    from motoro.models.database import system_session
    from motoro.models.run import AgentRun

    async with system_session(reason="clean up disposable skill policy agents") as resources:
        await resources.execute(delete(AgentRun).where(AgentRun.owner_id == identifier))
        await resources.execute(delete(Agent).where(Agent.owner_id == identifier))
    async with get_session() as db:
        await db.execute(delete(Protocol).where(Protocol.owner_id == identifier))
        await db.execute(delete(ResearchExperiment).where(ResearchExperiment.owner_id == identifier))
        await db.execute(delete(User).where(User.id == identifier))
    await dispose_engine()
    from motoro.models.database import dispose_engine as dispose_motoro

    await dispose_motoro()


async def registered(owner):
    return await skill_service.create_skill(name=f"skill-{uuid.uuid4().hex}", description="Check calculations",
        body="Original instructions", owner_id=owner,
        files=[("reference.md", "Reference"), ("a.bin", "AP8=", "base64")])


async def test_shared_drafts_protect_registration_until_final_reference_removed(owner):
    skill = await registered(owner)
    async with get_session() as db:
        first = await create_protocol(db, name="first", owner_id=owner, graph=graph(skill.id))
        second = await create_protocol(db, name="second", owner_id=owner, graph=graph(skill.id))
        with pytest.raises(SkillInUseError, match="first") as error:
            await delete_registered_skill(db, skill.id, owner)
        assert len(error.value.uses) == 2
        await update_protocol(db, first.id, fields={"graph": {"nodes": [], "edges": []}})
        with pytest.raises(SkillInUseError):
            await delete_registered_skill(db, skill.id, owner)
        await db.delete(second)
        await db.flush()
        assert await delete_registered_skill(db, skill.id, owner)
    assert await skill_service.get_skill(skill.id) is None


async def test_publications_and_runs_retain_independently_of_draft(owner, monkeypatch):
    skill = await registered(owner)
    async def freeze(value, **_kwargs):
        return copy.deepcopy(value)
    monkeypatch.setattr("asaree.services.protocol_revisions.freeze_sampling", freeze)
    async with get_session() as db:
        protocol = await create_protocol(db, name="published", owner_id=owner, graph=graph(skill.id))
        publication = await publish_protocol(db, protocol, owner_id=owner)
        run = await create_protocol_run(db, protocol_id=protocol.id, owner_id=owner,
                                        protocol_revision_id=publication.id)
        await update_protocol(db, protocol.id, fields={"graph": {"nodes": [], "edges": []}})
        assert {use["kind"] for use in await skill_uses(db, skill.id)} == {"published version", "run"}
        await db.delete(publication)
        await db.flush()
        assert [use["kind"] for use in await skill_uses(db, skill.id)] == ["run"]
        with pytest.raises(SkillInUseError):
            await delete_registered_skill(db, skill.id, owner)
        await db.delete(run)
        await db.flush()
        assert await delete_registered_skill(db, skill.id, owner)


async def test_factor_alternatives_and_design_history_are_retained(owner):
    first, second = await registered(owner), await registered(owner)
    spec = {"factors": [{"name": "Choice", "level_type": "skill_selection",
                          "levels": [[str(first.id)], [str(second.id)]]}]}
    async with get_session() as db:
        experiment = await create_experiment(db, name="choices", owner_id=owner, design_spec=spec)
        design = await get_or_create_current(db, experiment.id)
        assert {use["kind"] for use in await skill_uses(db, second.id)} == {
            "experiment factors", "design revision"}
        await update_experiment(db, experiment.id, fields={"design_spec": {}})
        assert [use["kind"] for use in await skill_uses(db, second.id)] == ["design revision"]
        with pytest.raises(SkillInUseError):
            await delete_registered_skill(db, second.id, owner)
        await db.delete(design)
        await db.flush()
        assert await delete_registered_skill(db, second.id, owner)


async def test_legacy_scalar_factors_follow_design_changes(owner):
    first, second = await registered(owner), await registered(owner)
    selected = graph(first.id)
    selected["nodes"][0]["data"]["factor_bindings"] = {"config.skill_id": "Choice"}
    async with get_session() as db:
        experiment = await create_experiment(db, name="legacy", owner_id=owner,
            design_spec={"factors": [{"name": "Choice", "levels": [str(first.id), str(second.id)]}]})
        protocol = await create_protocol(db, name="legacy", owner_id=owner,
                                         experiment_id=experiment.id, graph=selected)
        with pytest.raises(SkillInUseError):
            await delete_registered_skill(db, second.id, owner)
        await update_experiment(db, experiment.id, fields={"design_spec": {}})
        assert not await skill_uses(db, second.id)
        assert [use["id"] for use in await skill_uses(db, first.id)] == [protocol.id]


async def test_legacy_locked_factor_choices_retain_their_skills(owner):
    skill = await registered(owner)
    async with get_session() as db:
        experiment = await create_experiment(db, name="legacy locked", owner_id=owner)
        await update_experiment(db, experiment.id, fields={"locked_design_spec": {"factors": [
            {"name": "Locked", "level_type": "skill_toggle", "levels": [[str(skill.id)], []]}]}})
        with pytest.raises(SkillInUseError):
            await delete_registered_skill(db, skill.id, owner)
        await update_experiment(db, experiment.id, fields={"locked_design_spec": None})
        assert await delete_registered_skill(db, skill.id, owner)


async def test_legacy_run_retains_its_actual_factor_choice(owner, monkeypatch):
    first, second = await registered(owner), await registered(owner)
    selected = graph(first.id)
    selected["nodes"][0]["data"]["factor_bindings"] = {"config.skill_id": "Choice"}
    async def freeze(value, **_kwargs):
        return copy.deepcopy(value)
    monkeypatch.setattr("asaree.services.protocol_revisions.freeze_sampling", freeze)
    async with get_session() as db:
        protocol = await create_protocol(db, name="legacy-run", owner_id=owner, graph=selected)
        publication = await publish_protocol(db, protocol, owner_id=owner)
        await create_protocol_run(db, protocol_id=protocol.id, owner_id=owner,
                                  protocol_revision_id=publication.id, factor_values={"Choice": str(second.id)})
        assert [use["kind"] for use in await skill_uses(db, second.id)] == ["run"]
        with pytest.raises(SkillInUseError):
            await delete_registered_skill(db, second.id, owner)


async def test_ordinary_autosave_skips_skill_validation_and_reference_writes(owner, monkeypatch):
    skill = await registered(owner)
    async with get_session() as db:
        protocol = await create_protocol(db, name="autosave", owner_id=owner, graph=graph(skill.id))
        before = (await db.execute(select(SkillReference.id, text("xmin::text")).where(
            SkillReference.protocol_id == protocol.id))).all()
        validator = AsyncMock(side_effect=AssertionError("Autosave must not resolve skill bundles"))
        monkeypatch.setattr("asaree.services.protocols.validate_references", validator)
        moved = copy.deepcopy(protocol.graph)
        moved["nodes"][0]["position"] = {"x": 100, "y": 200}
        await update_protocol(db, protocol.id, fields={"graph": moved})
        assert (await db.execute(select(SkillReference.id, text("xmin::text")).where(
            SkillReference.protocol_id == protocol.id))).all() == before
        validator.assert_not_awaited()


async def test_missing_or_foreign_selection_is_rejected_and_rolls_back(owner):
    foreign = await registered(uuid.uuid4())
    for identifier in (foreign.id, uuid.uuid4()):
        with pytest.raises(SkillUnavailableError):
            async with get_session() as db:
                await create_protocol(db, name="invalid", owner_id=owner, graph=graph(identifier))
    async with get_session() as db:
        assert await db.scalar(select(func.count()).select_from(Protocol).where(Protocol.owner_id == owner)) == 0


async def test_attachment_committing_first_prevents_concurrent_deletion(owner):
    skill = await registered(owner)
    async with get_session() as attachment:
        await create_protocol(attachment, name="concurrent", owner_id=owner, graph=graph(skill.id))
        async def remove():
            async with get_session() as db:
                await delete_registered_skill(db, skill.id, owner)
        task = asyncio.create_task(remove())
        assert not (await asyncio.wait({task}, timeout=.05))[0]
        await attachment.commit()
        with pytest.raises(SkillInUseError):
            await task


async def test_deletion_committing_first_prevents_concurrent_attachment(owner):
    skill = await registered(owner)
    async with get_session() as deletion:
        assert await delete_registered_skill(deletion, skill.id, owner)
        async def attach():
            async with get_session() as db:
                await create_protocol(db, name="too-late", owner_id=owner, graph=graph(skill.id))
        task = asyncio.create_task(attach())
        assert not (await asyncio.wait({task}, timeout=.05))[0]
        await deletion.commit()
        with pytest.raises(SkillUnavailableError):
            await task
    async with get_session() as db:
        assert not await skill_uses(db, skill.id)


async def test_audit_reports_direct_motoro_changes_without_rewriting_history(owner):
    skill = await registered(owner)
    async with get_session() as db:
        protocol = await create_protocol(db, name="audit", owner_id=owner, graph=graph(skill.id))
        original = copy.deepcopy(protocol.graph)
    # Deliberately outside ASAREE's policy, as permitted by the product contract.
    await skill_service.delete_skill(skill.id)
    async with get_session() as db:
        issues = [issue for issue in await audit_references(db) if issue["skill_id"] == skill.id]
        assert [issue["reason"] for issue in issues] == ["deleted"]
        assert (await db.get(Protocol, protocol.id)).graph == original


async def test_audit_command_reports_missing_legacy_content_without_changing_graph(owner, tmp_path):
    missing = uuid.uuid4()
    original = graph(missing)
    async with get_session() as db:
        # Model write simulates an existing definition predating validation.
        protocol = Protocol(name="Legacy unavailable skill", owner_id=owner, graph=copy.deepcopy(original))
        db.add(protocol)
        await db.flush()
        identifier = protocol.id
    result = subprocess.run([sys.executable, "-m", "asaree.services.skill_references"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert any(issue["skill_id"] == str(missing) and issue["reason"] == "missing"
               for issue in report["unavailable_references"])
    async with get_session() as db:
        assert (await db.get(Protocol, identifier)).graph == original


async def test_agent_and_agent_run_references_are_also_protected(owner):
    from motoro.models.database import system_session
    from motoro.models.run import AgentRun
    from motoro.runner import create_agent, create_run, update_agent

    from asaree.services.skill_references import protect_agent_run

    skill = await registered(owner)
    agent = await create_agent(name=f"policy-{uuid.uuid4().hex}", goal="Check", owner_id=owner,
                               skill_config={"skill_ids": [str(skill.id)]})
    async with get_session() as db:
        with pytest.raises(SkillInUseError, match="agent"):
            await delete_registered_skill(db, skill.id, owner)
    async with protect_agent_run(agent, owner):
        run = await create_run(agent_id=agent.id, owner_id=owner, user_input="Check", config_snapshot={
            "skill_config": {"skill_ids": [str(skill.id)]}})
    await update_agent(agent.id, skill_config={"skill_ids": []})
    async with get_session() as db:
        assert [use["kind"] for use in await skill_uses(db, skill.id)] == ["agent run"]
        with pytest.raises(SkillInUseError, match="agent run"):
            await delete_registered_skill(db, skill.id, owner)
    async with system_session(reason="remove disposable test run history") as resources:
        await resources.execute(delete(AgentRun).where(AgentRun.id == run.id))
    async with get_session() as db:
        assert await delete_registered_skill(db, skill.id, owner)


@pytest.mark.parametrize("available", [True, False])
async def test_standalone_run_endpoint_checks_skill_availability_before_enqueue(owner, monkeypatch, available):
    from types import SimpleNamespace

    from fastapi import HTTPException
    from motoro.runner import create_agent

    from asaree.api import runs

    skill = await registered(owner)
    agent = await create_agent(name=f"standalone-{uuid.uuid4().hex}", goal="Check", owner_id=owner,
                               skill_config={"skill_ids": [str(skill.id)]})
    queued = AsyncMock()
    monkeypatch.setattr(runs, "enqueue_run", queued)
    if not available:
        await skill_service.delete_skill(skill.id)
        with pytest.raises(HTTPException) as error:
            await runs.create_run_endpoint(runs.CreateRunRequest(agent_id=agent.id, user_input="Check"),
                                           SimpleNamespace(id=owner))
        assert error.value.status_code == 422
        queued.assert_not_awaited()
    else:
        response = await runs.create_run_endpoint(runs.CreateRunRequest(agent_id=agent.id, user_input="Check"),
                                                  SimpleNamespace(id=owner))
        queued.assert_awaited_once_with(response.id)
        async with get_session() as db:
            assert {use["kind"] for use in await skill_uses(db, skill.id)} == {"agent", "agent run"}


async def test_migration_backfills_legacy_references_and_preserves_content(results_db):
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from asaree.models.experiment_design_revision import ExperimentDesignRevision
    from asaree.models.protocol_revision import ProtocolRevision
    from asaree.models.protocol_run import ProtocolRun

    db, scope, _, owner = results_db
    migration = import_module("asaree.migrations.versions.e12a34b56c78_skill_references")
    connection = await db.connection()
    def migrate(sync, operation):
        with Operations.context(MigrationContext.configure(sync)):
            operation()
    await connection.run_sync(migrate, migration.downgrade)
    missing = uuid.uuid4()
    original = graph(missing)
    protocol = await db.get(Protocol, scope.protocol_id)
    protocol.graph = copy.deepcopy(original)
    publication = await db.get(ProtocolRevision, scope.protocol_revision_id)
    publication.graph = copy.deepcopy(original)
    experiment = await db.get(ResearchExperiment, scope.experiment_id)
    experiment.design_spec = {"factors": [{"name": "Choice", "level_type": "skill_toggle",
                                           "levels": [[str(missing)], []]}]}
    design = await db.get(ExperimentDesignRevision, scope.design_revision_id)
    design.design_spec = copy.deepcopy(experiment.design_spec)
    db.add(ProtocolRun(protocol_id=protocol.id, protocol_revision_id=publication.id, owner_id=owner.id))
    await db.flush()
    await connection.run_sync(migrate, migration.upgrade)
    uses = await skill_uses(db, missing)
    assert {use["kind"] for use in uses} == {
        "draft canvas", "published version", "run", "experiment factors", "design revision"}
    assert protocol.graph == publication.graph == original
    identifiers = set((await db.scalars(select(SkillReference.id))).all())
    await connection.run_sync(migrate, migration.upgrade)
    assert set((await db.scalars(select(SkillReference.id))).all()) == identifiers
    issues = [issue for issue in await audit_references(db) if issue["skill_id"] == missing]
    assert len(issues) == 5 and all(issue["reason"] == "missing" for issue in issues)
    await db.delete(protocol)
    await db.delete(experiment)
    await db.flush()
    assert await db.scalar(select(func.count()).select_from(SkillReference)) == 0
