from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from motoro.schemas.output import OutputEnvelope

from asaree.services.measurement_engine import CompletedReplicate, MeasurementInput, ProducerBinding
from asaree.services.runtime_metrics import (
    NodeRuntimeMetricProducer,
    RuntimeMetricProducer,
    authored_prompt_sha256,
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
async def test_runtime_producer_splits_rejections_by_scope_and_counts_null_verdicts_as_rejections() -> None:
    result = await _evaluate(
        _facts(
            runs=[{"run_id": "worker-1", "steps": [], "max_iterations_hit": True}, {"run_id": "worker-2", "steps": []}],
            critic_reviews=[
                {"run_id": "critic-1", "approved": False, "rejection_scope": "partial"},
                {"run_id": "critic-2", "approved": None, "rejection_scope": None},
                {"run_id": "critic-3", "approved": False, "rejection_scope": "full"},
                {"run_id": "critic-4", "approved": True, "rejection_scope": None},
            ],
            critic_gates=[
                {"revisions_used": 2, "approved": True, "forced": False},
                {"revisions_used": 1, "approved": None, "forced": True},
            ],
        )
    )

    values = {key: observation.value for key, observation in result.observations.items()}
    assert values["critic_invocations"] == 4
    assert values["critic_rejections"] == 3
    assert values["critic_approvals"] == 1
    assert values["critic_rejections_partial"] == 1
    assert values["critic_rejections_full"] == 2
    assert values["revision_rounds"] == 3
    assert values["capped_agent_runs"] == 1


@pytest.mark.asyncio
async def test_runtime_producer_reports_zero_critic_activity_when_no_critic_ran() -> None:
    result = await _evaluate(_facts(critic_gates=[{"revisions_used": 0, "approved": None, "forced": False}]))

    for key in ("critic_invocations", "critic_rejections_partial", "critic_rejections_full", "revision_rounds"):
        assert result.observations[key].value == 0


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
    attributed[1].output = OutputEnvelope(payload={"approved": False, "rejection_scope": "partial"}).to_json()
    attributed[2].pattern_overrides = {"reason_act_state": {"max_iterations_hit": True}}
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
    assert facts["critic_reviews"] == [{"approved": False, "rejection_scope": "partial", "run_id": str(run_ids[1])}]
    assert [run["max_iterations_hit"] for run in facts["runs"]] == [False, False, True, False]
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


def _prompt_graph(goal: str = "Clean the data.") -> dict[str, object]:
    return {
        "nodes": [
            {
                "id": "a",
                "type": "agent",
                "position": {"x": 0, "y": 0},
                "data": {"config": {"goal": goal, "model": "x"}},
            },
            {"id": "g", "type": "critic_gate", "data": {"config": {"system_prompt": "Checklist: ..."}}},
            {"id": "d", "type": "dataset", "data": {"config": {"goal": "not a prompt"}}},
        ],
        "edges": [],
    }


def test_prompt_hash_covers_prompt_text_only() -> None:
    base = authored_prompt_sha256(_prompt_graph())
    moved = _prompt_graph()
    moved["nodes"] = list(reversed(moved["nodes"]))  # type: ignore[call-overload]
    moved["nodes"][-1]["position"] = {"x": 500, "y": 9}  # type: ignore[index]
    moved["nodes"][-1]["data"]["config"]["model"] = "y"  # type: ignore[index]

    assert base is not None and len(base) == 64
    assert authored_prompt_sha256(moved) == base
    assert authored_prompt_sha256(_prompt_graph("Clean the data!")) != base
    assert authored_prompt_sha256({"nodes": [], "edges": []}) is None


@pytest.mark.asyncio
async def test_runtime_producer_reports_the_prompt_hash_from_facts() -> None:
    measured = await _evaluate(_facts(prompt_sha256="ab" * 32))
    missing = await _evaluate(_facts())

    assert measured.observations["prompt_sha256"].value == "ab" * 32
    assert missing.observations["prompt_sha256"].status == "unavailable"


async def _evaluate_nodes(facts: dict[str, object], node_ids: list[str]):
    adapter = NodeRuntimeMetricProducer()
    return await adapter.evaluate(
        ProducerBinding(
            id="runtime-stage",
            producer_id=adapter.producer_id,
            kind="runtime",
            outputs={key: key for key in adapter.output_keys},
            config={"node_ids": node_ids},
        ),
        {"facts": MeasurementInput(value_type="runtime_facts", value=facts)},
        CompletedReplicate(replicate_id="replicate-1", attempt_id="attempt-1"),
    )


def _stage_run(run_id: str, node_id: str | None, tokens: int, iterations: int) -> dict[str, object]:
    return {
        "run_id": run_id,
        "node_id": node_id,
        "usage": {"total_tokens": tokens},
        "steps": [{"iteration": index, "tool_call": None, "llm_call": None} for index in range(iterations)],
        "max_iterations_hit": False,
    }


@pytest.mark.asyncio
async def test_node_runtime_producer_sums_only_the_chosen_nodes_runs() -> None:
    facts = _facts(
        runs=[
            _stage_run("dc-1", "agent-dc", 100, 3),
            _stage_run("dc-revision", "agent-dc", 50, 2),
            _stage_run("fte-1", "agent-fte", 400, 5),
            _stage_run("critic-dc", "gate-dc", 7, 1),
            _stage_run("critic-fte", "gate-fte", 9, 1),
        ]
    )

    dc = await _evaluate_nodes(facts, ["agent-dc"])
    critics = await _evaluate_nodes(facts, ["gate-dc", "gate-fte"])
    absent = await _evaluate_nodes(facts, ["agent-mlm"])

    assert dc.observations["total_tokens"].value == 150
    assert dc.observations["agent_loop_iterations"].value == 5
    assert dc.observations["agent_runs"].value == 2
    assert critics.observations["total_tokens"].value == 16
    assert absent.observations["agent_runs"].value == 0
    assert absent.observations["total_tokens"].status == "unavailable"


@pytest.mark.asyncio
async def test_node_runtime_producer_is_unavailable_for_facts_without_node_attribution() -> None:
    legacy = _stage_run("dc-1", None, 100, 3)
    del legacy["node_id"]

    result = await _evaluate_nodes(_facts(runs=[legacy]), ["agent-dc"])

    assert {observation.status for observation in result.observations.values()} == {"unavailable"}


def test_node_runtime_validation_requires_nodes_on_the_canvas() -> None:
    def plan(node_ids: list[str]) -> dict[str, object]:
        return {
            "metrics": [{"id": "tokens-dc", "name": "tokens_dc", "value_type": "number", "aggregation": "sum"}],
            "producers": [
                {
                    "id": "runtime-dc",
                    "producer_id": "asaree.node_runtime",
                    "kind": "runtime",
                    "outputs": {"total_tokens": "tokens-dc"},
                    "config": {"node_ids": node_ids},
                }
            ],
            "inputs": [{"producer_binding_id": "runtime-dc", "input_key": "facts", "source_key": "attempt.runtime"}],
        }

    graph = {"nodes": [{"id": "agent-dc", "type": "agent"}], "edges": []}

    assert not validate_runtime_measurement_plan(plan(["agent-dc"]), graph=graph).issues
    assert [issue.code for issue in validate_runtime_measurement_plan(plan(["gone"]), graph=graph).issues] == [
        "node_runtime_node_unavailable"
    ]
    assert [issue.code for issue in validate_runtime_measurement_plan(plan([]), graph=graph).issues] == [
        "node_runtime_nodes_missing"
    ]


def test_node_runtime_metric_saved_as_id_and_name_still_validates() -> None:
    report = validate_runtime_measurement_plan(
        {
            "metrics": [
                {"id": "tokens-dc", "name": "tokens_dc"},
                {"id": "errors-dc", "name": "tool_errors_dc"},
            ],
            "producers": [
                {
                    "id": "node-runtime-tokens-dc",
                    "producer_id": "asaree.node_runtime",
                    "kind": "runtime",
                    "outputs": {"total_tokens": "tokens-dc"},
                    "config": {"node_ids": ["agent-dc"]},
                },
                {
                    "id": "node-runtime-errors-dc",
                    "producer_id": "asaree.node_runtime",
                    "kind": "runtime",
                    "outputs": {"tool_error_rate": "errors-dc"},
                    "config": {"node_ids": ["agent-dc"]},
                },
            ],
            "inputs": [
                {"producer_binding_id": binding, "input_key": "facts", "source_key": "attempt.runtime"}
                for binding in ("node-runtime-tokens-dc", "node-runtime-errors-dc")
            ],
        }
    )

    assert not report.issues
