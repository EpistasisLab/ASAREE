"""Built-in runtime metric producer for one immutable ProtocolRun attempt."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from math import isfinite
from typing import Any

from motoro.runner import get_run_steps, list_runs
from motoro.schemas.output import parse_envelope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.config import get_settings
from asaree.models.database import get_session
from asaree.models.experiment import ResearchExperiment
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.services.measurement_engine import (
    CompletedReplicate,
    ExperimentSnapshot,
    InvalidMeasurementPlanError,
    MeasurementEngine,
    MeasurementEvaluation,
    MeasurementInput,
    MeasurementPlan,
    MeasurementProducerAdapter,
    MetricAggregation,
    ObservationStatus,
    ProducedObservation,
    ProducerBinding,
    ProducerCapability,
    ProducerResult,
    ScalarOutputCapability,
    ValidationIssue,
    ValidationReport,
    parse_measurement_plan,
    preserved_binding_severity,
)
from asaree.services.measurement_migration import normalize_experiment_measurement_plan
from asaree.services.metrics import design_metrics_from_measurement_plan
from asaree.services.protocol_graph import node_map
from asaree.services.protocol_runs import (
    TERMINAL_PROTOCOL_RUN_STATUSES,
    get_cancel_requested_at,
    record_measurement_evaluation,
)
from asaree.services.reported_metrics import (
    REPORTED_PRODUCER_IDS,
    collect_reported_metrics,
    resolve_reported_metric_graph,
)

_SUM_OR_MEAN: set[MetricAggregation] = {"sum", "mean"}
_RATE_AGGREGATIONS: set[MetricAggregation] = {"mean", "rate", "pooled"}
_SUPPORTED_PRODUCER_IDS = frozenset({"asaree.runtime", "asaree.node_runtime", *REPORTED_PRODUCER_IDS})


def _merge_evaluations(
    evaluation: MeasurementEvaluation,
    checkpointed: MeasurementEvaluation | None,
) -> MeasurementEvaluation:
    if checkpointed is None:
        return evaluation
    observations = {item.metric_id: item for item in evaluation.observations}
    observations.update({item.metric_id: item for item in checkpointed.observations})
    artifacts = {(item.producer.binding_id, item.artifact_key): item for item in evaluation.artifacts}
    artifacts.update({(item.producer.binding_id, item.artifact_key): item for item in checkpointed.artifacts})
    return replace(
        evaluation,
        observations=tuple(observations.values()),
        artifacts=tuple(artifacts.values()),
    )


async def _watch_measurement_cancellation(
    protocol_run_id: uuid.UUID,
    cancellation_event: asyncio.Event,
    *,
    interval_seconds: float = 0.1,
) -> None:
    """Translate an external Stop request into local measurement cancellation."""
    while not cancellation_event.is_set():
        async with get_session() as watcher_db:
            if await get_cancel_requested_at(watcher_db, protocol_run_id) is not None:
                cancellation_event.set()
                return
        await asyncio.sleep(interval_seconds)


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    number = float(value)
    if not isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _sum_reported(runs: Sequence[Mapping[str, Any]], key: str) -> ProducedObservation:
    values: list[int | float] = []
    for run in runs:
        source = run if key == "cost_usd" else run.get("usage")
        value = _number(source.get(key)) if isinstance(source, Mapping) else None
        if value is not None:
            values.append(value)
    if not values:
        return ProducedObservation.unavailable(f"No attributed provider run reported {key}.")
    return ProducedObservation.measured(sum(values))


def _first_number(values: Mapping[str, Any], *keys: str) -> int | float | None:
    for key in keys:
        if key in values and (value := _number(values[key])) is not None:
            return value
    return None


def _reported_usage(run: Any, steps: Sequence[Any]) -> dict[str, int | float]:
    raw = getattr(run, "token_usage", None)
    usage: dict[str, int | float] = {}
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens", "input_token_count"),
        "output_tokens": ("output_tokens", "completion_tokens", "output_token_count"),
        "total_tokens": ("total_tokens", "total_token_count"),
        "cache_read_tokens": ("cache_read_tokens", "cache_read_input_tokens", "cached_tokens"),
        "cache_creation_tokens": ("cache_creation_tokens", "cache_creation_input_tokens"),
        "thinking_tokens": ("thinking_tokens", "reasoning_tokens"),
    }
    if isinstance(raw, Mapping):
        for normalized, keys in aliases.items():
            # Motoro v0.6 normalizes an unreported provider field to zero.
            # Until it preserves presence explicitly, only a positive count
            # proves that this field was reported rather than defaulted.
            if (value := _first_number(raw, *keys)) is not None and value > 0:
                usage[normalized] = value
    if "total_tokens" not in usage and "input_tokens" in usage and "output_tokens" in usage:
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]

    # Motoro keeps provider-distinct cache counts on individual LLM-call
    # records rather than AgentRun.token_usage. Positive values prove the
    # provider supplied the distinction; schema-default zero does not.
    step_usage: dict[str, int | float] = {}
    for step in steps:
        llm_call = getattr(step, "llm_call", None)
        if not isinstance(llm_call, Mapping):
            continue
        for normalized, keys in {
            "cache_read_tokens": ("cache_read_input_tokens", "cache_read_tokens"),
            "cache_creation_tokens": ("cache_creation_input_tokens", "cache_creation_tokens"),
            "thinking_tokens": ("thinking_tokens", "reasoning_tokens"),
        }.items():
            if (value := _first_number(llm_call, *keys)) is not None and value > 0:
                step_usage[normalized] = step_usage.get(normalized, 0) + value
    usage.update(step_usage)
    return usage


#: Node types whose authored text is a prompt the run sent to a model: the
#: stage agents, the critics (their system prompts carry the review criteria),
#: and the Output Parser contracts that shape what each stage returns.
_PROMPT_NODE_TYPES = frozenset({"agent", "critic_gate", "output_parser"})
_PROMPT_CONFIG_KEYS = ("system_prompt", "goal", "prompt", "output_contract")


def authored_prompt_sha256(graph: Mapping[str, Any]) -> str | None:
    """SHA-256 over the authored prompts of the graph a replicate ran.

    The notebook's ``prompt_sha256``: tamper evidence that the stage prompts and
    review criteria are the ones the experiment claims. Computed from the
    factor-resolved published revision, so a prompt bound to a factor hashes
    per cell, and a canvas edit after publishing cannot change it. Canonical
    JSON sorted by node id, so moving a node or reordering the graph is not a
    prompt change. ``None`` when the graph has no prompt text at all."""
    entries = []
    for node in graph.get("nodes") or []:
        if not isinstance(node, Mapping) or node.get("type") not in _PROMPT_NODE_TYPES:
            continue
        config = (node.get("data") or {}).get("config") or {}
        fields = {key: config[key] for key in _PROMPT_CONFIG_KEYS if config.get(key)}
        if fields:
            entries.append({"id": str(node.get("id")), "type": node.get("type"), **fields})
    if not entries:
        return None
    entries.sort(key=lambda entry: entry["id"])
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _max_iterations_hit(run: Any) -> bool:
    """Whether a Reason+Act run was cut off by its iteration ceiling (see
    ``protocol_execution._truncation_fields`` for why this is read from
    ``pattern_overrides`` rather than the steps)."""
    state = _reason_act_state(run)
    return state is not None and bool(state.get("max_iterations_hit"))


def _reason_act_state(run: Any) -> Mapping[str, Any] | None:
    """The loop summary Reason+Act mirrors onto its run; absent on single-pass runs."""
    state = (getattr(run, "pattern_overrides", None) or {}).get("reason_act_state")
    return state if isinstance(state, Mapping) and state else None


def _reason_act_facts(run: Any) -> dict[str, int] | None:
    state = _reason_act_state(run)
    if state is None:
        return None
    return {
        "iterations": int(_number(state.get("iterations")) or 0),
        "tool_calls": int(_number(state.get("tool_calls")) or 0),
    }


async def collect_runtime_facts(protocol_run: Any) -> dict[str, Any]:
    """Snapshot every Motoro run causally attributed to one ProtocolRun."""
    # Motoro applies ``limit`` after its exact JSON metadata filter. Passing
    # None maps to SQLAlchemy's unbounded LIMIT and is necessary here: rejected
    # revisions and peer consultations are intentionally absent from the final
    # node pointers, so a capped query could silently undercount an attempt.
    attributed = await list_runs(
        owner_id=protocol_run.owner_id,
        metadata={"protocol_run_id": str(protocol_run.id)},
        limit=None,  # type: ignore[arg-type]
    )
    unique_runs = {str(run.id): run for run in attributed}
    ordered = list(unique_runs.values())
    step_results = await asyncio.gather(*(get_run_steps(run.id) for run in ordered))
    runs = []
    critic_reviews = []
    for run, steps in zip(ordered, step_results, strict=True):
        metadata = getattr(run, "run_metadata", None)
        node_id = metadata.get("node_id") if isinstance(metadata, Mapping) else None
        runs.append(
            {
                "run_id": str(run.id),
                # The canvas node that launched the run -- a critic run carries
                # its gate's id -- so a node-scoped binding can split totals.
                "node_id": str(node_id) if node_id is not None else None,
                # Same v0.6 normalization issue as token usage: 0.0 is also
                # the fallback when pricing was unavailable. Prefer an
                # unavailable observation to a false claim that the run was
                # free until Motoro persists reporting provenance.
                "cost_usd": (
                    cost if (cost := _number(getattr(run, "cost_estimate", None))) is not None and cost > 0 else None
                ),
                "usage": _reported_usage(run, steps),
                "steps": [
                    {
                        "iteration": getattr(step, "iteration", None),
                        "tool_call": getattr(step, "tool_call", None),
                        "llm_call": getattr(step, "llm_call", None),
                    }
                    for step in steps
                ],
                "max_iterations_hit": _max_iterations_hit(run),
                # Present (possibly None) on every run so an attempt collected
                # before this field existed reads as unrecorded, not zero.
                "reason_act": _reason_act_facts(run),
            }
        )
        if isinstance(metadata, Mapping) and metadata.get("runtime_role") == "critic":
            envelope = parse_envelope(getattr(run, "output", None))
            payload = envelope.payload if envelope is not None else None
            # Every verdict the gate acted on, including ``approved: null`` --
            # the revision loop treats anything but ``true`` as a rejection.
            if isinstance(payload, Mapping):
                approved = payload.get("approved")
                critic_reviews.append(
                    {
                        "approved": approved if isinstance(approved, bool) else None,
                        "rejection_scope": payload.get("rejection_scope"),
                        "run_id": str(run.id),
                    }
                )
    gates = []
    for node_run in (protocol_run.node_runs or {}).values():
        if not isinstance(node_run, Mapping) or not any(
            key in node_run for key in ("approved", "forced", "revisions_used")
        ):
            continue
        gates.append(
            {
                "approved": node_run.get("approved"),
                "forced": node_run.get("forced", False),
                "revisions_used": node_run.get("revisions_used", 0),
            }
        )
    return {
        "protocol_run_id": str(protocol_run.id),
        "started_at": protocol_run.started_at.isoformat() if protocol_run.started_at is not None else None,
        "completed_at": protocol_run.completed_at.isoformat() if protocol_run.completed_at is not None else None,
        "runs": runs,
        "critic_reviews": critic_reviews,
        "critic_gates": gates,
    }


def _tool_attempts(tool_call: Any) -> list[Mapping[str, Any]]:
    if not isinstance(tool_call, Mapping):
        return []
    calls = tool_call.get("calls")
    if isinstance(calls, list):
        return [call for item in calls for call in _tool_attempts(item)]
    return [tool_call]


def _duration(facts: Mapping[str, Any]) -> ProducedObservation:
    start_raw = facts.get("started_at")
    completion_raw = facts.get("completed_at")
    if not isinstance(start_raw, str) or not isinstance(completion_raw, str):
        return ProducedObservation.unavailable("Protocol run start or completion time was not recorded.")
    try:
        start = datetime.fromisoformat(start_raw)
        completion = datetime.fromisoformat(completion_raw)
    except ValueError:
        return ProducedObservation.failed("Protocol run start or completion time is invalid.")
    return ProducedObservation.measured(max(0.0, (completion - start).total_seconds()))


def _run_observations(runs: Sequence[Mapping[str, Any]]) -> dict[str, ProducedObservation]:
    """Observations that are plain sums over a set of attributed Motoro runs."""
    observations = {
        key: _sum_reported(runs, key)
        for key in (
            "cost_usd",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "thinking_tokens",
        )
    }
    tool_attempts: list[Mapping[str, Any]] = []
    iterations: set[tuple[str, int]] = set()
    for run in runs:
        run_id = str(run.get("run_id") or "")
        steps = run.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            tool_attempts.extend(_tool_attempts(step.get("tool_call")))
            iteration = step.get("iteration")
            if run_id and isinstance(iteration, int) and not isinstance(iteration, bool):
                iterations.add((run_id, iteration))

    observations["tool_calls"] = ProducedObservation.measured(len(tool_attempts))
    if tool_attempts:
        failed = sum(call.get("success") is not True for call in tool_attempts)
        observations["tool_error_rate"] = ProducedObservation.measured(failed / len(tool_attempts))
    else:
        observations["tool_error_rate"] = ProducedObservation.unavailable(
            "Tool error rate is undefined because no tool calls were attempted."
        )
    observations["agent_loop_iterations"] = ProducedObservation.measured(len(iterations))
    observations["capped_agent_runs"] = ProducedObservation.measured(
        sum(run.get("max_iterations_hit") is True for run in runs)
    )
    observations["agent_runs"] = ProducedObservation.measured(len(runs))
    # The loop's own counters (the notebook's react_* columns), as opposed to
    # the step-derived turns and tool calls above, which cover every run.
    if runs and not any("reason_act" in run for run in runs):
        unrecorded = ProducedObservation.unavailable("This attempt did not record Reason+Act loop state.")
        for key in ("react_runs", "react_turns", "react_tool_calls"):
            observations[key] = unrecorded
        return observations
    react = [run["reason_act"] for run in runs if isinstance(run.get("reason_act"), Mapping)]
    observations["react_runs"] = ProducedObservation.measured(len(react))
    if react:
        observations["react_turns"] = ProducedObservation.measured(sum(int(r.get("iterations") or 0) for r in react))
        observations["react_tool_calls"] = ProducedObservation.measured(
            sum(int(r.get("tool_calls") or 0) for r in react)
        )
    else:
        for key in ("react_turns", "react_tool_calls"):
            observations[key] = ProducedObservation.unavailable("No Reason+Act run took part in this attempt.")
    return observations


class RuntimeMetricProducer:
    """Compute runtime observations from a previously collected attempt snapshot."""

    producer_id = "asaree.runtime"
    output_keys = (
        "cost_usd",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "thinking_tokens",
        "duration_seconds",
        "tool_calls",
        "tool_error_rate",
        "agent_loop_iterations",
        "critic_rejections",
        "critic_approvals",
        "critic_invocations",
        "critic_rejections_partial",
        "critic_rejections_full",
        "revision_rounds",
        "capped_agent_runs",
        "agent_runs",
        "react_runs",
        "react_turns",
        "react_tool_calls",
        "prompt_sha256",
    )

    def capability_for(self, snapshot: ExperimentSnapshot) -> ProducerCapability | None:
        if not any(value.value_type == "runtime_facts" for value in snapshot.inputs.values()):
            return None
        scalar_outputs = {
            key: ScalarOutputCapability(value_type="number", aggregations=_SUM_OR_MEAN) for key in self.output_keys
        }
        scalar_outputs["tool_error_rate"] = ScalarOutputCapability(value_type="number", aggregations=_RATE_AGGREGATIONS)
        scalar_outputs["prompt_sha256"] = ScalarOutputCapability(value_type="opaque", aggregations={"none"})
        return ProducerCapability(
            producer_id=self.producer_id,
            kind="runtime",
            version="1",
            inputs={"facts": "runtime_facts"},
            scalar_outputs=scalar_outputs,
        )

    async def evaluate(
        self,
        binding: ProducerBinding,
        inputs: dict[str, MeasurementInput],
        attempt: CompletedReplicate,
    ) -> ProducerResult:
        del attempt
        facts = inputs.get("facts")
        if facts is None or not isinstance(facts.value, Mapping):
            raise ValueError("runtime producer requires a runtime_facts input")
        collection_error = facts.value.get("collection_error")
        if isinstance(collection_error, str):
            return ProducerResult(
                observations={key: ProducedObservation.failed(collection_error) for key in binding.outputs}
            )
        runs = facts.value.get("runs")
        attributed_runs = [run for run in runs if isinstance(run, Mapping)] if isinstance(runs, list) else []

        observations = _run_observations(attributed_runs)
        observations["duration_seconds"] = _duration(facts.value)

        reviews = facts.value.get("critic_reviews")
        critic_reviews = (
            [review for review in reviews if isinstance(review, Mapping)] if isinstance(reviews, list) else []
        )
        gates = facts.value.get("critic_gates")
        critic_gates = [gate for gate in gates if isinstance(gate, Mapping)] if isinstance(gates, list) else []
        revision_rounds = sum(max(int(gate.get("revisions_used") or 0), 0) for gate in critic_gates)
        if critic_reviews:
            # The gate revises on anything but an explicit approval, so a null
            # verdict is a rejection here too.
            rejections = [review for review in critic_reviews if review.get("approved") is not True]
            partial = sum(review.get("rejection_scope") == "partial" for review in rejections)
            observations["critic_rejections"] = ProducedObservation.measured(len(rejections))
            observations["critic_approvals"] = ProducedObservation.measured(len(critic_reviews) - len(rejections))
            observations["critic_invocations"] = ProducedObservation.measured(len(critic_reviews))
            # An absent or unrecognized scope revises as "full" (see
            # _build_revision_instruction), so the partial count is a floor.
            observations["critic_rejections_partial"] = ProducedObservation.measured(partial)
            observations["critic_rejections_full"] = ProducedObservation.measured(len(rejections) - partial)
        else:
            # Compatibility for an attempt collected before critic runs were
            # explicitly role-tagged in Motoro metadata -- or a run whose gates
            # never called a critic, where every count is genuinely zero.
            approvals = sum(gate.get("approved") is True and gate.get("forced") is not True for gate in critic_gates)
            observations["critic_rejections"] = ProducedObservation.measured(revision_rounds)
            observations["critic_approvals"] = ProducedObservation.measured(approvals)
            observations["critic_invocations"] = ProducedObservation.measured(revision_rounds + approvals)
            if revision_rounds:
                for key in ("critic_rejections_partial", "critic_rejections_full"):
                    observations[key] = ProducedObservation.unavailable(
                        "This attempt did not record each rejection's scope."
                    )
            else:
                observations["critic_rejections_partial"] = ProducedObservation.measured(0)
                observations["critic_rejections_full"] = ProducedObservation.measured(0)
        observations["revision_rounds"] = ProducedObservation.measured(revision_rounds)
        prompt_sha = facts.value.get("prompt_sha256")
        observations["prompt_sha256"] = (
            ProducedObservation.measured(prompt_sha)
            if isinstance(prompt_sha, str)
            else ProducedObservation.unavailable("This attempt's published protocol revision has no prompt text.")
        )
        return ProducerResult(observations=observations)


class NodeRuntimeMetricProducer:
    """Runtime totals over only the Motoro runs launched by chosen canvas nodes.

    The notebook's per-stage ``tokens_<stage>`` / ``n_turns_<stage>`` columns:
    ``config.node_ids`` names the nodes a stage is made of -- one Agent, or
    every Critic Gate for a "critic" stage -- and every attempt each of them
    made counts, revisions included. A separate producer rather than a scoped
    ``asaree.runtime`` binding so the built-in runtime metrics stay one
    unscoped binding with one metric per output."""

    producer_id = "asaree.node_runtime"
    output_keys = (
        "agent_runs",
        "cost_usd",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "thinking_tokens",
        "tool_calls",
        "tool_error_rate",
        "agent_loop_iterations",
        "capped_agent_runs",
        "react_runs",
        "react_turns",
        "react_tool_calls",
    )

    def capability_for(self, snapshot: ExperimentSnapshot) -> ProducerCapability | None:
        if not any(value.value_type == "runtime_facts" for value in snapshot.inputs.values()):
            return None
        scalar_outputs = {
            key: ScalarOutputCapability(value_type="number", aggregations=_SUM_OR_MEAN) for key in self.output_keys
        }
        scalar_outputs["tool_error_rate"] = ScalarOutputCapability(value_type="number", aggregations=_RATE_AGGREGATIONS)
        return ProducerCapability(
            producer_id=self.producer_id,
            kind="runtime",
            version="1",
            inputs={"facts": "runtime_facts"},
            scalar_outputs=scalar_outputs,
        )

    async def evaluate(
        self,
        binding: ProducerBinding,
        inputs: dict[str, MeasurementInput],
        attempt: CompletedReplicate,
    ) -> ProducerResult:
        del attempt
        facts = inputs.get("facts")
        if facts is None or not isinstance(facts.value, Mapping):
            raise ValueError("node runtime producer requires a runtime_facts input")
        collection_error = facts.value.get("collection_error")
        if isinstance(collection_error, str):
            return ProducerResult(
                observations={key: ProducedObservation.failed(collection_error) for key in binding.outputs}
            )
        node_ids = set(binding_node_ids(binding.config))
        runs = facts.value.get("runs")
        all_runs = [run for run in runs if isinstance(run, Mapping)] if isinstance(runs, list) else []
        if all_runs and not any("node_id" in run for run in all_runs):
            return ProducerResult(
                observations={
                    key: ProducedObservation.unavailable("This attempt did not record which node launched each run.")
                    for key in binding.outputs
                }
            )
        scoped = [run for run in all_runs if run.get("node_id") in node_ids]
        observations = _run_observations(scoped)
        return ProducerResult(observations=observations)


def binding_node_ids(config: Mapping[str, Any]) -> list[str]:
    node_ids = config.get("node_ids")
    if not isinstance(node_ids, list):
        return []
    return [node_id for node_id in node_ids if isinstance(node_id, str) and node_id]


_RUNTIME_PRODUCER_IDS = frozenset({RuntimeMetricProducer.producer_id, NodeRuntimeMetricProducer.producer_id})


def _node_scope_issues(
    declared: MeasurementPlan,
    graph: Mapping[str, Any] | None,
    preserved_binding_ids: set[str] | None,
) -> list[ValidationIssue]:
    issues = []
    nodes = node_map(graph) if graph is not None else None
    for index, binding in enumerate(declared.producers):
        if binding.producer_id != NodeRuntimeMetricProducer.producer_id:
            continue
        path = f"producers[{index}].config.node_ids"
        node_ids = binding_node_ids(binding.config)
        severity = preserved_binding_severity(binding.id, preserved_binding_ids, has_preserved_value=bool(node_ids))
        if not node_ids:
            issues.append(ValidationIssue("node_runtime_nodes_missing", "Choose at least one node to measure.", path))
            continue
        missing = [node_id for node_id in node_ids if nodes is not None and node_id not in nodes]
        if missing:
            issues.append(
                ValidationIssue(
                    "node_runtime_node_unavailable",
                    f"Measured node(s) {', '.join(missing)} are not on the canvas.",
                    path,
                    severity,
                )
            )
    return issues


def validate_runtime_measurement_plan(
    document: Any,
    *,
    preserved_binding_ids: set[str] | None = None,
    graph: Mapping[str, Any] | None = None,
) -> ValidationReport:
    """Validate the runtime-producer slice of a complete measurement plan.

    ``graph``, when given, is checked against node-scoped bindings' node ids."""
    if not document:
        return ValidationReport(())
    declared = parse_measurement_plan(normalize_experiment_measurement_plan(document, ()))
    scope_issues = _node_scope_issues(declared, graph, preserved_binding_ids)
    bindings = tuple(binding for binding in declared.producers if binding.producer_id in _RUNTIME_PRODUCER_IDS)
    if not bindings:
        return ValidationReport(())
    binding_ids = {binding.id for binding in bindings}
    metric_ids = {metric_id for binding in bindings for metric_id in binding.outputs.values()}
    plan = MeasurementPlan(
        metrics=tuple(metric for metric in declared.metrics if metric.id in metric_ids),
        producers=bindings,
        inputs=tuple(item for item in declared.inputs if item.producer_binding_id in binding_ids),
    )
    snapshot = ExperimentSnapshot(
        experiment_id="validation",
        inputs={
            "attempt.runtime": MeasurementInput(value_type="runtime_facts", value={}),
            **{item.source_key: MeasurementInput(value_type="runtime_facts", value={}) for item in plan.inputs},
        },
    )
    report = MeasurementEngine([RuntimeMetricProducer(), NodeRuntimeMetricProducer()]).validate(
        plan, snapshot, require_primary=False
    )
    producer_path = re.compile(r"^producers\[(\d+)\]")
    input_path = re.compile(r"^inputs\[(\d+)\]")

    def restore_full_plan_path(issue: ValidationIssue) -> ValidationIssue:
        match = producer_path.match(issue.path)
        if match is not None and int(match.group(1)) < len(bindings):
            binding = bindings[int(match.group(1))]
            full_index = declared.producers.index(binding)
            path = producer_path.sub(f"producers[{full_index}]", issue.path, count=1)
            if issue.code == "unknown_output" and binding.id in (preserved_binding_ids or set()):
                return ValidationIssue(
                    code="runtime_output_unavailable",
                    message=issue.message,
                    path=path,
                    severity="needs_attention",
                )
            return replace(issue, path=path)
        match = input_path.match(issue.path)
        if match is not None and int(match.group(1)) < len(plan.inputs):
            full_index = declared.inputs.index(plan.inputs[int(match.group(1))])
            return replace(issue, path=input_path.sub(f"inputs[{full_index}]", issue.path, count=1))
        return issue

    return ValidationReport((*scope_issues, *(restore_full_plan_path(issue) for issue in report.issues)))


