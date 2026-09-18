from __future__ import annotations

import asyncio
from typing import Any

import pytest

from asaree.services.measurement_engine import (
    ArtifactCapability,
    CompletedReplicate,
    EvaluationArtifact,
    ExperimentSnapshot,
    MeasurementEngine,
    MeasurementInput,
    MeasurementPlan,
    MetricDefinition,
    ProducedArtifact,
    ProducedObservation,
    ProducerBinding,
    ProducerCapability,
    ProducerInputBinding,
    ProducerResult,
    ScalarOutputCapability,
    normalize_measurement_plan,
    parse_measurement_plan,
)


class RecordingAdapter:
    def __init__(
        self,
        capability: ProducerCapability,
        result: ProducerResult,
    ) -> None:
        self.capability = capability
        self.producer_id = capability.producer_id
        self.result = result
        self.calls: list[dict[str, MeasurementInput]] = []
        self.bindings: list[ProducerBinding] = []

    def capability_for(self, snapshot: ExperimentSnapshot) -> ProducerCapability | None:
        return self.capability

    async def evaluate(
        self,
        binding: ProducerBinding,
        inputs: dict[str, MeasurementInput],
        attempt: CompletedReplicate,
    ) -> ProducerResult:
        self.bindings.append(binding)
        self.calls.append(inputs)
        return self.result


class FailingAdapter(RecordingAdapter):
    async def evaluate(
        self,
        binding: ProducerBinding,
        inputs: dict[str, MeasurementInput],
        attempt: CompletedReplicate,
    ) -> ProducerResult:
        self.calls.append(inputs)
        raise RuntimeError("producer crashed")


def test_measurement_plan_document_round_trips_in_normalized_collections() -> None:
    document: dict[str, Any] = {
        "metrics": [
            {
                "id": "quality",
                "name": "Quality",
                "value_type": "number",
                "direction": "maximize",
                "aggregation": "mean",
                "primary": True,
            }
        ],
        "producers": [
            {
                "id": "evaluator",
                "producer_id": "custom.evaluator",
                "kind": "reported",
                "outputs": {"quality": "quality"},
                "artifacts": [],
                "config": {},
            }
        ],
        "inputs": [
            {
                "producer_binding_id": "evaluator",
                "input_key": "answer",
                "source_key": "final.output",
            }
        ],
    }

    plan = parse_measurement_plan(document)

    assert plan.metrics[0].name == "Quality"
    assert plan.producers[0].outputs == {"quality": "quality"}
    assert normalize_measurement_plan(document) == document

    invalid = {**document, "metrics": [{**document["metrics"][0], "direction": "sideways"}]}
    with pytest.raises(ValueError, match="measurement plan"):
        parse_measurement_plan(invalid)

    without_primary = {
        **document,
        "metrics": [{key: value for key, value in document["metrics"][0].items() if key != "primary"}],
    }
    assert normalize_measurement_plan(without_primary)["metrics"][0]["primary"] is False

    multiple_primary = {
        **document,
        "metrics": [document["metrics"][0], {**document["metrics"][0], "id": "helpfulness", "name": "Helpfulness"}],
    }
    with pytest.raises(ValueError, match="at most one primary"):
        normalize_measurement_plan(multiple_primary)


