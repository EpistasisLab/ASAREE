"""Cell settings stay isolated at ASAREE's Motoro runner boundary."""

from __future__ import annotations

import asyncio
import copy
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from motoro import runner
from motoro.config import CoreSettings
from motoro.models.agent import Agent
from motoro.models.run import RunStatus
from motoro.schemas.agent import ModelConfig
from motoro.schemas.output import OutputEnvelope

from asaree.services import protocol_execution as pe
from asaree.services import run_tools
from asaree.worker import tasks


@pytest.fixture
def storage(monkeypatch):
    """Run real Motoro snapshot validation with an in-memory persistence stub."""
    agents = {}
    runs = {}
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    settings = CoreSettings(_env_file=None)
    monkeypatch.setattr("motoro.config.get_settings", lambda: settings)
    monkeypatch.setattr("asaree.config.get_settings", lambda: settings)
    db = MagicMock()

    async def execute(statement):
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params
        if entity is Agent:
            row = agents.get(params.get("name_1")) if "name_1" in params else next(iter(agents.values()), None)
        else:
            row = runs.get(params["id_1"])
        result = MagicMock()
        result.scalar_one.return_value = row
        result.scalar_one_or_none.return_value = row
        return result

    def add(row):
        row.id = uuid.uuid4()
        if isinstance(row, Agent):
            agents[row.name] = row
        else:
            runs[row.id] = row

    db.execute = AsyncMock(side_effect=execute)
    db.add.side_effect = add
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    @asynccontextmanager
    async def session(_reason):
        yield db

    async def model(data, **kwargs):
        return ModelConfig(**data)

    monkeypatch.setattr(runner, "_session", session)
    monkeypatch.setattr(pe, "build_model_config", model)
    monkeypatch.setattr(pe, "get_settings", lambda: SimpleNamespace(worker_job_timeout_seconds=300))
    return agents, runs


def _graph(kind="agent"):
    node = {
        "id": "a",
        "type": kind,
        "data": {
            "label": "Analyst",
            "config": {
                "goal": "Baseline goal",
                "system_prompt": "Baseline system prompt",
                "budget_limit_usd": 5,
                "max_run_duration_seconds": 90,
            },
            "factor_bindings": {
                "config.system_prompt": "System prompt",
                "config.goal": "Goal",
                "config.max_run_duration_seconds": "Timeout",
                "config.budget_limit_usd": "Budget",
            },
        },
    }
    model = {"id": "m", "type": "model_openai", "data": {"config": {"model": "gpt-4o"}}}
    pattern = {
        "id": "p",
        "type": "pattern_reason_act",
        "data": {"config": {"max_iterations": 9}, "factor_bindings": {"config.max_iterations": "Iterations"}},
    }
    tool = {
        "id": "t",
        "type": "mcp_tool",
        "data": {
            "config": {"server_name": "server", "tool_names": ["baseline"]},
            "factor_bindings": {"config.tool_names": "Tools"},
        },
    }
    return {
        "nodes": [node, model, pattern, tool],
        "edges": [
            {"id": "m-a", "source": "m", "target": "a", "targetHandle": "model"},
            {"id": "p-a", "source": "p", "target": "a", "targetHandle": "architectural_pattern"},
            {"id": "t-a", "source": "t", "target": "a", "targetHandle": "tool"},
        ],
    }


