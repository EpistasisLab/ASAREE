"""Unit tests for run_tools.gather_tools -- the agent tool_config -> run
allow-list resolver. Fake registry throughout; no MCP server is contacted."""

from __future__ import annotations

from typing import Any

import pytest
from motoro.mcp.adapters import build_openai_tool_name_map, tools_to_openai_format

from asaree.services import run_tools


class _FakeRegistry:
    def __init__(self, servers: dict[str, list[str]]) -> None:
        self.servers = servers

    def get_all_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": f"{server}.{tool}",
                "server": server,
                "tool_name": tool,
                "description": f"{tool} on {server}",
                "input_schema": {"type": "object", "properties": {}},
            }
            for server, tools in self.servers.items()
            for tool in tools
        ]


class _FakeAgent:
    def __init__(self, tool_names: list[str] | None) -> None:
        self.tool_config_data = None if tool_names is None else {"tool_names": tool_names}


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


def test_colliding_bare_name_is_namespaced(registry: _FakeRegistry) -> None:
    """Two servers exposing ``ping`` must not both bind a tool called ``ping``
    -- the provider rejects duplicate names and the run dies before its first
    LLM call (the "tools: Tool names must be unique" failure)."""
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


def test_collision_is_registry_wide_not_allowlist_wide(registry: _FakeRegistry) -> None:
    """Only one server's ``ping`` is granted, but the other is still connected
    -- ``lookup_tool``'s bare-name index spans the whole registry, so a bare
    ``ping`` could resolve to the server this run was never granted."""
    tools = run_tools.gather_tools(_FakeAgent(["scikit-learn-mcp.ping"]))
    assert [t["tool_name"] for t in tools] == ["scikit-learn-mcp.ping"]