@pytest.mark.asyncio
async def test_engine_evaluates_each_binding_once_and_normalizes_two_adapters() -> None:
    runtime = RecordingAdapter(
        ProducerCapability(
            producer_id="asaree.runtime",
            kind="runtime",
            version="1",
            inputs={"facts": "runtime_facts"},
            scalar_outputs={
                "cost_usd": ScalarOutputCapability(value_type="number", aggregations={"sum"}),
                "total_tokens": ScalarOutputCapability(value_type="number", aggregations={"sum"}),
            },
        ),
        ProducerResult(
            observations={
                "cost_usd": ProducedObservation.measured(0.42),
                "total_tokens": ProducedObservation.measured(120),
            }
        ),
    )
    evaluator = RecordingAdapter(
        ProducerCapability(
            producer_id="asaree.classification",
            kind="reported",
            version="sklearn-1.8",
            inputs={"predictions": "prediction_bundle"},
            scalar_outputs={"accuracy": ScalarOutputCapability(value_type="number", aggregations={"mean", "pooled"})},
            artifact_outputs={"confusion_matrix": ArtifactCapability(kind="confusion_matrix")},
        ),
        ProducerResult(
            observations={"accuracy": ProducedObservation.measured(0.75)},
            artifacts={
                "confusion_matrix": ProducedArtifact(
                    kind="confusion_matrix",
                    payload={"labels": ["no", "yes"], "matrix": [[2, 1], [0, 1]]},
                )
            },
        ),
    )
    engine = MeasurementEngine([runtime, evaluator])
    snapshot = ExperimentSnapshot(
        experiment_id="experiment-1",
        inputs={
            "attempt.runtime": MeasurementInput(
                value_type="runtime_facts",
                value={"attempt_id": "attempt-1"},
                provenance={"protocol_run_id": "attempt-1"},
            ),
            "model.predictions": MeasurementInput(
                value_type="prediction_bundle",
                value={"y_true": [0, 0, 1, 0], "y_pred": [0, 1, 1, 0]},
                provenance={"dataset_hash": "sha256:abc"},
            ),
        },
    )
    plan = MeasurementPlan(
        metrics=[
            MetricDefinition(
                id="cost",
                name="Provider cost",
                value_type="number",
                direction="minimize",
                aggregation="sum",
            ),
            MetricDefinition(
                id="tokens",
                name="Total tokens",
                value_type="number",
                direction="minimize",
                aggregation="sum",
            ),
            MetricDefinition(
                id="accuracy",
                name="Accuracy",
                value_type="number",
                direction="maximize",
                aggregation="pooled",
                primary=True,
            ),
        ],
        producers=[
            ProducerBinding(
                id="runtime",
                producer_id="asaree.runtime",
                kind="runtime",
                outputs={"cost_usd": "cost", "total_tokens": "tokens"},
            ),
            ProducerBinding(
                id="classification",
                producer_id="asaree.classification",
                kind="reported",
                outputs={"accuracy": "accuracy"},
                artifacts=("confusion_matrix",),
            ),
        ],
        inputs=[
            ProducerInputBinding(
                producer_binding_id="runtime",
                input_key="facts",
                source_key="attempt.runtime",
            ),
            ProducerInputBinding(
                producer_binding_id="classification",
                input_key="predictions",
                source_key="model.predictions",
            ),
        ],
    )

    assert {capability.producer_id for capability in engine.list_capabilities(snapshot)} == {
        "asaree.runtime",
        "asaree.classification",
    }
    assert engine.validate(plan, snapshot).valid

    result = await engine.evaluate(
        plan,
        snapshot,
        CompletedReplicate(replicate_id="replicate-1", attempt_id="attempt-1"),
    )

    assert len(runtime.calls) == 1
    assert len(evaluator.calls) == 1
    assert [(observation.metric_id, observation.value) for observation in result.observations] == [
        ("cost", 0.42),
        ("tokens", 120),
        ("accuracy", 0.75),
    ]
    assert result.observations[0].producer.version == "1"
    assert result.observations[0].input_provenance == {
        "facts": {
            "source_key": "attempt.runtime",
            "value_type": "runtime_facts",
            "provenance": {"protocol_run_id": "attempt-1"},
        }
    }
    assert result.artifacts == (
        EvaluationArtifact(
            artifact_key="confusion_matrix",
            kind="confusion_matrix",
            payload={"labels": ["no", "yes"], "matrix": [[2, 1], [0, 1]]},
            attempt_id="attempt-1",
            producer=result.observations[2].producer,
            input_provenance={
                "predictions": {
                    "source_key": "model.predictions",
                    "value_type": "prediction_bundle",
                    "provenance": {"dataset_hash": "sha256:abc"},
                }
            },
        ),
    )


