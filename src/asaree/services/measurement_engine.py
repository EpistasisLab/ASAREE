"""Producer-neutral contracts and orchestration for experiment measurements.

Concrete producers live behind :class:`MeasurementProducerAdapter`.  Callers
submit one normalized plan and receive the same observation/artifact shape for
built-in runtime facts and reported custom values.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence, Set
from copy import deepcopy
from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Any, Literal, Protocol, cast, runtime_checkable

from pydantic import TypeAdapter, ValidationError

ProducerKind = Literal["runtime", "reported"]
ProvenanceKind = ProducerKind | Literal["legacy", "unbound"]
MetricValueType = Literal["number", "boolean", "opaque"]
MetricDirection = Literal["maximize", "minimize", "neutral"]
MetricAggregation = Literal["mean", "sum", "rate", "pooled", "none"]
ObservationStatus = Literal[
    "measured",
    "unavailable",
    "failed",
    "timed_out",
    "cancelled",
    "not_applicable",
]
# Runtime metrics remain scalar. Reported custom metrics deliberately retain
# the tool's opaque result, so observations must be able to carry any
# JSON-compatible value without coercing or validating it.
ScalarValue = Any


@dataclass(frozen=True)
class MetricDefinition:
    id: str
    name: str
    value_type: MetricValueType
    direction: MetricDirection
    aggregation: MetricAggregation
    primary: bool = False
    description: str | None = None
    unit: str | None = None


@dataclass(frozen=True)
class ProducerBinding:
    id: str
    producer_id: str
    kind: ProducerKind
    outputs: Mapping[str, str]
    artifacts: Sequence[str] = ()
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProducerInputBinding:
    producer_binding_id: str
    input_key: str
    source_key: str


@dataclass(frozen=True)
class MeasurementPlan:
    """Normalized declaration: definitions, producers, and inputs stay separate."""

    metrics: Sequence[MetricDefinition] = ()
    producers: Sequence[ProducerBinding] = ()
    inputs: Sequence[ProducerInputBinding] = ()


_MEASUREMENT_PLAN_ADAPTER = TypeAdapter(MeasurementPlan)


class MultiplePrimaryMetricsError(ValueError):
    """Raised when a measurement declaration marks more than one primary."""


def ensure_at_most_one_primary(primary_flags: Sequence[bool]) -> None:
    if sum(primary_flags) > 1:
        raise MultiplePrimaryMetricsError("A measurement plan can have at most one primary metric.")


def parse_measurement_plan(document: Any) -> MeasurementPlan:
    """Parse the one canonical persisted measurement-plan shape."""
    try:
        return _MEASUREMENT_PLAN_ADAPTER.validate_python(document)
    except ValidationError as exc:
        raise ValueError(f"Invalid measurement plan: {exc}") from exc


def normalize_measurement_plan(document: Any) -> dict[str, Any]:
    """Return a JSON-compatible canonical plan document."""
    plan = parse_measurement_plan(document)
    ensure_at_most_one_primary(tuple(metric.primary for metric in plan.metrics))
    return cast(
        dict[str, Any],
        _MEASUREMENT_PLAN_ADAPTER.dump_python(plan, mode="json", exclude_none=True),
    )


@dataclass(frozen=True)
class ScalarOutputCapability:
    value_type: MetricValueType
    aggregations: Set[MetricAggregation]


@dataclass(frozen=True)
class ArtifactCapability:
    kind: str


@dataclass(frozen=True)
class ProducerCapability:
    producer_id: str
    kind: ProducerKind
    version: str
    inputs: Mapping[str, str]
    scalar_outputs: Mapping[str, ScalarOutputCapability]
    artifact_outputs: Mapping[str, ArtifactCapability] = field(default_factory=dict)
    dynamic_scalar_outputs: bool = False


@dataclass(frozen=True)
class MeasurementInput:
    value_type: str
    value: Any
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExperimentSnapshot:
    experiment_id: str
    inputs: Mapping[str, MeasurementInput]
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CompletedReplicate:
    replicate_id: str
    attempt_id: str


@dataclass(frozen=True)
class ProducedObservation:
    status: ObservationStatus
    value: ScalarValue | None = None
    error: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def measured(cls, value: ScalarValue) -> ProducedObservation:
        return cls(status="measured", value=value)

    @classmethod
    def unavailable(cls, error: str | None = None) -> ProducedObservation:
        return cls(status="unavailable", error=error)

    @classmethod
    def failed(cls, error: str) -> ProducedObservation:
        return cls(status="failed", error=error)

    @classmethod
    def timed_out(cls, error: str) -> ProducedObservation:
        return cls(status="timed_out", error=error)

    @classmethod
    def cancelled(cls, error: str) -> ProducedObservation:
        return cls(status="cancelled", error=error)

    @classmethod
    def not_applicable(cls, error: str | None = None) -> ProducedObservation:
        return cls(status="not_applicable", error=error)


@dataclass(frozen=True)
class ProducedArtifact:
    kind: str
    payload: Any


@dataclass(frozen=True)
class ProducerResult:
    observations: Mapping[str, ProducedObservation] = field(default_factory=dict)
    artifacts: Mapping[str, ProducedArtifact] = field(default_factory=dict)


@dataclass(frozen=True)
class ProducerProvenance:
    binding_id: str
    producer_id: str
    kind: ProvenanceKind
    version: str
    binding_config: Mapping[str, Any] = field(default_factory=dict)
    evaluation: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricObservation:
    metric_id: str
    metric_name: str
    value_type: MetricValueType
    status: ObservationStatus
    value: ScalarValue | None
    error: str | None
    attempt_id: str
    producer: ProducerProvenance
    input_provenance: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class EvaluationArtifact:
    artifact_key: str
    kind: str
    payload: Any
    attempt_id: str
    producer: ProducerProvenance
    input_provenance: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class MeasurementEvaluation:
    replicate_id: str
    attempt_id: str
    observations: tuple[MetricObservation, ...]
    artifacts: tuple[EvaluationArtifact, ...]

    def to_document(self) -> dict[str, Any]:
        """Return a detached JSON-compatible attempt/projection document."""
        return {
            "replicate_id": self.replicate_id,
            "attempt_id": self.attempt_id,
            "observations": [
                {
                    "metric_id": item.metric_id,
                    "metric_name": item.metric_name,
                    "value_type": item.value_type,
                    "status": item.status,
                    "value": item.value,
                    "error": item.error,
                    "attempt_id": item.attempt_id,
                    "producer": {
                        "binding_id": item.producer.binding_id,
                        "producer_id": item.producer.producer_id,
                        "kind": item.producer.kind,
                        "version": item.producer.version,
                        **(
                            {"binding_config": deepcopy(item.producer.binding_config)}
                            if item.producer.binding_config
                            else {}
                        ),
                        **({"evaluation": deepcopy(item.producer.evaluation)} if item.producer.evaluation else {}),
                    },
                    "input_provenance": deepcopy(item.input_provenance),
                }
                for item in self.observations
            ],
            "artifacts": [
                {
                    "artifact_key": item.artifact_key,
                    "kind": item.kind,
                    "payload": deepcopy(item.payload),
                    "attempt_id": item.attempt_id,
                    "producer": {
                        "binding_id": item.producer.binding_id,
                        "producer_id": item.producer.producer_id,
                        "kind": item.producer.kind,
                        "version": item.producer.version,
                        **(
                            {"binding_config": deepcopy(item.producer.binding_config)}
                            if item.producer.binding_config
                            else {}
                        ),
                        **({"evaluation": deepcopy(item.producer.evaluation)} if item.producer.evaluation else {}),
                    },
                    "input_provenance": deepcopy(item.input_provenance),
                }
                for item in self.artifacts
            ],
        }


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    path: str
    severity: Literal["blocking", "needs_attention"] = "blocking"


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues


def preserved_binding_severity(
    binding_id: str,
    preserved_binding_ids: set[str] | None,
    *,
    has_preserved_value: bool = True,
) -> Literal["blocking", "needs_attention"]:
    """Keep broken durable bindings repairable without relaxing new drafts."""
    return "needs_attention" if has_preserved_value and binding_id in (preserved_binding_ids or set()) else "blocking"


def validate_measurement_plan_structure(
    plan: MeasurementPlan,
    *,
    require_primary: bool = False,
) -> ValidationReport:
    """Validate producer-neutral invariants across the complete plan."""
    issues: list[ValidationIssue] = []
    metrics_by_id: dict[str, MetricDefinition] = {}
    names: set[str] = set()
    for index, metric in enumerate(plan.metrics):
        if metric.id in metrics_by_id:
            issues.append(
                ValidationIssue(
                    "duplicate_metric_id",
                    f"Metric id {metric.id!r} is declared more than once.",
                    f"metrics[{index}].id",
                )
            )
        else:
            metrics_by_id[metric.id] = metric
        folded_name = metric.name.strip().casefold()
        if folded_name in names:
            issues.append(
                ValidationIssue(
                    "duplicate_metric_name",
                    f"Metric name {metric.name.strip()!r} is declared more than once.",
                    f"metrics[{index}].name",
                )
            )
        names.add(folded_name)
        if metric.primary and metric.direction == "neutral":
            issues.append(
                ValidationIssue(
                    "invalid_direction",
                    f"Neutral metric {metric.name!r} cannot be the primary ranking metric.",
                    f"metrics[{index}].direction",
                )
            )

    producer_ids: set[str] = set()
    bound_metric_ids: list[str] = []
    for index, binding in enumerate(plan.producers):
        if binding.id in producer_ids:
            issues.append(
                ValidationIssue(
                    "duplicate_producer_binding_id",
                    f"Producer binding id {binding.id!r} is declared more than once.",
                    f"producers[{index}].id",
                )
            )
        producer_ids.add(binding.id)
        for output_key, metric_id in binding.outputs.items():
            if metric_id not in metrics_by_id:
                issues.append(
                    ValidationIssue(
                        "unknown_metric",
                        f"Output {output_key!r} refers to unknown metric {metric_id!r}.",
                        f"producers[{index}].outputs",
                    )
                )
            else:
                bound_metric_ids.append(metric_id)

    seen_inputs: set[tuple[str, str]] = set()
    for index, item in enumerate(plan.inputs):
        if item.producer_binding_id not in producer_ids:
            issues.append(
                ValidationIssue(
                    "unknown_producer_binding",
                    f"Input refers to unknown producer binding {item.producer_binding_id!r}.",
                    f"inputs[{index}].producer_binding_id",
                )
            )
        identity = (item.producer_binding_id, item.input_key)
        if identity in seen_inputs:
            issues.append(
                ValidationIssue(
                    "duplicate_input_binding",
                    (
                        f"Input {item.input_key!r} is bound more than once for producer binding "
                        f"{item.producer_binding_id!r}."
                    ),
                    f"inputs[{index}].input_key",
                )
            )
        seen_inputs.add(identity)

    for index, metric in enumerate(plan.metrics):
        binding_count = bound_metric_ids.count(metric.id)
        if binding_count == 0:
            issues.append(
                ValidationIssue(
                    "unbound_metric", f"Metric {metric.name!r} has no producer output binding.", f"metrics[{index}]"
                )
            )
        elif binding_count > 1:
            issues.append(
                ValidationIssue(
                    "duplicate_metric_binding",
                    f"Metric {metric.name!r} has more than one producer output binding.",
                    f"metrics[{index}]",
                )
            )

    primary_count = sum(metric.primary for metric in plan.metrics)
    if require_primary and plan.metrics and primary_count == 0:
        issues.append(
            ValidationIssue(
                "missing_primary_metric", "A non-empty measurement plan must have one primary metric.", "metrics"
            )
        )
    elif primary_count > 1:
        issues.append(
            ValidationIssue(
                "multiple_primary_metrics", "A measurement plan can have at most one primary metric.", "metrics"
            )
        )
    return ValidationReport(tuple(issues))


class InvalidMeasurementPlanError(ValueError):
    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__("measurement plan is invalid: " + "; ".join(issue.message for issue in report.issues))


class MeasurementProducerAdapter(Protocol):
    producer_id: str

    def capability_for(self, snapshot: ExperimentSnapshot) -> ProducerCapability | None: ...

    async def evaluate(
        self,
        binding: ProducerBinding,
        inputs: dict[str, MeasurementInput],
        attempt: CompletedReplicate,
    ) -> ProducerResult: ...


@runtime_checkable
class MeasurementBindingValidator(Protocol):
    """Optional producer-specific validation at the engine's public seam."""

    def validate_binding(
        self,
        binding: ProducerBinding,
        snapshot: ExperimentSnapshot,
        *,
        path: str,
    ) -> Sequence[ValidationIssue]: ...


