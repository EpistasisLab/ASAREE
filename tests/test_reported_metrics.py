from types import SimpleNamespace
from uuid import uuid4

import pytest

from asaree.services.measurement_engine import parse_measurement_plan
from asaree.services.reported_metrics import collect_reported_metrics, validate_reported_measurement_plan


def _plan(producer_id: str, config: dict) -> object:
    return parse_measurement_plan(
        {
            "metrics": [
                {
                    "id": "quality",
                    "name": "Quality",
                }
            ],
            "producers": [
                {
                    "id": "quality-source",
                    "producer_id": producer_id,
                    "kind": "reported",
                    "outputs": {"value": "quality"},
                    "artifacts": [],
                    "config": config,
                }
            ],
            "inputs": [],
        }
    )


@pytest.mark.asyncio
async def test_mcp_report_uses_last_call_even_when_it_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    agent_run_id = uuid4()
    calls = [
        {"server": "scorer", "tool": "score", "result": "first", "success": True},
        {
            "server": "scorer",
            "tool": "score",
            "result": '{"error":"last"}',
            "success": False,
            "error_type": "tool_reported",
        },
    ]
    monkeypatch.setattr(
        "asaree.services.reported_metrics.get_run_steps",
        lambda _run_id: _async_value([SimpleNamespace(tool_call={"calls": calls})]),
    )
    monkeypatch.setattr(
        "asaree.services.reported_metrics.mcp_service.get_server",
        lambda _server_id: _async_value(SimpleNamespace(name="scorer")),
    )
    run = SimpleNamespace(id=uuid4(), replicate_result_id=None, node_runs={"agent": {"run_id": str(agent_run_id)}})

    result = await collect_reported_metrics(
        run,
        _plan(
            "asaree.mcp_tool",
            {"agent_node_id": "agent", "mcp_node_id": "mcp", "server_id": str(uuid4()), "tool_name": "score"},
        ),
        {"nodes": [], "edges": []},
    )

    assert result.observations[0].status == "measured"
    assert result.observations[0].value == '{"error":"last"}'
    assert result.observations[0].producer.evaluation["tool_call_success"] is False


@pytest.mark.asyncio
async def test_called_tool_returning_json_null_is_measured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "asaree.services.reported_metrics.get_run_steps",
        lambda _run_id: _async_value(
            [SimpleNamespace(tool_call={"server": "scorer", "tool": "score", "result": None})]
        ),
    )
    monkeypatch.setattr(
        "asaree.services.reported_metrics.mcp_service.get_server",
        lambda _server_id: _async_value(SimpleNamespace(name="scorer")),
    )
    run = SimpleNamespace(id=uuid4(), replicate_result_id=None, node_runs={"agent": {"run_id": str(uuid4())}})

    result = await collect_reported_metrics(
        run,
        _plan(
            "asaree.mcp_tool",
            {
                "agent_node_id": "agent",
                "mcp_node_id": "mcp",
                "server_id": str(uuid4()),
                "tool_name": "score",
            },
        ),
        {"nodes": [], "edges": []},
    )

    assert result.observations[0].status == "measured"
    assert result.observations[0].value is None


@pytest.mark.asyncio
async def test_missing_agent_tool_call_leaves_report_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "asaree.services.reported_metrics.get_run_steps",
        lambda _run_id: _async_value([]),
    )
    run = SimpleNamespace(id=uuid4(), replicate_result_id=None, node_runs={"agent": {"run_id": str(uuid4())}})

    result = await collect_reported_metrics(
        run,
        _plan(
            "asaree.python_script",
            {"agent_node_id": "agent", "script_node_id": "script"},
        ),
        {
            "nodes": [{"id": "script", "type": "script", "data": {"config": {"name": "score"}}}],
            "edges": [{"source": "script", "target": "agent", "targetHandle": "tool"}],
        },
    )

    assert result.observations[0].status == "unavailable"
    assert result.observations[0].value is None


