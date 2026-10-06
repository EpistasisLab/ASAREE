"""Publishing and reading immutable protocol canvas revisions."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.dataset import RegisteredDataset
from asaree.models.experiment import ResearchExperiment
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.services.dataset_row_csv import DatasetRowCsvError, read_row_source
from asaree.services.dataset_row_inputs import DatasetRowInputError, resolve_dataset_row_plan
from asaree.services.datasets import get_dataset_by_name
from asaree.services.design_revisions import get_current_revision
from asaree.services.experiment_versions import experiment_settings, publication_matches_experiment
from asaree.services.protocol_graph_schema import functional_protocol_graph_hash


async def get_revision(db: AsyncSession, revision_id: uuid.UUID) -> ProtocolRevision | None:
    return await db.get(ProtocolRevision, revision_id)


async def get_published_revision(db: AsyncSession, protocol: Protocol) -> ProtocolRevision | None:
    if protocol.published_revision_id is None:
        return None
    return await get_revision(db, protocol.published_revision_id)


async def publish_protocol(
    db: AsyncSession, protocol: Protocol, *, owner_id: uuid.UUID | None = None,
    name: str | None = None, note: str | None = None,
) -> ProtocolRevision:
    """Freeze canvas, experiment settings, and generated design as one version."""
    protocol = (
        await db.execute(
            select(Protocol)
            .where(Protocol.id == protocol.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    experiment = (
        await db.scalar(select(ResearchExperiment).where(ResearchExperiment.id == protocol.experiment_id)
                        .with_for_update().execution_options(populate_existing=True))
        if protocol.experiment_id is not None else None
    )
    design = await get_current_revision(db, experiment.id) if experiment is not None else None
    plan = resolve_dataset_row_plan(protocol.graph)
    if plan is not None:
        if owner_id is None:
            raise DatasetRowInputError("source_unavailable", "registered row source is unavailable")
        driver_id = uuid.UUID(plan["driver_dataset_id"])
        registration = (
            await db.execute(
                select(RegisteredDataset).where(
                    RegisteredDataset.id == driver_id,
                    RegisteredDataset.owner_id == owner_id,
                )
            )
        ).scalar_one_or_none()
        if registration is None:
            raise DatasetRowInputError("source_unavailable", "registered row source is unavailable")
        nodes = {str(node.get("id")): node for node in protocol.graph.get("nodes", []) if isinstance(node, dict)}
        for binding in plan["bindings"]:
            driver_node = nodes[binding["dataset_node_id"]]
            driver_config = (driver_node.get("data") or {}).get("config") or {}
            if driver_config.get("dataset_name") != registration.name:
                raise DatasetRowInputError("invalid_dataset_config", "dataset_id and dataset_name do not agree")
        # Resolve whole-context aliases as well as row drivers. Name-only legacy
        # nodes are owner-scoped and still identify the registered row source.
        for node in nodes.values():
            if node.get("type") != "dataset":
                continue
            config = (node.get("data") or {}).get("config") or {}
            if config.get("enabled", True) is False:
                continue
            node_id = str(node.get("id"))
            connected = any(
                edge.get("source") == node_id
                and edge.get("targetHandle") in {"dataset", "resource", "tool"}
                and (edge.get("data") or {}).get("dataset_input", {}).get("mode", "whole_dataset") == "whole_dataset"
                for edge in protocol.graph.get("edges", [])
                if isinstance(edge, dict) and isinstance(edge.get("data") or {}, dict)
            )
            if not connected:
                continue
            configured_id = config.get("dataset_id")
            name = config.get("dataset_name")
            resolved_by_id = None
            if configured_id is not None:
                try:
                    candidate = (
                        await db.execute(
                            select(RegisteredDataset).where(
                                RegisteredDataset.id == uuid.UUID(configured_id),
                                RegisteredDataset.owner_id == owner_id,
                            )
                        )
                    ).scalar_one_or_none()
                except (ValueError, TypeError, AttributeError):
                    candidate = None
                if candidate is not None:
                    resolved_by_id = candidate
            resolved_by_name = (
                await get_dataset_by_name(db, name, owner_id=owner_id)
                if isinstance(name, str) and name
                else None
            )
            if configured_id is not None and resolved_by_id is None:
                # In a row publication, a configured ID is an identity claim.
                # Do not silently fall back to a different name match (and do
                # not reveal whether the ID belongs to another user).
                raise DatasetRowInputError("source_unavailable", "registered dataset source is unavailable")
            if resolved_by_id is not None and name is not None and name != resolved_by_id.name:
                raise DatasetRowInputError("invalid_dataset_config", "dataset_id and dataset_name do not agree")
            if (
                resolved_by_id is not None
                and resolved_by_name is not None
                and resolved_by_id.id != resolved_by_name.id
            ):
                raise DatasetRowInputError("invalid_dataset_config", "dataset_id and dataset_name do not agree")
            resolved = resolved_by_id or resolved_by_name
            if resolved is not None and resolved.id == registration.id:
                raise DatasetRowInputError(
                    "mixed_driver_modes", "one dataset cannot use both per_row and whole_dataset inputs"
                )
        source = read_row_source(
            dataset_id=str(registration.id), raw_path=registration.raw_path, raw_sha256=registration.raw_sha256
        )
        for binding in plan["bindings"]:
            missing = [column for column in binding["columns"] if column not in source.columns]
            if missing:
                raise DatasetRowCsvError("invalid_columns", "selected columns are not present in the registered CSV")
    published = await get_published_revision(db, protocol)
    unchanged = (
        is_draft_published(protocol, published) and await publication_matches_experiment(db, protocol, published)
    )
    if published is not None and unchanged:
        return published
    if experiment is not None and experiment.locked_at is not None:
        raise ValueError("Experiment is locked. Unlock it before publishing a changed experiment.")
    if experiment is not None:
        from asaree.services.design_generation import get_design_impact
        incomplete_skills = any(
            factor.get("level_type") == "skill_selection" and len(factor.get("levels") or []) < 2
            for factor in (experiment.design_spec or {}).get("factors") or []
        )
        if incomplete_skills:
            # This publication can be tested with an available choice, but it
            # owns no generated design and cannot create production cell runs.
            design = None
        else:
            impact = await get_design_impact(db, experiment_id=experiment.id, design_spec=experiment.design_spec)
            if impact.regeneration_required:
                raise ValueError("Generate cells for the draft design before publishing the experiment.")

    highest = (
        await db.execute(
            select(func.max(ProtocolRevision.revision)).where(ProtocolRevision.protocol_id == protocol.id)
        )
    ).scalar_one()
    revision = ProtocolRevision(
        protocol_id=protocol.id,
        revision=(highest or 0) + 1,
        name=name,
        note=note,
        graph=copy.deepcopy(protocol.graph),
        experiment_snapshot=experiment_settings(experiment) if experiment is not None else None,
        design_revision_id=design.id if design is not None else None,
        published_at=datetime.now(UTC),
    )
    db.add(revision)
    await db.flush()
    protocol.published_revision_id = revision.id
    await db.flush()
    # A published canvas creates a new definition of "current". Preserve any
    # old latest-attempt score on that attempt itself, then clear the mutable
    # replicate projection so Results/CSV/top-bar chips cannot keep treating
    # an older canvas execution as current.
    stale_pairs = (
        await db.execute(
            select(ProtocolRun, FactorialReplicateResult)
            .join(FactorialReplicateResult, FactorialReplicateResult.run_id == ProtocolRun.id)
            .join(FactorialCell, FactorialReplicateResult.cell_id == FactorialCell.id)
            .where(ProtocolRun.protocol_id == protocol.id)
        )
    ).all()
    for run, replicate in stale_pairs:
        stale = (
            (run.protocol_revision_id is not None and run.protocol_revision_id != revision.id)
            or (run.protocol_revision_id is None and run.created_at < revision.published_at)
        )
        if not stale:
            continue
        snapshot = dict(run.attempt_result or {})
        if "metric_values" not in snapshot and isinstance(replicate.metric_values, dict):
            snapshot["metric_values"] = dict(replicate.metric_values)
        evaluation = (replicate.artifacts or {}).get("metric_evaluation")
        if "metric_evaluation" not in snapshot and isinstance(evaluation, dict):
            snapshot["metric_evaluation"] = dict(evaluation)
        run.attempt_result = snapshot or None
        replicate.metric_values = None
        replicate.artifacts = None
    await db.flush()
    # TimestampMixin's server-side update can expire fields such as
    # ``updated_at`` on the existing Protocol instance. Refresh while still
    # inside AsyncSession so the API response never tries an implicit lazy
    # load (which would raise MissingGreenlet).
    await db.refresh(protocol)
    return revision


def is_draft_published(protocol: Protocol, published: ProtocolRevision | None) -> bool:
    return published is not None and functional_protocol_graph_hash(protocol.graph) == functional_protocol_graph_hash(
        published.graph
    )


__all__ = ["get_published_revision", "get_revision", "is_draft_published", "publish_protocol"]
