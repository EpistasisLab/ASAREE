from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from motoro.schemas.output import OutputEnvelope

from asaree.services.measurement_engine import CompletedReplicate, MeasurementInput, ProducerBinding
from asaree.services.runtime_metrics import (
    RuntimeMetricProducer,
    collect_runtime_facts,
    validate_runtime_measurement_plan,
)


def _facts(**overrides: object) -> dict[str, object]:
    started = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    facts: dict[str, object] = {
        "started_at": started.isoformat(),
        "completed_at": (started + timedelta(seconds=12.5)).isoformat(),
        "runs": [],
        "critic_gates": [],
    }
    facts.update(overrides)
    return facts


async def _evaluate(facts: dict[str, object]):
    adapter = RuntimeMetricProducer()
    outputs = {key: key for key in adapter.output_keys}
    return await adapter.evaluate(
        ProducerBinding(
            id="runtime",
            producer_id=adapter.producer_id,
            kind="runtime",
            outputs=outputs,
        ),
        {"facts": MeasurementInput(value_type="runtime_facts", value=facts)},
        CompletedReplicate(replicate_id="replicate-1", attempt_id="attempt-1"),
    )


def test_runtime_validation_marks_a_removed_output_as_output_specific_capability_loss() -> None:
    report = validate_runtime_measurement_plan(
        {
            "metrics": [
                {
                    "id": "removed",
                    "name": "Removed built-in",
                    "value_type": "number",
                    "direction": "minimize",
                    "aggregation": "sum",
                }
            ],
            "producers": [
                {
                    "id": "runtime",
                    "producer_id": "asaree.runtime",
                    "kind": "runtime",
                    "outputs": {"removed_output": "removed"},
                }
            ],
            "inputs": [
                {
                    "producer_binding_id": "runtime",
                    "input_key": "facts",
                    "source_key": "attempt.runtime",
                }
            ],
        },
        preserved_binding_ids={"runtime"},
    )

    assert len(report.issues) == 1
    assert report.issues[0].code == "runtime_output_unavailable"
    assert report.issues[0].path == "producers[0].outputs.removed_output"
    assert report.issues[0].severity == "needs_attention"


def test_runtime_validation_keeps_a_new_unknown_output_blocking() -> None:
    report = validate_runtime_measurement_plan(
        {
            "metrics": [
                {
                    "id": "typo",
                    "name": "Typo",
                    "value_type": "number",
                    "direction": "minimize",
                    "aggregation": "sum",
                }
            ],
            "producers": [
                {
                    "id": "new-runtime",
                    "producer_id": "asaree.runtime",
                    "kind": "runtime",
                    "outputs": {"cost_usd_typo": "typo"},
                }
            ],
            "inputs": [
                {
                    "producer_binding_id": "new-runtime",
                    "input_key": "facts",
                    "source_key": "attempt.runtime",
                }
            ],
        }
    )

    assert report.issues[0].code == "unknown_output"
    assert report.issues[0].severity == "blocking"


def test_runtime_validation_restores_full_plan_producer_indices() -> None:
    document = {
        "metrics": [
            {
                "id": "custom",
                "name": "Custom",
                "value_type": "number",
                "direction": "maximize",
                "aggregation": "mean",
            },
            {
                "id": "removed",
                "name": "Removed built-in",
                "value_type": "number",
                "direction": "minimize",
                "aggregation": "sum",
            },
        ],
        "producers": [
            {
                "id": "python-first",
                "producer_id": "asaree.python_script",
                "kind": "reported",
                "outputs": {"value": "custom"},
            },
            {
                "id": "runtime-second",
                "producer_id": "asaree.runtime",
                "kind": "runtime",
                "outputs": {"removed_output": "removed"},
            },
        ],
        "inputs": [
            {
                "producer_binding_id": "runtime-second",
                "input_key": "facts",
                "source_key": "attempt.runtime",
            }
        ],
    }

    report = validate_runtime_measurement_plan(document, preserved_binding_ids={"runtime-second"})

    assert len(report.issues) == 1
    assert report.issues[0].path == "producers[1].outputs.removed_output"
    assert report.issues[0].severity == "needs_attention"