@pytest.mark.parametrize("kind", ["agent", "critic_gate"])
@pytest.mark.parametrize("row_mode", [False, True])
async def test_cell_configs_are_pinned_before_either_run_executes(storage, monkeypatch, kind, row_mode):
    agents, runs = storage
    baseline = _graph(kind)
    monkeypatch.setattr(pe, "_baseline_graph_for_run", AsyncMock(return_value=baseline))
    catalog = [{"name": f"server.{name}", "tool_name": name, "server": "server"} for name in ("baseline", "ping")]
    monkeypatch.setattr(run_tools, "get_registry", lambda: SimpleNamespace(get_all_tools=lambda **kwargs: catalog))
    entered = asyncio.Event()
    executions = {}

    async def execute(*, run_id, available_tools, timeout, **kwargs):
        executions[run_id] = timeout
        if len(executions) == 2:
            entered.set()
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert len(runs) == 2
        run = runs[run_id]
        expected = [
            tool for tool in catalog if tool["name"] in run.run_metadata[run_tools.PINNED_TOOL_CONFIG]["tool_names"]
        ]
        assert available_tools == expected
        run.status = RunStatus.COMPLETED
        run.output = OutputEnvelope(result="Done", payload={"approved": True}).to_json()

    monkeypatch.setattr(pe, "_execute_run_cancellable", execute)
    protocol_id, owner_id = uuid.uuid4(), uuid.uuid4()
    pending = []
    for iterations, prompt, timeout in [(3, "Cell three", 30), (6, "Cell six", 60)]:
        graph = pe.apply_factor_bindings(
            baseline,
            {
                "Iterations": iterations,
                "System prompt": prompt,
                "Goal": prompt,
                "Timeout": timeout,
                "Budget": iterations,
                "Tools": ["ping"] if iterations == 3 else [],
            },
        )
        node = graph["nodes"][0]
        common = dict(protocol_id=protocol_id, protocol_run_id=uuid.uuid4(), owner_id=owner_id, graph=graph)
        if kind == "agent":
            ambient = {"dataset_mode": "per_row"} if row_mode else {}
            pending.append(pe._run_agent_node(node, user_input="Run cell", ambient_meta=ambient, **common))
        else:
            pending.append(pe._run_critic(node, worker_output="Review cell", **common))
    outcomes = await asyncio.gather(*pending)
    assert all(outcome[1] is None for outcome in outcomes)
    assert len(agents) == 1
    [agent] = agents.values()
    assert agent.name == f"protocol-{protocol_id}-a"
    assert agent.system_prompt == "Baseline system prompt"
    assert agent.goal == "Baseline goal"
    assert agent.max_run_duration_seconds == 90
    assert agent.budget_limit_usd == 5
    if kind == "agent":
        assert agent.pattern_config["pattern_params"]["reason_act"]["max_iterations"] == 9
        assert agent.tool_config_data["tool_names"] == ["server.baseline"]

    for run, (iterations, prompt, timeout) in zip(
        runs.values(),
        [(3, "Cell three", 30), (6, "Cell six", 60)],
        strict=True,
    ):
        snapshot = run.config_snapshot
        assert snapshot["system_prompt"] == prompt
        assert snapshot["goal"] == prompt
        assert snapshot["budget_limit_usd"] == iterations
        assert snapshot["max_run_duration_seconds"] == executions[run.id] == timeout
        if kind == "agent":
            names = run.run_metadata[run_tools.PINNED_TOOL_CONFIG]["tool_names"]
            assert ("server.ping" in names) == (iterations == 3)
            assert "server.baseline" not in names
            assert snapshot["pattern_config"]["pattern_params"]["reason_act"]["max_iterations"] == iterations
        else:
            assert run.run_metadata[run_tools.PINNED_TOOL_CONFIG]["tool_names"] == []
            assert snapshot["output_contract"] == pe.CRITIC_OUTPUT_CONTRACT


async def test_worker_uses_pinned_tools_and_timeout(storage, monkeypatch):
    agents, _runs = storage
    agent = await runner.create_agent(
        name="shared",
        owner_id=uuid.uuid4(),
        goal="Work",
        max_run_duration_seconds=90,
        tool_config={"tool_names": ["server.other"]},
    )
    run = await runner.create_run(
        agent_id=agent.id,
        owner_id=agent.owner_id,
        user_input="Work",
        config_snapshot={"max_run_duration_seconds": 30},
        metadata={run_tools.PINNED_TOOL_CONFIG: {"tool_names": ["server.ping"]}},
    )
    agent.max_run_duration_seconds = 999
    agent.tool_config_data = {"tool_names": ["server.other"]}
    catalog = [{"name": "server.ping", "tool_name": "ping", "server": "server"}]
    registry = SimpleNamespace(get_all_tools=lambda **kwargs: copy.deepcopy(catalog))
    monkeypatch.setattr(run_tools, "get_registry", lambda: registry)
    monkeypatch.setattr(tasks, "get_registry", lambda: registry)
    monkeypatch.setattr(tasks, "get_run", AsyncMock(return_value=run))
    execute = AsyncMock()
    monkeypatch.setattr(tasks, "execute_run", execute)
    timeouts = []

    async def wait_for(coro, *, timeout):
        timeouts.append(timeout)
        return await coro

    monkeypatch.setattr(tasks.asyncio, "wait_for", wait_for)
    await tasks.execute_run_task({}, str(run.id))
    assert timeouts == [30]
    assert execute.call_args.kwargs["available_tools"] == catalog
    assert len(agents) == 1


@pytest.mark.parametrize("published", [False, True])
async def test_baseline_comes_from_unfactored_publication(monkeypatch, published):
    run = SimpleNamespace(protocol_id=uuid.uuid4(), protocol_revision_id=uuid.uuid4() if published else None)
    draft, publication = _graph(), copy.deepcopy(_graph())
    publication["nodes"][0]["data"]["config"]["goal"] = "Published baseline"

    @asynccontextmanager
    async def session():
        yield MagicMock()

    monkeypatch.setattr(pe, "get_session", session)
    monkeypatch.setattr(pe, "get_protocol_run", AsyncMock(return_value=run))
    get_protocol = AsyncMock(return_value=SimpleNamespace(graph=draft))
    monkeypatch.setattr(pe, "get_protocol", get_protocol)
    monkeypatch.setattr(pe, "get_revision", AsyncMock(return_value=SimpleNamespace(graph=publication)))
    assert await pe._baseline_graph_for_run(uuid.uuid4()) == (publication if published else draft)
    if published:
        get_protocol.assert_not_awaited()
