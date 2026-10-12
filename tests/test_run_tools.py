"""Unit tests for run_tools.gather_tools -- the agent tool_config -> run
allow-list resolver. Fake registry throughout; no MCP server is contacted."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from motoro.engine.context import RunContext
from motoro.mcp.adapters import MCPToolExecutor, build_openai_tool_name_map, tools_to_openai_format
from motoro.mcp.client import MCPClient, ToolInfo, ToolResult
from motoro.mcp.registry import MCPServerRegistry
from motoro.schemas.agent import ModelConfig
from motoro.schemas.llm import PlanStep

from asaree.services import run_tools


class _FakeRegistry:
    def __init__(self, servers: dict[str, list[str]]) -> None:
        self.servers = servers

    def get_all_tools(self, *, owner_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        assert owner_id == _OWNER_ID
        return [
            {
                "name": f"{server}.{tool}",
                "server": server,
                "server_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, server)),
                "tool_name": tool,
                "description": f"{tool} on {server}",
                "input_schema": {"type": "object", "properties": {}},
            }
            for server, tools in self.servers.items()
            for tool in tools
        ]


class _FakeAgent:
    def __init__(self, tool_names: list[str] | None, tool_descriptions: dict[str, str] | None = None) -> None:
        self.tool_config_data = (
            None if tool_names is None else {"tool_names": tool_names, "tool_descriptions": tool_descriptions or {}}
        )
        self.owner_id = _OWNER_ID


_OWNER_ID = uuid.uuid4()


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> _FakeRegistry:
    reg = _FakeRegistry(
        {
            "scikit-learn-mcp": ["describe_dataset", "run_script", "ping"],
            "okf-doc-hair-concentrations": ["search_concepts", "ping"],
        }
    )
    monkeypatch.setattr(run_tools, "get_registry", lambda: reg)
    return reg


def test_no_tool_config_gets_no_tools(registry: _FakeRegistry) -> None:
    assert run_tools.gather_tools(_FakeAgent(None)) == []
    assert run_tools.gather_tools(_FakeAgent([])) == []


def test_admits_only_named_tools(registry: _FakeRegistry) -> None:
    agent = _FakeAgent(["scikit-learn-mcp.describe_dataset"])
    tools = run_tools.gather_tools(agent)
    assert [t["name"] for t in tools] == ["scikit-learn-mcp.describe_dataset"]
    # Unique bare name -> left alone, so the model sees the clean tool name.
    assert tools[0]["tool_name"] == "describe_dataset"


def test_resource_description_is_prefixed_to_the_live_tool_description(registry: _FakeRegistry) -> None:
    name = "okf-doc-hair-concentrations.search_concepts"
    [tool] = run_tools.gather_tools(_FakeAgent([name], {name: "Knowledge source: Hair concentrations."}))

    assert tool["description"].startswith("Knowledge source: Hair concentrations.\n\n")
    assert tool["description"].endswith("search_concepts on okf-doc-hair-concentrations")


def test_colliding_bare_names_pass_through_to_motoro(registry: _FakeRegistry) -> None:
    """Motoro projects distinct provider names without changing descriptors."""
    agent = _FakeAgent(
        [
            "scikit-learn-mcp.describe_dataset",
            "scikit-learn-mcp.ping",
            "okf-doc-hair-concentrations.search_concepts",
            "okf-doc-hair-concentrations.ping",
        ]
    )
    tools = run_tools.gather_tools(agent)
    assert len(tools) == 4
    assert tools == [
        tool
        for tool in registry.get_all_tools(owner_id=_OWNER_ID)
        if tool["name"] in agent.tool_config_data["tool_names"]
    ]
    assert [t["tool_name"] for t in tools if t["name"].endswith(".ping")] == ["ping", "ping"]

    bound = tools_to_openai_format(tools)
    names = [t["function"]["name"] for t in bound]
    assert len(set(names)) == len(names)

    # Each bound name still round-trips to a namespaced MCP name that
    # ``lookup_tool`` resolves against exactly one server.
    name_map = build_openai_tool_name_map(tools)
    pings = sorted(v for v in name_map.values() if v.endswith("ping"))
    assert pings == ["okf-doc-hair-concentrations.ping", "scikit-learn-mcp.ping"]

    # ``name`` is untouched, so the allow-list check still matches.
    assert {t["name"] for t in tools} == set(agent.tool_config_data["tool_names"])


def test_ungranted_collision_leaves_descriptor_and_provider_name_bare(registry: _FakeRegistry) -> None:
    """A collision outside the run's grants needs no provider-name rewrite."""
    tools = run_tools.gather_tools(_FakeAgent(["scikit-learn-mcp.ping"]))
    assert [t["tool_name"] for t in tools] == ["ping"]
    assert [t["function"]["name"] for t in tools_to_openai_format(tools)] == ["ping"]


@pytest.mark.parametrize("granted_servers", [("server-a", "server-b"), ("server-b",)])
async def test_gathered_tools_dispatch_through_motoro(
    monkeypatch: pytest.MonkeyPatch, granted_servers: tuple[str, ...]
) -> None:
    """Use real discovery and dispatch, replacing only the MCP transport."""
    calls: list[tuple[str, str]] = []

    async def connect(client: MCPClient) -> None:
        client._connected = True
        client._tools = [ToolInfo(name="ping", description=f"Ping {client.name}")]

    async def invoke(client: MCPClient, tool_name: str, arguments: Any, **kwargs: Any) -> ToolResult:
        calls.append((client.name, tool_name))
        return ToolResult(content=f"pong from {client.name}")

    monkeypatch.setattr(MCPClient, "connect", connect)
    monkeypatch.setattr(MCPClient, "call_tool", invoke)
    monkeypatch.setattr("motoro.mcp.adapters.settings", SimpleNamespace(tool_timeout_seconds=1))
    registry = MCPServerRegistry()
    for server in ("server-a", "server-b"):
        await registry.register(server_id=uuid.uuid4(), name=server, owner_id=_OWNER_ID)
    monkeypatch.setattr(run_tools, "get_registry", lambda: registry)

    tools = run_tools.gather_tools(_FakeAgent([f"{server}.ping" for server in granted_servers]))
    assert [tool["tool_name"] for tool in tools] == ["ping"] * len(granted_servers)
    schemas = tools_to_openai_format(tools)
    provider_names = [schema["function"]["name"] for schema in schemas]
    assert len(set(provider_names)) == len(granted_servers)
    mapping = build_openai_tool_name_map(tools)
    context = RunContext(
        agent_goal="Check granted servers",
        system_prompt="Use available tools",
        model_config=ModelConfig(),
        user_input="ping",
        available_tools=tools,
    )
    executor = MCPToolExecutor(registry)
    for provider_name, server in zip(provider_names, granted_servers, strict=True):
        step = PlanStep(action="ping", description="Check server", tool_name=mapping[provider_name])
        assert executor.can_handle(step, context)
        result, _, record = await executor.execute_step(step, context)
        assert result == f"pong from {server}"
        assert record is not None
        assert (record.server, record.tool, record.success) == (server, "ping", True)

    if len(granted_servers) == 1:
        denied = PlanStep(action="ping", description="Check ungranted server", tool_name="server-a.ping")
        assert not executor.can_handle(denied, context)
        with pytest.raises(ValueError, match="not found in this run"):
            await executor.execute_step(denied, context)
    assert calls == [(server, "ping") for server in granted_servers]