@pytest.mark.asyncio
async def test_runtime_producer_sums_every_reported_cost_and_usage_field() -> None:
    result = await _evaluate(
        _facts(
            runs=[
                {
                    "run_id": "worker-rejected",
                    "cost_usd": 0.25,
                    "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
                    "steps": [],
                },
                {
                    "run_id": "critic",
                    "cost_usd": None,
                    "usage": {"input_tokens": 6, "output_tokens": 2, "total_tokens": 8},
                    "steps": [],
                },
                {
                    "run_id": "peer",
                    "cost_usd": 0.5,
                    "usage": {},
                    "steps": [],
                },
            ]
        )
    )

    assert result.observations["cost_usd"].value == pytest.approx(0.75)
    assert result.observations["input_tokens"].value == 16
    assert result.observations["output_tokens"].value == 6
    assert result.observations["total_tokens"].value == 22


@pytest.mark.asyncio
async def test_runtime_producer_keeps_unreported_usage_unavailable_and_offers_distinct_usage() -> None:
    result = await _evaluate(
        _facts(
            runs=[
                {
                    "run_id": "one",
                    "cost_usd": None,
                    "usage": {"cache_read_tokens": 7, "thinking_tokens": 3},
                    "steps": [],
                }
            ]
        )
    )

    assert result.observations["cost_usd"].status == "unavailable"
    assert result.observations["input_tokens"].status == "unavailable"
    assert result.observations["output_tokens"].status == "unavailable"
    assert result.observations["total_tokens"].status == "unavailable"
    assert result.observations["cache_read_tokens"].value == 7
    assert result.observations["cache_creation_tokens"].status == "unavailable"
    assert result.observations["thinking_tokens"].value == 3


@pytest.mark.asyncio
async def test_runtime_producer_uses_protocol_wall_clock_and_counts_tool_attempts_and_iterations() -> None:
    result = await _evaluate(
        _facts(
            runs=[
                {
                    "run_id": "worker",
                    "cost_usd": 0.1,
                    "usage": {},
                    "steps": [
                        {"iteration": 0, "tool_call": {"tool": "first", "success": True}},
                        {
                            "iteration": 0,
                            "tool_call": {
                                "calls": [
                                    {"tool": "second", "success": False},
                                    {"tool": "third", "success": True},
                                ]
                            },
                        },
                        {"iteration": 1, "tool_call": None},
                    ],
                },
                {
                    "run_id": "peer",
                    "cost_usd": 0.2,
                    "usage": {},
                    "steps": [{"iteration": 0, "tool_call": None}],
                },
            ]
        )
    )

    assert result.observations["duration_seconds"].value == 12.5
    assert result.observations["tool_calls"].value == 3
    assert result.observations["tool_error_rate"].value == pytest.approx(1 / 3)
    assert result.observations["agent_loop_iterations"].value == 3


@pytest.mark.asyncio
async def test_runtime_producer_distinguishes_no_tool_calls_from_zero_percent_errors() -> None:
    no_calls = await _evaluate(_facts(runs=[]))
    successful_call = await _evaluate(
        _facts(
            runs=[
                {
                    "run_id": "worker",
                    "cost_usd": None,
                    "usage": {},
                    "steps": [{"iteration": 0, "tool_call": {"tool": "ok", "success": True}}],
                }
            ]
        )
    )

    assert no_calls.observations["tool_calls"].value == 0
    assert no_calls.observations["tool_error_rate"].status == "unavailable"
    assert successful_call.observations["tool_error_rate"].value == 0


@pytest.mark.asyncio
async def test_runtime_producer_counts_rejections_and_only_actual_critic_approvals() -> None:
    result = await _evaluate(
        _facts(
            critic_reviews=[
                {"run_id": "critic-1", "approved": False},
                {"run_id": "critic-2", "approved": False},
                {"run_id": "critic-3", "approved": True},
            ],
            # A forced final continuation has no critic review of its own. The
            # compatibility gate record must not add an approval or recount
            # the prior rejection when explicit review facts are present.
            critic_gates=[
                {"revisions_used": 2, "approved": None, "forced": True},
            ],
        )
    )

    assert result.observations["critic_rejections"].value == 2
    assert result.observations["critic_approvals"].value == 1


@pytest.mark.asyncio
async def test_runtime_producer_marks_duration_unavailable_without_both_boundaries() -> None:
    result = await _evaluate(_facts(completed_at=None))

    assert result.observations["duration_seconds"].status == "unavailable"