async def finalize_attempt_measurement(
    db: AsyncSession,
    protocol_run_id: uuid.UUID,
) -> bool:
    """Freeze every declared producer observation on one attempt.

    Runtime observations are evaluated as one immutable attempt document.
    """
    run = await db.get(ProtocolRun, protocol_run_id)
    if (
        run is None
        or (run.status not in TERMINAL_PROTOCOL_RUN_STATUSES and run.status != "finalizing")
        or (run.replicate_result_id is None and not run.is_test_run and run.target_node_id is None)
        or (run.attempt_result or {}).get("measurement") is not None
    ):
        return False
    protocol = await db.get(Protocol, run.protocol_id)
    if protocol is None or protocol.experiment_id is None:
        return False
    experiment = await db.get(ResearchExperiment, protocol.experiment_id)
    if experiment is None:
        return False
    attempt_result = run.attempt_result if isinstance(run.attempt_result, Mapping) else {}
    document = attempt_result.get("measurement_plan_snapshot")
    if document is None:
        # Compatibility for attempts created before plans were snapshotted at
        # queue time. New attempts never consult mutable experiment state here.
        document = (
            experiment.locked_measurement_plan if experiment.locked_at is not None else experiment.measurement_plan
        )
    if not document:
        return False
    declared = parse_measurement_plan(normalize_experiment_measurement_plan(document, ()))
    evaluation_state = (run.attempt_result or {}).get("evaluation_state")
    if evaluation_state in {"running", "completed"}:
        interrupted_status: ObservationStatus | None = None
        if evaluation_state == "running" and (
            run.status in {"failed", "cancelled", "limit_reached"} or run.cancel_requested_at is not None
        ):
            interrupted_status = (
                "cancelled" if run.status == "cancelled" or run.cancel_requested_at is not None else "failed"
            )
        if interrupted_status is not None:
            attempt = CompletedReplicate(
                replicate_id=str(run.replicate_result_id or run.id),
                attempt_id=str(run.id),
            )
            interrupted = MeasurementEngine(()).complete_declared_metrics(
                declared,
                attempt,
                None,
                status=lambda _metric, binding: (
                    interrupted_status
                    if binding is not None and binding.producer_id in _SUPPORTED_PRODUCER_IDS
                    else "unavailable"
                ),
                error=lambda _metric, binding: (
                    (
                        "Evaluation was cancelled before this producer completed."
                        if interrupted_status == "cancelled"
                        else "Evaluation was interrupted; the producer was not retried."
                    )
                    if binding is not None and binding.producer_id in _SUPPORTED_PRODUCER_IDS
                    else (
                        f"Producer {binding.producer_id!r} is not available for this evaluation."
                        if binding is not None
                        else "No supported metric producer is bound to this declared metric."
                    )
                ),
            )
            interrupted_result = dict(run.attempt_result or {})
            interrupted_result["evaluation_summary"] = {"cost_usd": 0.0}
            run.attempt_result = interrupted_result
            await record_measurement_evaluation(db, run.id, interrupted)
            completed_result = dict(run.attempt_result or {})
            completed_result["evaluation_state"] = "completed"
            run.attempt_result = completed_result
            if interrupted_status == "cancelled" and run.status not in TERMINAL_PROTOCOL_RUN_STATUSES:
                run.status = "cancelled"
                run.completed_at = datetime.now(UTC)
            await db.flush()
            return True
        return False
    supported_bindings = tuple(
        binding for binding in declared.producers if binding.producer_id in _SUPPORTED_PRODUCER_IDS
    )
    bindings = supported_bindings
    if not supported_bindings and not run.is_test_run:
        return False
    binding_ids = {binding.id for binding in bindings}
    metric_ids = {metric_id for binding in bindings for metric_id in binding.outputs.values()}
    metrics = tuple(metric for metric in declared.metrics if metric.id in metric_ids)
    inputs = tuple(item for item in declared.inputs if item.producer_binding_id in binding_ids)
    supported_plan = replace(declared, metrics=metrics, producers=bindings, inputs=inputs)

    revision = await db.get(ProtocolRevision, run.protocol_revision_id) if run.protocol_revision_id else None
    measurement_graph = resolve_reported_metric_graph(
        revision.graph if revision is not None else {},
        run.factor_values or {},
    )
    from asaree.services.experiment_measurements import (
        blocking_measurement_plan_issues,
        unavailable_producer_reasons,
        validate_experiment_measurement_plan,
    )

    # The attempt snapshot is the execution contract.  Full experiment
    # validation reconciles a draft plan with the selected design metrics and
    # rejects unbound declarations; both checks belong before publishing, not
    # here.  At finalization we validate only the supported, bound slice that
    # can still execute.  The engine completes every other declared metric as
    # unavailable below, preserving historical plans without consulting
    # mutable experiment design state.
    readiness_metrics = design_metrics_from_measurement_plan(supported_plan)
    readiness_report = await validate_experiment_measurement_plan(
        db,
        document=supported_plan,
        metrics=readiness_metrics,
        graph=measurement_graph,
        experiment_id=experiment.id,
        owner_id=run.owner_id,
    )
    blocking_issues = blocking_measurement_plan_issues(readiness_report)
    if blocking_issues:
        raise InvalidMeasurementPlanError(ValidationReport(blocking_issues))
    unavailable_outputs = unavailable_producer_reasons(supported_plan, readiness_report)
    ready_bindings = tuple(
        replace(
            binding,
            outputs={
                output_key: metric_id
                for output_key, metric_id in binding.outputs.items()
                if "*" not in unavailable_outputs.get(binding.id, {})
                and output_key not in unavailable_outputs.get(binding.id, {})
            },
        )
        for binding in bindings
        if "*" not in unavailable_outputs.get(binding.id, {})
        and any(output_key not in unavailable_outputs.get(binding.id, {}) for output_key in binding.outputs)
    )
    runtime_bindings = tuple(binding for binding in ready_bindings if binding.producer_id in _RUNTIME_PRODUCER_IDS)
    runtime_binding_ids = {binding.id for binding in runtime_bindings}
    runtime_metric_ids = {metric_id for binding in runtime_bindings for metric_id in binding.outputs.values()}
    runtime_plan = replace(
        declared,
        metrics=tuple(metric for metric in declared.metrics if metric.id in runtime_metric_ids),
        producers=runtime_bindings,
        inputs=tuple(item for item in declared.inputs if item.producer_binding_id in runtime_binding_ids),
    )
    has_runtime = bool(runtime_bindings)
    await db.flush()
    facts: dict[str, Any] = {}
    if has_runtime:
        try:
            facts = await collect_runtime_facts(run)
        except Exception as exc:
            facts = {
                "protocol_run_id": str(run.id),
                "collection_error": f"{type(exc).__name__}: {exc}",
            }
        facts["prompt_sha256"] = authored_prompt_sha256(measurement_graph)
        task_completed_at = (run.attempt_result or {}).get("task_completed_at")
        if isinstance(task_completed_at, str):
            # Runtime duration is task execution only. Evaluation has its own
            # boundary and is reported separately by Test Run Results.
            facts["completed_at"] = task_completed_at
    snapshot_inputs: dict[str, MeasurementInput] = {}
    for item in runtime_plan.inputs:
        if item.input_key == "facts":
            snapshot_inputs[item.source_key] = MeasurementInput(
                value_type="runtime_facts",
                value=facts,
                provenance={"protocol_run_id": str(run.id)},
            )
    snapshot = ExperimentSnapshot(
        experiment_id=str(experiment.id),
        inputs=snapshot_inputs,
        properties={},
    )
    adapters: list[MeasurementProducerAdapter] = []
    if has_runtime:
        adapters.extend((RuntimeMetricProducer(), NodeRuntimeMetricProducer()))
    settings = get_settings()
    # Persist a claim before collecting runtime facts. A concurrent finalizer
    # blocks on this row lock, then observes the durable claim and returns
    # without duplicating finalization work.
    claimed_run = (
        await db.execute(select(ProtocolRun).where(ProtocolRun.id == run.id).with_for_update())
    ).scalar_one_or_none()
    if claimed_run is None:
        return False
    claimed_result = dict(claimed_run.attempt_result or {})
    if claimed_result.get("measurement") is not None or claimed_result.get("evaluation_state") in {
        "running",
        "completed",
    }:
        return False
    claimed_result["evaluation_state"] = "running"
    claimed_result["evaluation_claimed_at"] = datetime.now(UTC).isoformat()
    claimed_run.attempt_result = claimed_result
    await db.flush()
    await db.commit()
    run = claimed_run

    cancellation_event = asyncio.Event()
    if run.cancel_requested_at is not None:
        cancellation_event.set()
    cancellation_watcher = asyncio.create_task(_watch_measurement_cancellation(run.id, cancellation_event))
    try:
        engine = MeasurementEngine(
            adapters,
            max_concurrency=settings.metric_producer_max_concurrency,
            producer_timeout_seconds=settings.metric_producer_timeout_seconds,
        )
        evaluation = await engine.evaluate(
            runtime_plan,
            snapshot,
            CompletedReplicate(replicate_id=str(run.replicate_result_id or run.id), attempt_id=str(run.id)),
            validate_plan=True,
            unavailable_outputs={
                binding_id: reasons
                for binding_id, reasons in unavailable_outputs.items()
                if binding_id in runtime_binding_ids
            },
            cancellation_event=cancellation_event,
        )
    finally:
        cancellation_watcher.cancel()
        await asyncio.gather(cancellation_watcher, return_exceptions=True)

    await db.refresh(run)
    reported = await collect_reported_metrics(run, declared, measurement_graph)
    evaluation = _merge_evaluations(evaluation, reported)

    evaluation = engine.complete_declared_metrics(
        declared,
        CompletedReplicate(replicate_id=str(run.replicate_result_id or run.id), attempt_id=str(run.id)),
        evaluation,
        status="unavailable",
        error=lambda _metric, binding: (
            f"Producer {binding.producer_id!r} is not available for this evaluation."
            if binding is not None
            else "No supported metric producer is bound to this declared metric."
        ),
    )

    attempt_result = dict(run.attempt_result or {})
    attempt_result["evaluation_summary"] = {"cost_usd": 0.0}
    run.attempt_result = attempt_result
    primary_metric_id = next((metric.id for metric in declared.metrics if metric.primary), None)
    await record_measurement_evaluation(
        db,
        run.id,
        evaluation,
        required_metric_id=primary_metric_id,
    )
    completed_result = dict(run.attempt_result or {})
    completed_result["evaluation_state"] = "completed"
    run.attempt_result = completed_result
    if any(observation.status == "cancelled" for observation in evaluation.observations):
        run.status = "cancelled"
        run.completed_at = datetime.now(UTC)
        await db.flush()
    return True


__all__ = [
    "NodeRuntimeMetricProducer",
    "RuntimeMetricProducer",
    "authored_prompt_sha256",
    "collect_runtime_facts",
    "finalize_attempt_measurement",
    "validate_runtime_measurement_plan",
]