@runtime_checkable
class DynamicScalarOutputProvider(Protocol):
    """Resolve binding-declared output contracts for extensible producers."""

    def scalar_output_for(
        self,
        binding: ProducerBinding,
        output_key: str,
        snapshot: ExperimentSnapshot,
    ) -> ScalarOutputCapability | None: ...


class MeasurementEngine:
    """The sole public interface for capability, validation, and evaluation."""

    def __init__(
        self,
        adapters: Sequence[MeasurementProducerAdapter],
        *,
        max_concurrency: int = 4,
        producer_timeout_seconds: float = 30,
    ) -> None:
        self._adapters = {adapter.producer_id: adapter for adapter in adapters}
        if len(self._adapters) != len(adapters):
            raise ValueError("producer adapter ids must be unique")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if producer_timeout_seconds <= 0:
            raise ValueError("producer_timeout_seconds must be positive")
        self._max_concurrency = max_concurrency
        self._producer_timeout_seconds = producer_timeout_seconds

    def list_capabilities(self, snapshot: ExperimentSnapshot) -> tuple[ProducerCapability, ...]:
        applicable: list[ProducerCapability] = []
        for adapter in self._adapters.values():
            capability = adapter.capability_for(snapshot)
            if capability is None:
                continue
            if capability.producer_id != adapter.producer_id:
                raise ValueError("adapter capability producer id must match its registered producer id")
            applicable.append(capability)
        return tuple(applicable)

    def validate(
        self,
        plan: MeasurementPlan,
        snapshot: ExperimentSnapshot,
        *,
        require_primary: bool = False,
    ) -> ValidationReport:
        issues = list(validate_measurement_plan_structure(plan, require_primary=require_primary).issues)
        capabilities = {item.producer_id: item for item in self.list_capabilities(snapshot)}
        metrics_by_id: dict[str, MetricDefinition] = {}
        for metric in plan.metrics:
            metrics_by_id.setdefault(metric.id, metric)

        input_bindings: dict[str, dict[str, tuple[int, ProducerInputBinding]]] = {}
        for index, item in enumerate(plan.inputs):
            by_key = input_bindings.setdefault(item.producer_binding_id, {})
            by_key.setdefault(item.input_key, (index, item))

        for index, binding in enumerate(plan.producers):
            capability = capabilities.get(binding.producer_id)
            if capability is None:
                issues.append(
                    ValidationIssue(
                        code="unknown_producer",
                        message=f"Unknown or inapplicable producer {binding.producer_id!r}.",
                        path=f"producers[{index}].producer_id",
                    )
                )
                continue
            if binding.kind != capability.kind:
                issues.append(
                    ValidationIssue(
                        code="incompatible_producer_kind",
                        message=(f"Producer {binding.producer_id!r} is {capability.kind!r}, not {binding.kind!r}."),
                        path=f"producers[{index}].kind",
                    )
                )
            adapter = self._adapters[binding.producer_id]
            if isinstance(adapter, MeasurementBindingValidator):
                issues.extend(adapter.validate_binding(binding, snapshot, path=f"producers[{index}]"))
            configured_inputs = input_bindings.get(binding.id, {})
            for input_key in capability.inputs:
                if input_key not in configured_inputs:
                    issues.append(
                        ValidationIssue(
                            code="missing_input",
                            message=f"Producer binding {binding.id!r} needs input {input_key!r}.",
                            path=f"producers[{index}]",
                        )
                    )
            for input_key, (input_index, input_binding) in configured_inputs.items():
                expected_type = capability.inputs.get(input_key)
                if expected_type is None:
                    issues.append(
                        ValidationIssue(
                            code="unknown_input",
                            message=f"Producer {binding.producer_id!r} has no input {input_key!r}.",
                            path=f"inputs[{input_index}].input_key",
                        )
                    )
                    continue
                source = snapshot.inputs.get(input_binding.source_key)
                if source is None:
                    issues.append(
                        ValidationIssue(
                            code="missing_input",
                            message=f"Measurement input source {input_binding.source_key!r} is unavailable.",
                            path=f"inputs[{input_index}].source_key",
                        )
                    )
                elif source.value_type != expected_type:
                    issues.append(
                        ValidationIssue(
                            code="incompatible_input_type",
                            message=(
                                f"Input {input_key!r} needs {expected_type!r}, but source "
                                f"{input_binding.source_key!r} provides {source.value_type!r}."
                            ),
                            path=f"inputs[{input_index}].source_key",
                        )
                    )
            for output_key, metric_id in binding.outputs.items():
                output = capability.scalar_outputs.get(output_key)
                if (
                    output is None
                    and capability.dynamic_scalar_outputs
                    and isinstance(adapter, DynamicScalarOutputProvider)
                ):
                    output = adapter.scalar_output_for(binding, output_key, snapshot)
                resolved_metric = metrics_by_id.get(metric_id)
                if output is None:
                    issues.append(
                        ValidationIssue(
                            code="unknown_output",
                            message=f"Producer {binding.producer_id!r} has no scalar output {output_key!r}.",
                            path=f"producers[{index}].outputs.{output_key}",
                        )
                    )
                    continue
                if resolved_metric is None:
                    continue
                if resolved_metric.value_type != output.value_type:
                    issues.append(
                        ValidationIssue(
                            code="incompatible_output_type",
                            message=(
                                f"Output {output_key!r} provides {output.value_type!r}, but metric "
                                f"{resolved_metric.name!r} expects {resolved_metric.value_type!r}."
                            ),
                            path=f"producers[{index}].outputs",
                        )
                    )
                if resolved_metric.aggregation not in output.aggregations:
                    issues.append(
                        ValidationIssue(
                            code="invalid_aggregation",
                            message=(
                                f"Aggregation {resolved_metric.aggregation!r} is not defined for output {output_key!r}."
                            ),
                            path=f"metrics[{plan.metrics.index(resolved_metric)}].aggregation",
                        )
                    )
            for artifact_key in binding.artifacts:
                if artifact_key not in capability.artifact_outputs:
                    issues.append(
                        ValidationIssue(
                            code="unknown_artifact",
                            message=f"Producer {binding.producer_id!r} has no artifact output {artifact_key!r}.",
                            path=f"producers[{index}].artifacts",
                        )
                    )

        return ValidationReport(tuple(issues))

    async def evaluate(
        self,
        plan: MeasurementPlan,
        snapshot: ExperimentSnapshot,
        attempt: CompletedReplicate,
        *,
        validate_plan: bool = True,
        unavailable_outputs: Mapping[str, Mapping[str, str]] | None = None,
        cancellation_event: asyncio.Event | None = None,
    ) -> MeasurementEvaluation:
        unavailable = unavailable_outputs or {}
        runnable_bindings = tuple(
            replace(
                binding,
                outputs={
                    output_key: metric_id
                    for output_key, metric_id in binding.outputs.items()
                    if "*" not in unavailable.get(binding.id, {}) and output_key not in unavailable.get(binding.id, {})
                },
            )
            for binding in plan.producers
            if "*" not in unavailable.get(binding.id, {})
            and any(output_key not in unavailable.get(binding.id, {}) for output_key in binding.outputs)
        )
        runnable_binding_ids = {binding.id for binding in runnable_bindings}
        runnable_metric_ids = {metric_id for binding in runnable_bindings for metric_id in binding.outputs.values()}
        runnable_plan = replace(
            plan,
            metrics=tuple(metric for metric in plan.metrics if metric.id in runnable_metric_ids),
            producers=runnable_bindings,
            inputs=tuple(item for item in plan.inputs if item.producer_binding_id in runnable_binding_ids),
        )
        if validate_plan:
            report = self.validate(runnable_plan, snapshot)
            if not report.valid:
                raise InvalidMeasurementPlanError(report)

        metrics = {metric.id: metric for metric in plan.metrics}
        input_bindings: dict[str, dict[str, ProducerInputBinding]] = {}
        for item in plan.inputs:
            input_bindings.setdefault(item.producer_binding_id, {})[item.input_key] = item

        observations_by_metric: dict[str, MetricObservation] = {}
        artifacts: list[EvaluationArtifact] = []
        capabilities = {item.producer_id: item for item in self.list_capabilities(snapshot)}
        semaphore = asyncio.Semaphore(self._max_concurrency)

        def normalize_binding(binding: ProducerBinding, produced: ProducerResult) -> MeasurementEvaluation:
            capability = capabilities.get(binding.producer_id)
            resolved_inputs = {
                key: snapshot.inputs[item.source_key]
                for key, item in input_bindings.get(binding.id, {}).items()
                if item.source_key in snapshot.inputs
            }
            input_provenance = {
                key: {
                    "source_key": input_bindings[binding.id][key].source_key,
                    "value_type": value.value_type,
                    "provenance": deepcopy(value.provenance),
                }
                for key, value in resolved_inputs.items()
            }
            base_provenance = ProducerProvenance(
                binding_id=binding.id,
                producer_id=binding.producer_id,
                kind=binding.kind,
                version=capability.version if capability is not None else "unknown",
                binding_config=deepcopy(binding.config),
            )
            normalized_observations: list[MetricObservation] = []
            for output_key, metric_id in binding.outputs.items():
                metric = metrics[metric_id]
                outcome = produced.observations.get(
                    output_key,
                    ProducedObservation.unavailable(f"Producer did not return output {output_key!r}."),
                )
                if outcome.status == "measured" and not is_scalar_value(outcome.value, metric.value_type):
                    outcome = ProducedObservation.failed(
                        f"Producer returned an invalid {metric.value_type} for output {output_key!r}."
                    )
                normalized_observations.append(
                    MetricObservation(
                        metric_id=metric.id,
                        metric_name=metric.name,
                        value_type=metric.value_type,
                        status=outcome.status,
                        value=outcome.value,
                        error=outcome.error,
                        attempt_id=attempt.attempt_id,
                        producer=replace(base_provenance, evaluation=deepcopy(outcome.provenance)),
                        input_provenance=input_provenance,
                    )
                )
            normalized_artifacts = tuple(
                EvaluationArtifact(
                    artifact_key=artifact_key,
                    kind=artifact.kind,
                    payload=artifact.payload,
                    attempt_id=attempt.attempt_id,
                    producer=base_provenance,
                    input_provenance=input_provenance,
                )
                for artifact_key in binding.artifacts
                if (artifact := produced.artifacts.get(artifact_key)) is not None
            )
            return MeasurementEvaluation(
                replicate_id=attempt.replicate_id,
                attempt_id=attempt.attempt_id,
                observations=tuple(normalized_observations),
                artifacts=normalized_artifacts,
            )

        async def evaluate_binding(binding: ProducerBinding) -> MeasurementEvaluation:
            try:
                adapter = self._adapters[binding.producer_id]
                resolved_inputs = {
                    key: snapshot.inputs[item.source_key] for key, item in input_bindings.get(binding.id, {}).items()
                }
                async with semaphore:
                    produced = await asyncio.wait_for(
                        adapter.evaluate(binding, resolved_inputs, attempt),
                        timeout=self._producer_timeout_seconds,
                    )
            except TimeoutError:
                timeout = f"{self._producer_timeout_seconds:g}"
                produced = ProducerResult(
                    observations={
                        output_key: ProducedObservation.timed_out(
                            f"Producer exceeded the {timeout}s administrative timeout."
                        )
                        for output_key in binding.outputs
                    }
                )
            except Exception as exc:
                produced = ProducerResult(
                    observations={output_key: ProducedObservation.failed(str(exc)) for output_key in binding.outputs}
                )
            return normalize_binding(binding, produced)

        def cancelled_evaluation(binding: ProducerBinding) -> MeasurementEvaluation:
            return normalize_binding(
                binding,
                ProducerResult(
                    observations={
                        output_key: ProducedObservation.cancelled(
                            "Evaluation was cancelled before this producer completed."
                        )
                        for output_key in binding.outputs
                    },
                ),
            )

        cancelled_before_start = cancellation_event is not None and cancellation_event.is_set()
        producer_tasks = (
            []
            if cancelled_before_start
            else [asyncio.create_task(evaluate_binding(binding)) for binding in runnable_bindings]
        )
        produced_evaluations: list[MeasurementEvaluation] = (
            [cancelled_evaluation(binding) for binding in runnable_bindings] if cancelled_before_start else []
        )
        cancellation_waiter: asyncio.Task[bool] | None = None
        try:
            if cancelled_before_start:
                pass
            elif cancellation_event is None:
                produced_evaluations = list(await asyncio.gather(*producer_tasks))
            else:
                cancellation_waiter = asyncio.create_task(cancellation_event.wait())
                all_producers = asyncio.gather(*producer_tasks)
                completion_signals: set[asyncio.Future[Any]] = {all_producers, cancellation_waiter}
                done, _ = await asyncio.wait(
                    completion_signals,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if all_producers in done:
                    produced_evaluations = list(await all_producers)
                else:
                    for task in producer_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*producer_tasks, return_exceptions=True)
                    await asyncio.gather(all_producers, return_exceptions=True)
                    produced_evaluations = [
                        task.result()
                        if not task.cancelled() and task.exception() is None
                        else cancelled_evaluation(binding)
                        for binding, task in zip(runnable_bindings, producer_tasks, strict=True)
                    ]
        finally:
            if cancellation_waiter is not None and not cancellation_waiter.done():
                cancellation_waiter.cancel()
            if cancellation_waiter is not None:
                await asyncio.gather(cancellation_waiter, return_exceptions=True)
            for task in producer_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*producer_tasks, return_exceptions=True)

        for produced in produced_evaluations:
            observations_by_metric.update({item.metric_id: item for item in produced.observations})
            artifacts.extend(produced.artifacts)

        partial = MeasurementEvaluation(
            replicate_id=attempt.replicate_id,
            attempt_id=attempt.attempt_id,
            observations=tuple(
                observations_by_metric[metric.id] for metric in plan.metrics if metric.id in observations_by_metric
            ),
            artifacts=tuple(artifacts),
        )
        if not unavailable:
            return partial
        return self.complete_declared_metrics(
            plan,
            attempt,
            partial,
            status="unavailable",
            error=lambda _metric, binding: (
                (
                    unavailable.get(binding.id, {}).get(
                        next(
                            (
                                output_key
                                for output_key, metric_id in binding.outputs.items()
                                if metric_id == _metric.id
                            ),
                            "*",
                        ),
                        unavailable.get(binding.id, {}).get("*", "The metric producer is unavailable."),
                    )
                )
                if binding is not None
                else "No supported metric producer is bound to this declared metric."
            ),
        )

    def complete_declared_metrics(
        self,
        plan: MeasurementPlan,
        attempt: CompletedReplicate,
        evaluation: MeasurementEvaluation | None = None,
        *,
        status: ObservationStatus | Callable[[MetricDefinition, ProducerBinding | None], ObservationStatus],
        error: str | Callable[[MetricDefinition, ProducerBinding | None], str],
    ) -> MeasurementEvaluation:
        """Complete a partial/checkpointed evaluation without inventing a producer call."""
        existing = {item.metric_id: item for item in evaluation.observations} if evaluation else {}
        bindings = {metric_id: binding for binding in plan.producers for metric_id in binding.outputs.values()}
        observations: list[MetricObservation] = []
        for metric in plan.metrics:
            if metric.id in existing:
                observations.append(existing[metric.id])
                continue
            binding = bindings.get(metric.id)
            reason = error(metric, binding) if callable(error) else error
            observation_status = status(metric, binding) if callable(status) else status
            observations.append(
                MetricObservation(
                    metric_id=metric.id,
                    metric_name=metric.name,
                    value_type=metric.value_type,
                    status=observation_status,
                    value=None,
                    error=reason,
                    attempt_id=attempt.attempt_id,
                    producer=ProducerProvenance(
                        binding_id=binding.id if binding else f"unbound-{metric.id}",
                        producer_id=binding.producer_id if binding else "unbound",
                        kind=binding.kind if binding else "unbound",
                        version="unknown",
                        binding_config=deepcopy(binding.config) if binding else {},
                    ),
                    input_provenance={},
                )
            )
        return MeasurementEvaluation(
            replicate_id=attempt.replicate_id,
            attempt_id=attempt.attempt_id,
            observations=tuple(observations),
            artifacts=evaluation.artifacts if evaluation else (),
        )