@pytest.mark.asyncio
async def test_runtime_producer_records_collection_failures_as_failed_observations() -> None:
    result = await _evaluate({"collection_error": "core read failed"})

    assert result.observations["cost_usd"].status == "failed"
    assert result.observations["cost_usd"].error == "core read failed"
    assert result.observations["tool_calls"].status == "failed"


@pytest.mark.asyncio
async def test_runtime_fact_collection_uses_protocol_attribution_not_final_node_pointers(monkeypatch) -> None:
    protocol_run_id = uuid4()
    owner_id = uuid4()
    run_ids = [uuid4(), uuid4(), uuid4(), uuid4()]
    attributed = [
        SimpleNamespace(
            id=run_id,
            cost_estimate=index / 10,
            token_usage={"prompt_tokens": index, "completion_tokens": index + 1},
        )
        for index, run_id in enumerate(run_ids, start=1)
    ]
    for run in attributed:
        run.run_metadata = {}
        run.output = None
    attributed[1].run_metadata = {"runtime_role": "critic"}
    attributed[1].output = OutputEnvelope(payload={"approved": False}).to_json()
    list_call: dict[str, object] = {}

    async def fake_list_runs(**kwargs):
        list_call.update(kwargs)
        return attributed

    async def fake_get_run_steps(run_id):
        return [
            SimpleNamespace(
                iteration=0,
                tool_call={"tool": str(run_id), "success": True},
                llm_call={"cache_read_input_tokens": 5, "cache_creation_input_tokens": 0},
            )
        ]

    monkeypatch.setattr("asaree.services.runtime_metrics.list_runs", fake_list_runs)
    monkeypatch.setattr("asaree.services.runtime_metrics.get_run_steps", fake_get_run_steps)
    protocol_run = SimpleNamespace(
        id=protocol_run_id,
        owner_id=owner_id,
        started_at=datetime(2026, 9, 15, 12, tzinfo=UTC),
        completed_at=datetime(2026, 9, 15, 12, 1, tzinfo=UTC),
        # Only the final worker is pointed to here. The attribution query must
        # still include its rejected revision, critic, and peer run.
        node_runs={
            "worker": {"run_id": str(run_ids[-1])},
            "gate": {"approved": None, "forced": True, "revisions_used": 2},
        },
    )

    facts = await collect_runtime_facts(protocol_run)

    assert list_call["owner_id"] == owner_id
    assert list_call["metadata"] == {"protocol_run_id": str(protocol_run_id)}
    assert {run["run_id"] for run in facts["runs"]} == {str(run_id) for run_id in run_ids}
    assert facts["critic_gates"] == [{"approved": None, "forced": True, "revisions_used": 2}]
    assert facts["critic_reviews"] == [{"approved": False, "run_id": str(run_ids[1])}]
    assert facts["runs"][0]["usage"] == {
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
        "cache_read_tokens": 5,
    }


@pytest.mark.asyncio
async def test_runtime_fact_collection_keeps_missing_cost_and_partial_usage_unavailable(monkeypatch) -> None:
    run_id = uuid4()
    defaulted_run_id = uuid4()

    async def fake_list_runs(**kwargs):
        del kwargs
        return [
            SimpleNamespace(id=run_id, cost_estimate=None, token_usage={"prompt_tokens": 8}),
            SimpleNamespace(
                id=defaulted_run_id,
                cost_estimate=0.0,
                token_usage={"prompt_tokens": 0, "completion_tokens": 0},
            ),
        ]

    async def fake_get_run_steps(requested_run_id):
        cache_tokens = 3 if requested_run_id == run_id else 0
        return [SimpleNamespace(iteration=0, tool_call=None, llm_call={"cache_read_input_tokens": cache_tokens})]

    monkeypatch.setattr("asaree.services.runtime_metrics.list_runs", fake_list_runs)
    monkeypatch.setattr("asaree.services.runtime_metrics.get_run_steps", fake_get_run_steps)
    protocol_run = SimpleNamespace(
        id=uuid4(),
        owner_id=uuid4(),
        started_at=datetime(2026, 9, 15, 12, tzinfo=UTC),
        completed_at=datetime(2026, 9, 15, 12, 1, tzinfo=UTC),
        node_runs={},
    )

    facts = await collect_runtime_facts(protocol_run)

    assert facts["runs"][0]["cost_usd"] is None
    assert facts["runs"][0]["usage"] == {"input_tokens": 8, "cache_read_tokens": 3}
    assert facts["runs"][1]["cost_usd"] is None
    assert facts["runs"][1]["usage"] == {}
