"""Promotes a completed replicate's Score-stage result into
``FactorialReplicateResult.metric_values``.

Not automatic: ``services.protocol_execution.run_protocol`` deliberately
leaves ``metric_values`` unpopulated after a cell's run completes (see its own
comment on that post-write block) -- there's no generic notion yet of "which
output_contract field is the metric" for an arbitrary graph, so a user
promotes ``artifacts`` into ``metric_values`` manually via
``PUT /experiments/{id}/replicates/{replicate_label}``, the same manual step the real
notebook's own ``score_payload`` is today.

This module is the deterministic, testable version of that manual step for
one specific, common pipeline shape (a Score agent wired to a single
``run_model_script`` tool call) -- both ``spinal-use-case.json`` and
``myocardial-use-case.json`` are this shape. It reads the Score stage's raw
tool-call result straight from Motoro's own ``run_steps`` (the actual
JSON ``run_model_script`` returned), not the agent's own free-text report --
the agent is only ever instructed to report those numbers verbatim, never to
recompute them, but parsing prose is still strictly less reliable than
reading the tool call Motoro already durably recorded.

``average_precision``/``roc_auc`` are top-level keys in that result's
``test_metrics``; ``f1``/``balanced_accuracy``/``accuracy`` are
threshold-dependent and MUST come from ``metrics_at_chosen_threshold`` (the
train-only-selected operating point) per ``ASAREE_stats_handoff_brief.md``
Section 4's own threshold-discipline rule -- ``metrics_at_0.5`` or any
test-tuned threshold would be leakage.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from motoro.runner import get_run_steps
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.services.factorial_cells import upsert_replicate
from asaree.services.protocol_runs import get_protocol_run, list_experiment_trials

_TOOL_NAME = "run_model_script"
# Present as top-level keys in test_metrics.
_TOP_LEVEL_METRICS = ("average_precision", "roc_auc")
# Threshold-dependent -- only ever read from metrics_at_chosen_threshold (see
# module docstring); never metrics_at_0.5 or any other threshold block.
_CHOSEN_THRESHOLD_METRICS = ("f1", "balanced_accuracy", "accuracy")


def extract_score_metrics(tool_result: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten one ``run_model_script`` response into the flat shape
    ``factorial_analysis._replicates_to_frame`` reads (it only ever reads
    ``metric_values``'s top-level keys, never recurses). Returns ``None``
    when the call never produced ``test_metrics`` at all -- e.g. it returned
    only an ``error`` (an uninitialized workspace, a rejected payload)."""
    test_metrics = tool_result.get("test_metrics")
    if not isinstance(test_metrics, dict):
        return None
    chosen = test_metrics.get("metrics_at_chosen_threshold")
    chosen = chosen if isinstance(chosen, dict) else {}
    metrics: dict[str, Any] = {}
    for name in _TOP_LEVEL_METRICS:
        if name in test_metrics:
            metrics[name] = test_metrics[name]
    for name in _CHOSEN_THRESHOLD_METRICS:
        if name in chosen:
            metrics[name] = chosen[name]
    return metrics or None


def find_score_tool_result(steps: Sequence[Any]) -> dict[str, Any] | None:
    """The LAST successful ``run_model_script`` call's parsed JSON result
    across an agent run's own steps -- "last" because a Score-stage agent can
    retry within its own turn (e.g. after a transient tool error), and the
    last call is the one whose output the agent's own final answer (and any
    Critic Gate reviewing it) actually reflects. ``None`` if the tool was
    never called successfully, or its result wasn't parseable JSON."""
    result: dict[str, Any] | None = None
    for step in steps:
        call = step.tool_call or {}
        if call.get("tool") != _TOOL_NAME or not call.get("success"):
            continue
        raw = call.get("result")
        if not isinstance(raw, str):
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            result = parsed
    return result


@dataclass
class PromotionResult:
    replicate_label: str
    promoted: bool
    reason: str  # explains a False `promoted` -- empty when promoted is True


async def promote_replicate_score_metrics(
    db: AsyncSession, *, experiment_id: uuid.UUID, replicate_label: str, protocol_run_id: uuid.UUID
) -> PromotionResult:
    """Promote one cell's Score-stage metrics, given the ``ProtocolRun`` that
    scored it. Doesn't assume the Score agent is any particular node id, or
    even the graph's sink -- a ``ProtocolRun.node_runs`` entry only ever
    carries a ``run_id`` for an agent node (never a critic_gate/tool/pattern/
    llm node -- see that field's own "agent nodes only" comment on the
    model), so every such entry is a candidate; whichever one's own steps
    hold a successful ``run_model_script`` call is the Score agent. This
    stays correct even against an older graph where Score was still gated by
    a Critic (Score) node (making the critic, not Score, the sink) --
    checking every agent run instead of relying on graph topology is what
    survives that."""
    run = await get_protocol_run(db, protocol_run_id)
    if run is None:
        return PromotionResult(replicate_label, False, f"no such protocol run: {protocol_run_id}")

    candidate_run_ids = [nr.get("run_id") for nr in (run.node_runs or {}).values() if nr.get("run_id")]
    if not candidate_run_ids:
        return PromotionResult(replicate_label, False, "no agent runs recorded on this protocol run")

    tool_result = None
    for run_id in candidate_run_ids:
        steps = await get_run_steps(uuid.UUID(run_id))
        tool_result = find_score_tool_result(steps)
        if tool_result is not None:
            break
    if tool_result is None:
        return PromotionResult(replicate_label, False, "no successful run_model_script call found in any agent run")

    metrics = extract_score_metrics(tool_result)
    if metrics is None:
        return PromotionResult(
            replicate_label, False, "run_model_script never returned test_metrics (see its own error)"
        )

    # Written back to the design revision the run was planned under, not
    # whatever is current now -- see ProtocolRun.design_revision_id. Null on a
    # run predating that column, which falls back to the current revision.
    await upsert_replicate(
        db,
        experiment_id=experiment_id,
        replicate_label=replicate_label,
        fields={"metric_values": metrics},
        revision_id=run.design_revision_id,
    )
    return PromotionResult(replicate_label, True, "")


async def promote_experiment_score_metrics(db: AsyncSession, *, experiment_id: uuid.UUID) -> list[PromotionResult]:
    """Promote every completed cell under *experiment_id* that has a
    ``ProtocolRun`` but no ``metric_values`` yet. Idempotent -- a cell that
    already has metric_values is left untouched (already-promoted or upserted
    directly by a notebook), not re-derived."""
    trials = await list_experiment_trials(db, experiment_id=experiment_id)
    results = []
    for trial in trials:
        if trial.metric_values:
            continue
        if trial.run_id is None:
            results.append(PromotionResult(trial.replicate_label, False, "no ProtocolRun for this replicate yet"))
            continue
        if trial.status != "completed":
            results.append(
                PromotionResult(trial.replicate_label, False, f"run status is {trial.status!r}, not completed")
            )
            continue
        results.append(
            await promote_replicate_score_metrics(
                db,
                experiment_id=experiment_id,
                replicate_label=trial.replicate_label,
                protocol_run_id=trial.run_id,
            )
        )
    return results


__all__ = [
    "PromotionResult",
    "extract_score_metrics",
    "find_score_tool_result",
    "promote_replicate_score_metrics",
    "promote_experiment_score_metrics",
]