def is_scalar_value(value: Any, value_type: MetricValueType) -> bool:
    if value_type == "opaque":
        return True
    if value_type == "boolean":
        return isinstance(value, bool)
    return not isinstance(value, bool) and isinstance(value, int | float) and isfinite(float(value))


__all__ = [
    "ArtifactCapability",
    "CompletedReplicate",
    "DynamicScalarOutputProvider",
    "EvaluationArtifact",
    "ExperimentSnapshot",
    "InvalidMeasurementPlanError",
    "MeasurementEngine",
    "MeasurementEvaluation",
    "MeasurementInput",
    "MeasurementBindingValidator",
    "MeasurementPlan",
    "MeasurementProducerAdapter",
    "MultiplePrimaryMetricsError",
    "MetricDefinition",
    "MetricObservation",
    "ProducedArtifact",
    "ProducedObservation",
    "ProducerBinding",
    "ProducerCapability",
    "ProducerInputBinding",
    "ProvenanceKind",
    "ProducerProvenance",
    "ProducerResult",
    "ScalarOutputCapability",
    "ValidationIssue",
    "ValidationReport",
    "ensure_at_most_one_primary",
    "is_scalar_value",
    "normalize_measurement_plan",
    "parse_measurement_plan",
    "validate_measurement_plan_structure",
]
