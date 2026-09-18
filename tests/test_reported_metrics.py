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
                    "value_type": "opaque",
                    "direction": "neutral",
                    "aggregation": "none",
                    "primary": False,
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


async def _async_value(value):
    return value
