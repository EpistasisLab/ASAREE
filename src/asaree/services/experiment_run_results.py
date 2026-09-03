"""User-facing rollups for an experiment's current cell results.

This is deliberately separate from :mod:`factorial_analysis`.  The latter is
an optional statistical analysis with design-specific preconditions; this
module answers the questions every experiment has from its first run: what
finished, what did it cost, and what did each cell and replicate produce.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from math import isfinite
from typing import Any

from motoro.runner import get_run
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.services.factorial_cells import list_replicates
from asaree.services.metrics import normalize_metrics
from asaree.services.protocol_runs import list_experiment_trials


def _number(value: Any) -> float | None:
    """A finite numeric value, excluding booleans (which are ints in Python)."""
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _usage(agent_run: Any | None) -> dict[str, int | float | None]:
    """Normalize Motoro's provider-shaped token and cost values.

    Providers don't all report the same token key names, so absence remains
    ``None`` rather than being presented as a misleading zero-cost result.
    """
    if agent_run is None:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost_usd": None}
    raw = getattr(agent_run, "token_usage", None) or {}
    if not isinstance(raw, dict):
        raw = {}

    def first(*keys: str) -> float | None:
        for key in keys:
            value = _number(raw.get(key))
            if value is not None:
                return value
        return None

    input_tokens = first("input_tokens", "prompt_tokens", "input_token_count")
    output_tokens = first("output_tokens", "completion_tokens", "output_token_count")
    total_tokens = first("total_tokens", "total_token_count")
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return {
        "input_tokens": int(input_tokens) if input_tokens is not None else None,
        "output_tokens": int(output_tokens) if output_tokens is not None else None,
        "total_tokens": int(total_tokens) if total_tokens is not None else None,
        "cost_usd": _number(getattr(agent_run, "cost_estimate", None)),
    }


def _duration_seconds(start: datetime, end: datetime) -> float:
    return max(0, (end - start).total_seconds())


def _numeric_metrics(values: dict[str, Any] | None) -> dict[str, float]:
    return {key: number for key, value in (values or {}).items() if (number := _number(value)) is not None}


def _sum(values: list[float]) -> float | None:
    return sum(values) if values else None


def _sum_reported(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row[key] is not None]
    return sum(values) if values else None


def _primary_metric(design_spec: dict[str, Any] | None) -> tuple[str | None, str]:
    """The declared comparison metric and direction, with safe defaults."""
    metrics = design_spec.get("metrics") if isinstance(design_spec, dict) else None
    if not isinstance(metrics, list):
        return None, "maximize"
    for metric in metrics:
        if isinstance(metric, dict) and metric.get("primary") and isinstance(metric.get("name"), str):
            direction = metric.get("direction")
            # Catalog runtime metrics are stored under their telemetry key
            # (cost_usd, duration_seconds, ...), while their display name is
            # intentionally human-readable ("Cost", "Duration").
            key = metric.get("catalogKey") if metric.get("kind") == "runtime" else metric["name"]
            resolved_key = key if isinstance(key, str) else metric["name"]
            resolved_direction = direction if direction in {"maximize", "minimize"} else "maximize"
            return resolved_key, resolved_direction
    return None, "maximize"


def _declared_runtime_metrics(design_spec: dict[str, Any] | None, execution: dict[str, Any]) -> dict[str, float]:
    """Project selected runtime telemetry into the Results metric namespace.

    Telemetry remains execution-owned and is never written into a replicate's
    persisted ``metric_values`` JSON.  This view merely makes a declared
    runtime metric selectable and comparable beside promoted score metrics.
    """
    metrics = design_spec.get("metrics") if isinstance(design_spec, dict) else None
    values: dict[str, float] = {}
    for metric in normalize_metrics(metrics):
        if metric["kind"] != "runtime" or not isinstance(metric.get("catalogKey"), str):
            continue
        value = _number(execution.get(metric["catalogKey"]))
        if value is not None:
            values[metric["catalogKey"]] = value
    return values


def _has_execution_evidence(node_run: dict[str, Any]) -> bool:
    """Whether a node has a real execution event worth showing in a timeline.

    Connector/config nodes are persisted as ``completed`` so the executor has
    a total graph record, but they don't independently run, spend, emit, or
    fail. An agent run ID, output, error, or non-terminal execution state is
    the evidence that makes a row useful to an end user.
    """
    return bool(
        node_run.get("run_id")
        or node_run.get("output_text")
        or node_run.get("error")
        or node_run.get("status") in {"running", "failed", "cancelled"}
    )


_NODE_TYPE_FALLBACK_LABELS = {
    "agent": "Agent",
    "critic_gate": "Critic Gate",
    "llm": "Model",
    "mcp_tool": "MCP Tool",
    "mcp_client_tool": "MCP Client Tool",
    "memory": "Memory",
    "dataset": "Dataset",
    "script": "Script",
    "skill": "Skill",
    "okf_bundle": "OKF Bundle",
    "okf_document": "OKF Document",
    "reason_act_pattern": "Reason + Act",
    "single_agent_baseline_pattern": "Single-Agent Baseline",
}


def _node_labels(graph: dict[str, Any] | None) -> dict[str, str]:
    """Map durable canvas IDs to their visible canvas title.

    ``data.label`` is the editable title users see on a node. Some historical
    graph snapshots predate labels, so mirror the node component's own
    placeholder there rather than leaking a generated node ID into Results.
    """
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        return {}
    labels: dict[str, str] = {}
    for node in graph["nodes"]:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            continue
        data = node.get("data")
        label = data.get("label") if isinstance(data, dict) else None
        if not isinstance(label, str) or not label.strip():
            label = node.get("label")
        if not isinstance(label, str) or not label.strip():
            label = _NODE_TYPE_FALLBACK_LABELS.get(node.get("type"), "Canvas node")
        if isinstance(label, str) and label.strip():
            labels[node["id"]] = label.strip()
    return labels


async def _node_labels_by_protocol_run(
    db: AsyncSession, protocol_runs: dict[uuid.UUID, ProtocolRun]
) -> dict[uuid.UUID, dict[str, str]]:
    """Use each run's pinned canvas, not today's draft, for node names."""
    revision_ids = {run.protocol_revision_id for run in protocol_runs.values() if run.protocol_revision_id}
    revisions_by_id: dict[uuid.UUID, ProtocolRevision] = {}
    if revision_ids:
        result = await db.execute(select(ProtocolRevision).where(ProtocolRevision.id.in_(revision_ids)))
        revisions_by_id = {revision.id: revision for revision in result.scalars().all()}

    # Keep the current graph as a compatibility fallback for a legacy run
    # whose revision pointer is missing or whose old revision was removed.
    protocol_ids = {run.protocol_id for run in protocol_runs.values()}
    protocols_by_id: dict[uuid.UUID, Protocol] = {}
    if protocol_ids:
        result = await db.execute(select(Protocol).where(Protocol.id.in_(protocol_ids)))
        protocols_by_id = {protocol.id: protocol for protocol in result.scalars().all()}

    labels_by_run: dict[uuid.UUID, dict[str, str]] = {}
    for run_id, run in protocol_runs.items():
        revision = revisions_by_id.get(run.protocol_revision_id) if run.protocol_revision_id else None
        protocol = protocols_by_id.get(run.protocol_id)
        graph = revision.graph if revision is not None else (protocol.graph if protocol is not None else None)
        labels_by_run[run_id] = _node_labels(graph)
    return labels_by_run


async def _agent_runs_by_id(run_ids: set[uuid.UUID]) -> dict[uuid.UUID, Any]:
    """Fetch the agent runs attached to protocol nodes, best-effort.

    A missing/deleted Motoro run must not make an experiment's results page
    unusable; it simply means cost and token reporting is unavailable for that
    particular node.
    """
    if not run_ids:
        return {}
    resolved = await asyncio.gather(*(get_run(run_id) for run_id in run_ids), return_exceptions=True)
    return {
        run_id: agent_run
        for run_id, agent_run in zip(run_ids, resolved, strict=True)
        if not isinstance(agent_run, BaseException) and agent_run is not None
    }


async def summarize_experiment_run_results(
    db: AsyncSession, *, experiment_id: uuid.UUID, design_spec: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a current-design results scorecard plus cell/replicate detail.

    The summary intentionally excludes obsolete results from metric and usage
    aggregates.  They remain visible in the returned replicate list so people
    can inspect history without allowing an older canvas to influence today's
    comparison.
    """
    replicates = await list_replicates(db, experiment_id=experiment_id)
    trials = await list_experiment_trials(db, experiment_id=experiment_id)
    trials_by_label = {trial.replicate_label: trial for trial in trials}
    protocol_run_ids = {trial.run_id for trial in trials if trial.run_id is not None}
    # The replicate row points to its latest ProtocolRun, but every earlier
    # execution remains durable in protocol_runs. Keep those old immutable
    # versions available so a re-run never hides the evidence it replaced.
    history_rows = (
        await db.execute(
            select(ProtocolRun, Protocol.published_revision_id, ProtocolRevision.published_at)
            .join(Protocol, ProtocolRun.protocol_id == Protocol.id)
            .outerjoin(ProtocolRevision, Protocol.published_revision_id == ProtocolRevision.id)
            .where(Protocol.experiment_id == experiment_id, ProtocolRun.replicate_label.is_not(None))
        )
    ).all()
    history_by_label: defaultdict[str, list[tuple[ProtocolRun, bool]]] = defaultdict(list)
    for historical_run, current_revision_id, current_published_at in history_rows:
        obsolete = current_revision_id is not None and (
            (
                historical_run.protocol_revision_id is not None
                and historical_run.protocol_revision_id != current_revision_id
            )
            or (
                historical_run.protocol_revision_id is None
                and current_published_at is not None
                and historical_run.created_at < current_published_at
            )
        )
        history_by_label[historical_run.replicate_label or ""].append((historical_run, obsolete))
        protocol_run_ids.add(historical_run.id)
    protocol_runs_by_id: dict[uuid.UUID, ProtocolRun] = {}
    if protocol_run_ids:
        result = await db.execute(select(ProtocolRun).where(ProtocolRun.id.in_(protocol_run_ids)))
        protocol_runs_by_id = {run.id: run for run in result.scalars().all()}
    node_labels_by_protocol_run = await _node_labels_by_protocol_run(db, protocol_runs_by_id)

    agent_run_ids: set[uuid.UUID] = set()
    for protocol_run in protocol_runs_by_id.values():
        for node_run in (protocol_run.node_runs or {}).values():
            agent_run_id = node_run.get("run_id") if isinstance(node_run, dict) else None
            try:
                if agent_run_id:
                    agent_run_ids.add(uuid.UUID(str(agent_run_id)))
            except (TypeError, ValueError):
                continue
    agent_runs = await _agent_runs_by_id(agent_run_ids)

    def execution_detail(protocol_run: ProtocolRun) -> dict[str, Any]:
        """The timeline and reported usage for one immutable ProtocolRun."""
        node_results: list[dict[str, Any]] = []
        usage_values: defaultdict[str, list[float]] = defaultdict(list)
        node_labels = node_labels_by_protocol_run.get(protocol_run.id, {})
        for node_id, node_run in (protocol_run.node_runs or {}).items():
            node_run = node_run if isinstance(node_run, dict) else {}
            if not _has_execution_evidence(node_run):
                continue
            agent_run_id = node_run.get("run_id")
            agent_run: Any | None = None
            try:
                if agent_run_id:
                    agent_run = agent_runs.get(uuid.UUID(str(agent_run_id)))
            except (TypeError, ValueError):
                pass
            usage = _usage(agent_run)
            for key, value in usage.items():
                if value is not None:
                    usage_values[key].append(float(value))
            node_results.append(
                {
                    "node_id": node_id,
                    "node_label": node_labels.get(node_id, node_id),
                    "status": node_run.get("status", "unknown"),
                    "output_text": node_run.get("output_text"),
                    "error": node_run.get("error"),
                    "agent_run_id": str(agent_run_id) if agent_run_id else None,
                    **usage,
                }
            )
        return {
            "duration_seconds": _duration_seconds(protocol_run.created_at, protocol_run.updated_at),
            "node_runs": node_results,
            "input_tokens": int(sum(usage_values["input_tokens"])) if usage_values["input_tokens"] else None,
            "output_tokens": int(sum(usage_values["output_tokens"])) if usage_values["output_tokens"] else None,
            "total_tokens": int(sum(usage_values["total_tokens"])) if usage_values["total_tokens"] else None,
            "cost_usd": sum(usage_values["cost_usd"]) if usage_values["cost_usd"] else None,
            "agent_run_count": sum(node["agent_run_id"] is not None for node in node_results),
            "reported_usage_count": len(
                {node["agent_run_id"] for node in node_results if node["total_tokens"] is not None}
            ),
            "reported_cost_count": len({node["agent_run_id"] for node in node_results if node["cost_usd"] is not None}),
        }

    result_rows: list[dict[str, Any]] = []
    metric_keys: set[str] = set()
    for replicate in replicates:
        trial = trials_by_label.get(replicate.replicate_label)
        protocol_run = protocol_runs_by_id.get(trial.run_id) if trial and trial.run_id else None
        execution = (
            execution_detail(protocol_run)
            if protocol_run is not None
            else {
                "duration_seconds": None,
                "node_runs": [],
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "cost_usd": None,
                "agent_run_count": 0,
                "reported_usage_count": 0,
                "reported_cost_count": 0,
            }
        )
        # The replicate row only names the latest run. It may itself be stale
        # after a later canvas publication, so obsolete history must include
        # that row as well as any preceding executions. Do not make callers
        # reconstruct this by combining a current row and "previous" history.
        obsolete_runs = [
            {
                "run_id": str(historical_run.id),
                "status": "queued" if historical_run.status == "pending" else historical_run.status,
                "obsolete": True,
                "error": historical_run.error,
                "protocol_revision_id": str(historical_run.protocol_revision_id)
                if historical_run.protocol_revision_id
                else None,
                "updated_at": historical_run.updated_at,
                **execution_detail(historical_run),
            }
            for historical_run, obsolete in history_by_label.get(replicate.replicate_label, [])
            if obsolete
        ]

        # Preserve manually promoted/evaluator output as-is, then layer only
        # the runtime telemetry the experiment explicitly declared.  This is
        # a response projection, not a fake score and not a DB write.
        metric_values = dict(replicate.metric_values or {})
        metric_values.update(_declared_runtime_metrics(design_spec, execution))
        metrics = _numeric_metrics(metric_values)
        metric_keys.update(metrics)
        result_rows.append(
            {
                "replicate_label": replicate.replicate_label,
                "replicate_number": replicate.replicate_number,
                "cell_label": replicate.cell_label,
                "factor_values": replicate.factor_values or {},
                "metric_values": metric_values,
                "status": (
                    "queued"
                    if trial is not None and trial.status == "pending"
                    else (trial.status if trial else "not_started")
                ),
                "obsolete": bool(trial and trial.obsolete),
                "error": trial.error if trial is not None else None,
                "run_id": str(trial.run_id) if trial and trial.run_id else None,
                "protocol_revision_id": (
                    str(protocol_run.protocol_revision_id)
                    if protocol_run and protocol_run.protocol_revision_id
                    else None
                ),
                "updated_at": trial.updated_at if trial is not None else replicate.updated_at,
                "metric_evaluation": (
                    (replicate.artifacts or {}).get("metric_evaluation")
                    if isinstance((replicate.artifacts or {}).get("metric_evaluation"), dict)
                    else None
                ),
                "obsolete_runs": sorted(obsolete_runs, key=lambda run: run["updated_at"], reverse=True),
                **execution,
            }
        )

    current_rows = [row for row in result_rows if not row["obsolete"]]
    cells: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in result_rows:
        cells[row["cell_label"]].append(row)

    cell_summaries: list[dict[str, Any]] = []
    for cell_label, rows in cells.items():
        current = [row for row in rows if not row["obsolete"]]
        source = current or rows
        metrics = {}
        for key in metric_keys:
            values = [_number(row["metric_values"].get(key)) for row in current]
            metrics[key] = _sum([value for value in values if value is not None])
        cell_summaries.append(
            {
                "cell_label": cell_label,
                "factor_values": rows[0]["factor_values"],
                "replicate_count": len(rows),
                "completed_count": sum(row["status"] == "completed" for row in rows),
                "current_completed_count": sum(row["status"] == "completed" for row in current),
                "obsolete_count": sum(len(row["obsolete_runs"]) for row in rows),
                # Kept under the established response key for client
                # compatibility. Values are deliberately cell totals, not
                # means: a cell aggregates all of its current replicates.
                "metric_means": {key: value for key, value in metrics.items() if value is not None},
                "cost_usd": _sum_reported(current, "cost_usd"),
                "total_tokens": _sum_reported(current, "total_tokens"),
                "duration_seconds": _sum_reported(source, "duration_seconds"),
            }
        )

    overview = {
        "total_replicates": len(result_rows),
        "completed_replicates": sum(row["status"] == "completed" for row in result_rows),
        "running_replicates": sum(row["status"] == "running" for row in result_rows),
        "queued_replicates": sum(row["status"] in {"pending", "queued"} for row in result_rows),
        "failed_replicates": sum(row["status"] in {"failed", "cancelled"} for row in result_rows),
        "not_started_replicates": sum(row["status"] == "not_started" for row in result_rows),
        "obsolete_replicates": sum(len(row["obsolete_runs"]) for row in result_rows),
        "total_cost_usd": _sum_reported(current_rows, "cost_usd"),
        "total_input_tokens": _sum_reported(current_rows, "input_tokens"),
        "total_output_tokens": _sum_reported(current_rows, "output_tokens"),
        "total_tokens": _sum_reported(current_rows, "total_tokens"),
        "total_duration_seconds": _sum_reported(current_rows, "duration_seconds"),
        "agent_run_count": sum(row["agent_run_count"] for row in current_rows),
        "reported_usage_count": sum(row["reported_usage_count"] for row in current_rows),
        "reported_cost_count": sum(row["reported_cost_count"] for row in current_rows),
    }
    primary_metric, primary_metric_direction = _primary_metric(design_spec)
    return {
        "overview": overview,
        "metric_keys": sorted(metric_keys),
        "primary_metric": primary_metric if primary_metric in metric_keys else None,
        "primary_metric_direction": primary_metric_direction,
        "cells": sorted(cell_summaries, key=lambda cell: cell["cell_label"]),
        "replicates": sorted(result_rows, key=lambda row: (row["cell_label"], row["replicate_number"])),
    }


__all__ = ["summarize_experiment_run_results"]