@pytest.mark.asyncio
async def test_engine_skips_unavailable_binding_and_records_an_explicit_outcome() -> None:
    runtime = RecordingAdapter(
        ProducerCapability(
            producer_id="asaree.runtime",
            kind="runtime",
            version="1",
            inputs={"facts": "runtime_facts"},
            scalar_outputs={"cost_usd": ScalarOutputCapability(value_type="number", aggregations={"sum"})},
        ),
        ProducerResult(observations={"cost_usd": ProducedObservation.measured(0.42)}),
    )
    evaluator = RecordingAdapter(
        ProducerCapability(
            producer_id="asaree.python_script",
            kind="reported",
            version="1",
            inputs={"evaluation": "python_evaluator_input"},
            scalar_outputs={"value": ScalarOutputCapability(value_type="number", aggregations={"mean"})},
        ),
        ProducerResult(observations={"value": ProducedObservation.measured(0.9)}),
    )
    plan = MeasurementPlan(
        metrics=[
            MetricDefinition("cost", "Cost", "number", "minimize", "sum"),
            MetricDefinition("quality", "Quality", "number", "maximize", "mean", primary=True),
        ],
        producers=[
            ProducerBinding("runtime", "asaree.runtime", "runtime", {"cost_usd": "cost"}),
            ProducerBinding(
                "quality-script",
                "asaree.python_script",
                "reported",
                {"value": "quality"},
                config={"script_node_id": "removed-script"},
            ),
        ],
        inputs=[
            ProducerInputBinding("runtime", "facts", "attempt.runtime"),
            ProducerInputBinding("quality-script", "evaluation", "attempt.quality"),
        ],
    )
    snapshot = ExperimentSnapshot(
        experiment_id="experiment-1",
        inputs={"attempt.runtime": MeasurementInput("runtime_facts", {})},
    )

    result = await MeasurementEngine([runtime, evaluator]).evaluate(
        plan,
        snapshot,
        CompletedReplicate("replicate-1", "attempt-1"),
        unavailable_outputs={"quality-script": {"*": "The Python Script is unavailable."}},
    )

    assert len(runtime.calls) == 1
    assert evaluator.calls == []
    assert [(item.metric_id, item.status, item.value) for item in result.observations] == [
        ("cost", "measured", 0.42),
        ("quality", "unavailable", None),
    ]
    assert result.observations[1].error == "The Python Script is unavailable."
    assert result.observations[1].producer.binding_config == {"script_node_id": "removed-script"}


@pytest.mark.asyncio
async def test_engine_skips_one_unavailable_output_without_suppressing_its_ready_sibling() -> None:
    runtime = RecordingAdapter(
        ProducerCapability(
            producer_id="asaree.runtime",
            kind="runtime",
            version="1",
            inputs={"facts": "runtime_facts"},
            scalar_outputs={
                "cost_usd": ScalarOutputCapability(value_type="number", aggregations={"sum"}),
                "duration_seconds": ScalarOutputCapability(value_type="number", aggregations={"mean"}),
            },
        ),
        ProducerResult(observations={"duration_seconds": ProducedObservation.measured(2.5)}),
    )
    plan = MeasurementPlan(
        metrics=[
            MetricDefinition("cost", "Cost", "number", "minimize", "sum"),
            MetricDefinition("duration", "Duration", "number", "minimize", "mean", primary=True),
        ],
        producers=[
            ProducerBinding(
                "runtime",
                "asaree.runtime",
                "runtime",
                {"cost_usd": "cost", "duration_seconds": "duration"},
            )
        ],
        inputs=[ProducerInputBinding("runtime", "facts", "attempt.runtime")],
    )

    result = await MeasurementEngine([runtime]).evaluate(
        plan,
        ExperimentSnapshot(
            experiment_id="experiment-1",
            inputs={"attempt.runtime": MeasurementInput("runtime_facts", {})},
        ),
        CompletedReplicate("replicate-1", "attempt-1"),
        unavailable_outputs={"runtime": {"cost_usd": "Cost is unavailable in this runtime."}},
    )

    assert len(runtime.calls) == 1
    assert runtime.bindings[0].outputs == {"duration_seconds": "duration"}
    assert [(item.metric_id, item.status, item.value) for item in result.observations] == [
        ("cost", "unavailable", None),
        ("duration", "measured", 2.5),
    ]
    assert result.observations[0].error == "Cost is unavailable in this runtime."