@pytest.mark.asyncio
async def test_script_report_matches_the_configured_script_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "asaree.services.reported_metrics.get_run_steps",
        lambda _run_id: _async_value(
            [
                SimpleNamespace(
                    iteration=1,
                    sequence=1,
                    tool_call={
                        "calls": [
                            {
                                "server": "asaree-script",
                                "tool": "run_wired_script",
                                "arguments": {"script": "other"},
                                "result": "wrong script",
                            },
                            {
                                "server": "asaree-script",
                                "tool": "run_wired_script",
                                "arguments": {"script": "score"},
                                "result": "wanted result",
                            },
                        ]
                    },
                )
            ]
        ),
    )
    run = SimpleNamespace(id=uuid4(), replicate_result_id=None, node_runs={"agent": {"run_id": str(uuid4())}})
    graph = {
        "nodes": [
            {"id": "score", "type": "script", "data": {"config": {"name": "score"}}},
            {"id": "other", "type": "script", "data": {"config": {"name": "other"}}},
        ],
        "edges": [
            {"source": "score", "target": "agent", "targetHandle": "tool"},
            {"source": "other", "target": "agent", "targetHandle": "tool"},
        ],
    }

    result = await collect_reported_metrics(
        run,
        _plan("asaree.python_script", {"agent_node_id": "agent", "script_node_id": "score"}),
        graph,
    )

    assert result.observations[0].value == "wanted result"


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["0.87", ""])
async def test_agent_output_report_captures_completed_final_output(output: str) -> None:
    run = SimpleNamespace(
        id=uuid4(),
        replicate_result_id=None,
        node_runs={"agent": {"status": "completed", "output_text": output}},
    )

    result = await collect_reported_metrics(
        run,
        _plan("asaree.agent_output", {"agent_node_id": "agent"}),
        {"nodes": [], "edges": []},
    )

    assert result.observations[0].status == "measured"
    assert result.observations[0].value == output
    assert result.observations[0].producer.evaluation["source"] == "agent_final_output"


@pytest.mark.asyncio
async def test_agent_output_projection_reads_the_structured_parser_payload() -> None:
    plan = parse_measurement_plan(
        {
            "metrics": [
                {
                    "id": "feature-count",
                    "name": "n_engineered_features",
                },
                {"id": "summary", "name": "summary"},
                {"id": "missing", "name": "missing"},
            ],
            "producers": [
                {
                    "id": "fte-output",
                    "producer_id": "asaree.agent_output",
                    "kind": "reported",
                    "outputs": {"feature_count": "feature-count", "summary": "summary", "missing": "missing"},
                    "config": {
                        "agent_node_id": "agent",
                        "projections": {
                            "feature_count": {"path": "engineering_recipe", "transform": "length"},
                            "missing": {"path": "absent"},
                        },
                    },
                }
            ],
            "inputs": [],
        }
    )
    run = SimpleNamespace(
        id=uuid4(),
        replicate_result_id=None,
        node_runs={
            "agent": {
                "status": "completed",
                "output_text": "summary",
                "payload": {"engineering_recipe": [{"name": "a"}, {"name": "b"}]},
            }
        },
    )

    result = await collect_reported_metrics(run, plan, {"nodes": [], "edges": []})

    by_id = {observation.metric_id: observation for observation in result.observations}
    assert by_id["feature-count"].status == "measured" and by_id["feature-count"].value == 2
    assert by_id["summary"].value == "summary", "no projection records the whole final output"
    assert by_id["missing"].status == "unavailable"


@pytest.mark.asyncio
async def test_agent_output_projection_falls_back_to_json_in_the_final_answer() -> None:
    plan = parse_measurement_plan(
        {
            "metrics": [{"id": "threshold", "name": "threshold"}],
            "producers": [
                {
                    "id": "dc-output",
                    "producer_id": "asaree.agent_output",
                    "kind": "reported",
                    "outputs": {"threshold": "threshold"},
                    "config": {"agent_node_id": "agent", "projections": {"threshold": {"path": "a.b"}}},
                }
            ],
            "inputs": [],
        }
    )
    run = SimpleNamespace(
        id=uuid4(),
        replicate_result_id=None,
        node_runs={"agent": {"status": "completed", "output_text": 'Done.\n```json\n{"a": {"b": 0.3}}\n```'}},
    )

    result = await collect_reported_metrics(run, plan, {"nodes": [], "edges": []})

    assert result.observations[0].value == 0.3


@pytest.mark.asyncio
async def test_mcp_metric_captures_the_complete_tool_result_without_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = parse_measurement_plan(
        {
            "metrics": [
                {
                    "id": "accuracy",
                    "name": "accuracy_at_0_5",
                    "value_type": "number",
                    "direction": "maximize",
                    "aggregation": "mean",
                }
            ],
            "producers": [
                {
                    "id": "score",
                    "producer_id": "asaree.mcp_tool",
                    "kind": "reported",
                    "outputs": {"accuracy": "accuracy"},
                    "config": {
                        "agent_node_id": "agent",
                        "mcp_node_id": "mcp",
                        "server_id": str(uuid4()),
                        "tool_name": "score",
                        "projections": {"accuracy": {"path": "/test_metrics/metrics_at_0.5/accuracy"}},
                    },
                }
            ],
            "inputs": [],
        }
    )
    monkeypatch.setattr(
        "asaree.services.reported_metrics.get_run_steps",
        lambda _run_id: _async_value(
            [
                SimpleNamespace(
                    tool_call={
                        "server": "scorer",
                        "tool": "score",
                        "result": '{"test_metrics":{"metrics_at_0.5":{"accuracy":0.81}}}',
                    }
                )
            ]
        ),
    )
    monkeypatch.setattr(
        "asaree.services.reported_metrics.mcp_service.get_server",
        lambda _server_id: _async_value(SimpleNamespace(name="scorer")),
    )
    run = SimpleNamespace(id=uuid4(), replicate_result_id=None, node_runs={"agent": {"run_id": str(uuid4())}})

    result = await collect_reported_metrics(run, plan, {"nodes": [], "edges": []})

    assert result.observations[0].status == "measured"
    assert result.observations[0].value == '{"test_metrics":{"metrics_at_0.5":{"accuracy":0.81}}}'
    assert result.observations[0].value_type is None