def test_validation_reports_every_required_plan_problem() -> None:
    adapter = RecordingAdapter(
        ProducerCapability(
            producer_id="known",
            kind="reported",
            version="1",
            inputs={"record": "structured_record"},
            scalar_outputs={
                "passed": ScalarOutputCapability(value_type="boolean", aggregations={"rate"}),
            },
        ),
        ProducerResult(),
    )
    engine = MeasurementEngine([adapter])
    snapshot = ExperimentSnapshot(
        experiment_id="experiment-1",
        inputs={
            "plain.text": MeasurementInput(value_type="text", value="yes"),
        },
    )
    plan = MeasurementPlan(
        metrics=[
            MetricDefinition(
                id="first",
                name="Passed",
                value_type="boolean",
                direction="neutral",
                aggregation="sum",
                primary=True,
            ),
            MetricDefinition(
                id="second",
                name=" passed ",
                value_type="boolean",
                direction="maximize",
                aggregation="rate",
            ),
        ],
        producers=[
            ProducerBinding(
                id="missing-input",
                producer_id="known",
                kind="reported",
                outputs={"passed": "first"},
            ),
            ProducerBinding(
                id="wrong-input",
                producer_id="known",
                kind="reported",
                outputs={"passed": "second"},
            ),
            ProducerBinding(
                id="unknown",
                producer_id="does-not-exist",
                kind="runtime",
                outputs={},
            ),
        ],
        inputs=[
            ProducerInputBinding(
                producer_binding_id="wrong-input",
                input_key="record",
                source_key="plain.text",
            ),
            ProducerInputBinding(
                producer_binding_id="wrong-input",
                input_key="unexpected",
                source_key="missing.source",
            ),
        ],
    )

    report = engine.validate(plan, snapshot)

    assert not report.valid
    assert {issue.code for issue in report.issues} >= {
        "unknown_producer",
        "missing_input",
        "incompatible_input_type",
        "duplicate_metric_name",
        "invalid_aggregation",
        "invalid_direction",
        "unknown_input",
    }


@pytest.mark.asyncio
async def test_every_declared_metric_gets_one_of_the_four_observation_statuses() -> None:
    adapter = RecordingAdapter(
        ProducerCapability(
            producer_id="status-producer",
            kind="reported",
            version="2",
            inputs={},
            scalar_outputs={
                key: ScalarOutputCapability(value_type="number", aggregations={"mean"})
                for key in ("measured", "unavailable", "failed", "not_applicable")
            },
        ),
        ProducerResult(
            observations={
                "measured": ProducedObservation.measured(3.5),
                "unavailable": ProducedObservation.unavailable("source omitted the field"),
                "failed": ProducedObservation.failed("calculation overflowed"),
                "not_applicable": ProducedObservation.not_applicable("wrong task type"),
            }
        ),
    )
    engine = MeasurementEngine([adapter])
    metric_ids = ("measured", "unavailable", "failed", "not_applicable")
    plan = MeasurementPlan(
        metrics=[
            MetricDefinition(
                id=metric_id,
                name=metric_id.replace("_", " ").title(),
                value_type="number",
                direction="maximize" if index == 0 else "neutral",
                aggregation="mean",
                primary=index == 0,
            )
            for index, metric_id in enumerate(metric_ids)
        ],
        producers=[
            ProducerBinding(
                id="statuses",
                producer_id="status-producer",
                kind="reported",
                outputs={metric_id: metric_id for metric_id in metric_ids},
            )
        ],
    )

    result = await engine.evaluate(
        plan,
        ExperimentSnapshot(experiment_id="experiment-1", inputs={}),
        CompletedReplicate(replicate_id="replicate-1", attempt_id="attempt-1"),
    )

    assert [observation.status for observation in result.observations] == [
        "measured",
        "unavailable",
        "failed",
        "not_applicable",
    ]
    assert [observation.error for observation in result.observations] == [
        None,
        "source omitted the field",
        "calculation overflowed",
        "wrong task type",
    ]


@pytest.mark.asyncio
async def test_engine_turns_adapter_and_invalid_scalar_failures_into_failed_observations() -> None:
    output = ScalarOutputCapability(value_type="number", aggregations={"mean"})
    malformed = RecordingAdapter(
        ProducerCapability(
            producer_id="malformed",
            kind="reported",
            version="1",
            inputs={},
            scalar_outputs={"score": output},
        ),
        ProducerResult(observations={"score": ProducedObservation.measured(float("nan"))}),
    )
    crashing = FailingAdapter(
        ProducerCapability(
            producer_id="crashing",
            kind="reported",
            version="1",
            inputs={},
            scalar_outputs={"quality": output},
        ),
        ProducerResult(),
    )
    plan = MeasurementPlan(
        metrics=[
            MetricDefinition(
                id="score",
                name="Score",
                value_type="number",
                direction="maximize",
                aggregation="mean",
                primary=True,
            ),
            MetricDefinition(
                id="quality",
                name="Quality",
                value_type="number",
                direction="maximize",
                aggregation="mean",
            ),
        ],
        producers=[
            ProducerBinding(
                id="malformed",
                producer_id="malformed",
                kind="reported",
                outputs={"score": "score"},
            ),
            ProducerBinding(
                id="crashing",
                producer_id="crashing",
                kind="reported",
                outputs={"quality": "quality"},
            ),
        ],
    )

    result = await MeasurementEngine([malformed, crashing]).evaluate(
        plan,
        ExperimentSnapshot(experiment_id="experiment-1", inputs={}),
        CompletedReplicate(replicate_id="replicate-1", attempt_id="attempt-1"),
    )

    assert [(item.status, item.value, item.error) for item in result.observations] == [
        ("failed", None, "Producer returned an invalid number for output 'score'."),
        ("failed", None, "producer crashed"),
    ]


def _independent_plan(binding_count: int) -> MeasurementPlan:
    return MeasurementPlan(
        metrics=[
            MetricDefinition(
                id=f"metric-{index}",
                name=f"Metric {index}",
                value_type="number",
                direction="maximize",
                aggregation="mean",
                primary=index == 0,
            )
            for index in range(binding_count)
        ],
        producers=[
            ProducerBinding(
                id=f"binding-{index}",
                producer_id="coordinated",
                kind="reported",
                outputs={"value": f"metric-{index}"},
            )
            for index in range(binding_count)
        ],
    )


class CoordinatedAdapter:
    producer_id = "coordinated"

    def __init__(self, evaluate: Any) -> None:
        self._evaluate = evaluate

    def capability_for(self, snapshot: ExperimentSnapshot) -> ProducerCapability:
        return ProducerCapability(
            producer_id=self.producer_id,
            kind="reported",
            version="1",
            inputs={},
            scalar_outputs={"value": ScalarOutputCapability(value_type="number", aggregations={"mean"})},
        )

    async def evaluate(
        self,
        binding: ProducerBinding,
        inputs: dict[str, MeasurementInput],
        attempt: CompletedReplicate,
    ) -> ProducerResult:
        return await self._evaluate(binding)


@pytest.mark.asyncio
async def test_engine_bounds_concurrent_producers_and_invokes_each_binding_once() -> None:
    active = 0
    peak = 0
    calls: list[str] = []
    release = asyncio.Event()

    async def evaluate(binding: ProducerBinding) -> ProducerResult:
        nonlocal active, peak
        calls.append(binding.id)
        active += 1
        peak = max(peak, active)
        try:
            await release.wait()
            return ProducerResult(observations={"value": ProducedObservation.measured(1)})
        finally:
            active -= 1

    engine = MeasurementEngine(
        [CoordinatedAdapter(evaluate)],
        max_concurrency=2,
        producer_timeout_seconds=1,
    )
    task = asyncio.create_task(
        engine.evaluate(
            _independent_plan(4),
            ExperimentSnapshot(experiment_id="experiment-1", inputs={}),
            CompletedReplicate(replicate_id="replicate-1", attempt_id="attempt-1"),
        )
    )
    while len(calls) < 2:
        await asyncio.sleep(0)

    assert peak == 2
    assert len(calls) == 2
    release.set()
    result = await task

    assert peak == 2
    assert sorted(calls) == [f"binding-{index}" for index in range(4)]
    assert [item.status for item in result.observations] == ["measured"] * 4


@pytest.mark.asyncio
async def test_engine_isolates_failure_and_timeout_without_retrying_producers() -> None:
    calls: dict[str, int] = {}

    async def evaluate(binding: ProducerBinding) -> ProducerResult:
        calls[binding.id] = calls.get(binding.id, 0) + 1
        if binding.id == "binding-0":
            return ProducerResult(observations={"value": ProducedObservation.measured(7)})
        if binding.id == "binding-1":
            raise RuntimeError("broken evaluator")
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    result = await MeasurementEngine(
        [CoordinatedAdapter(evaluate)],
        max_concurrency=3,
        producer_timeout_seconds=0.01,
    ).evaluate(
        _independent_plan(3),
        ExperimentSnapshot(experiment_id="experiment-1", inputs={}),
        CompletedReplicate(replicate_id="replicate-1", attempt_id="attempt-1"),
    )

    assert calls == {f"binding-{index}": 1 for index in range(3)}
    assert [(item.status, item.value, item.error) for item in result.observations] == [
        ("measured", 7, None),
        ("failed", None, "broken evaluator"),
        ("timed_out", None, "Producer exceeded the 0.01s administrative timeout."),
    ]