@pytest.mark.asyncio
async def test_absent_provider_output_is_unavailable_despite_empty_handoff() -> None:
    run = SimpleNamespace(
        id=uuid4(), replicate_result_id=None,
        node_runs={"agent": {"status": "completed", "output_text": "", "final_output_available": False}},
    )
    result = await collect_reported_metrics(
        run, _plan("asaree.agent_output", {"agent_node_id": "agent"}), {"nodes": [], "edges": []},
    )
    assert result.observations[0].status == "unavailable"


@pytest.mark.asyncio
async def test_agent_output_report_is_unavailable_when_agent_did_not_complete() -> None:
    run = SimpleNamespace(
        id=uuid4(),
        replicate_result_id=None,
        node_runs={"agent": {"status": "failed", "output_text": None}},
    )

    result = await collect_reported_metrics(
        run,
        _plan("asaree.agent_output", {"agent_node_id": "agent"}),
        {"nodes": [], "edges": []},
    )

    assert result.observations[0].status == "unavailable"
    assert result.observations[0].value is None


@pytest.mark.asyncio
async def test_sub_agent_metric_keeps_latest_success_after_a_failed_retry() -> None:
    run = SimpleNamespace(
        id=uuid4(),
        replicate_result_id=None,
        node_runs={
            "worker": {
                "status": "failed",
                "output_text": None,
                "last_successful_output_text": '{"score":0.91}',
                "last_successful_run_id": str(uuid4()),
            }
        },
    )

    result = await collect_reported_metrics(
        run,
        _plan("asaree.agent_output", {"agent_node_id": "worker"}),
        {"nodes": [{"id": "worker", "type": "sub_agent", "data": {}}], "edges": []},
    )

    assert result.observations[0].status == "measured"
    assert result.observations[0].value == '{"score":0.91}'


@pytest.mark.asyncio
async def test_agent_output_plan_requires_an_active_agent() -> None:
    plan = _plan("asaree.agent_output", {"agent_node_id": "agent"})

    valid = await validate_reported_measurement_plan(
        plan,
        {"nodes": [{"id": "agent", "type": "agent", "data": {}}]},
    )
    disabled = await validate_reported_measurement_plan(
        plan,
        {"nodes": [{"id": "agent", "type": "agent", "data": {"active": False}}]},
    )

    assert valid.valid
    assert [issue.code for issue in disabled.issues] == ["agent_output_agent_disabled"]

    sub_agent = await validate_reported_measurement_plan(
        plan,
        {"nodes": [{"id": "agent", "type": "sub_agent", "data": {}}]},
    )
    assert sub_agent.valid


@pytest.mark.asyncio
async def test_reported_metric_rejects_value_semantics() -> None:
    plan = parse_measurement_plan(
        {
            "metrics": [
                {
                    "id": "quality",
                    "name": "Quality",
                    "value_type": "number",
                    "direction": "maximize",
                    "aggregation": "mean",
                }
            ],
            "producers": [
                {
                    "id": "quality-source",
                    "producer_id": "asaree.agent_output",
                    "kind": "reported",
                    "outputs": {"value": "quality"},
                    "config": {"agent_node_id": "agent"},
                }
            ],
            "inputs": [],
        }
    )

    report = await validate_reported_measurement_plan(
        plan,
        {"nodes": [{"id": "agent", "type": "agent", "data": {}}]},
    )

    assert [issue.code for issue in report.issues] == ["reported_metric_semantics_not_supported"]


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_malformed_projection_is_a_validation_issue() -> None:
    plan = parse_measurement_plan(
        {
            "metrics": [{"id": "quality", "name": "Quality"}],
            "producers": [
                {
                    "id": "agent-output",
                    "producer_id": "asaree.agent_output",
                    "kind": "reported",
                    "outputs": {"value": "quality"},
                    "config": {"agent_node_id": "agent", "projections": {"value": {"path": "a", "transform": "sum"}}},
                }
            ],
            "inputs": [],
        }
    )

    report = await validate_reported_measurement_plan(
        plan, {"nodes": [{"id": "agent", "type": "agent", "data": {}}], "edges": []}
    )

    assert [issue.code for issue in report.issues] == ["reported_projection_invalid"]