@pytest.mark.asyncio
async def test_engine_cancellation_retains_completed_observations_and_cancels_unfinished_work() -> None:
    cancellation = asyncio.Event()
    slow_started = asyncio.Event()
    calls: list[str] = []

    async def evaluate(binding: ProducerBinding) -> ProducerResult:
        calls.append(binding.id)
        if binding.id == "binding-0":
            return ProducerResult(observations={"value": ProducedObservation.measured(11)})
        slow_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    task = asyncio.create_task(
        MeasurementEngine(
            [CoordinatedAdapter(evaluate)],
            max_concurrency=2,
            producer_timeout_seconds=1,
        ).evaluate(
            _independent_plan(3),
            ExperimentSnapshot(experiment_id="experiment-1", inputs={}),
            CompletedReplicate(replicate_id="replicate-1", attempt_id="attempt-1"),
            cancellation_event=cancellation,
        )
    )
    await slow_started.wait()
    await asyncio.sleep(0)
    cancellation.set()
    result = await task

    assert calls.count("binding-0") == 1
    assert [(item.status, item.value) for item in result.observations] == [
        ("measured", 11),
        ("cancelled", None),
        ("cancelled", None),
    ]
    assert all(
        item.error == "Evaluation was cancelled before this producer completed." for item in result.observations[1:]
    )


@pytest.mark.asyncio
async def test_engine_does_not_invoke_producers_after_cancellation_was_already_requested() -> None:
    calls: list[str] = []
    cancellation = asyncio.Event()
    cancellation.set()

    async def evaluate(binding: ProducerBinding) -> ProducerResult:
        calls.append(binding.id)
        return ProducerResult(observations={"value": ProducedObservation.measured(1)})

    result = await MeasurementEngine([CoordinatedAdapter(evaluate)]).evaluate(
        _independent_plan(2),
        ExperimentSnapshot(experiment_id="experiment-1", inputs={}),
        CompletedReplicate(replicate_id="replicate-1", attempt_id="attempt-1"),
        cancellation_event=cancellation,
    )

    assert calls == []
    assert [item.status for item in result.observations] == ["cancelled", "cancelled"]


@pytest.mark.asyncio
async def test_engine_isolates_input_resolution_failure_to_its_binding() -> None:
    calls: list[str] = []

    async def evaluate(binding: ProducerBinding) -> ProducerResult:
        calls.append(binding.id)
        return ProducerResult(observations={"value": ProducedObservation.measured(5)})

    base = _independent_plan(2)
    plan = MeasurementPlan(
        metrics=base.metrics,
        producers=base.producers,
        inputs=[
            ProducerInputBinding(
                producer_binding_id="binding-0",
                input_key="missing",
                source_key="missing.source",
            )
        ],
    )
    result = await MeasurementEngine([CoordinatedAdapter(evaluate)]).evaluate(
        plan,
        ExperimentSnapshot(experiment_id="experiment-1", inputs={}),
        CompletedReplicate(replicate_id="replicate-1", attempt_id="attempt-1"),
        validate_plan=False,
    )

    assert calls == ["binding-1"]
    assert [(item.status, item.value) for item in result.observations] == [
        ("failed", None),
        ("measured", 5),
    ]


def test_engine_completes_unbound_declared_metric_with_explicit_absence_provenance() -> None:
    plan = MeasurementPlan(
        metrics=[
            MetricDefinition("missing", "Missing", "number", "maximize", "mean", True),
        ]
    )
    result = MeasurementEngine(()).complete_declared_metrics(
        plan,
        CompletedReplicate(replicate_id="replicate-1", attempt_id="attempt-1"),
        status="unavailable",
        error="No producer is bound.",
    )

    assert result.observations[0].status == "unavailable"
    assert result.observations[0].producer.kind == "unbound"
    assert result.observations[0].producer.producer_id == "unbound"
