"""Resolve an agent's tool_config into a run's tool allow-list.

Shared by the inline run endpoint (``asaree.api.runs``) and, once the worker
slice lands, the background worker's run task — neither request-scoped nor
DB-session-scoped, so it has no reason to live in either exclusively.
"""

from __future__ import annotations

from typing import Any

from motoro.mcp.registry import get_registry


def gather_tools(agent: Any) -> list[dict[str, Any]]:
    """Resolve an agent's ``tool_config`` into this run's tool allow-list.

    ``execute_run`` never reads ``Agent.tool_config_data`` itself (see its
    ``available_tools`` param) -- the caller is responsible for turning it
    into the resolved catalog entries the orchestrator enforces as an
    allow-list (``motoro.mcp.adapters._tool_in_allowlist``).

    ``tool_config`` here is ``{"server_names": [...], "tool_names":
    ["server.tool", ...]}`` (asaree.api.agents.CreateAgentRequest / the
    spinal-fusion notebook's ``make_agent``). ``tool_names`` is authoritative
    -- membership in it, matched against the registry's namespaced ``name``,
    is what admits a catalog entry; ``server_names`` is not separately
    enforced, since every entry in ``tool_names`` already implies its server.
    An agent with no ``tool_config``, or one naming no tools, gets none --
    the executor fails closed on an empty allow-list rather than granting
    every connected server's tools by default.

    Catalog descriptors pass through unchanged, apart from optional
    ``tool_descriptions`` prefixes. Motoro owns collision-safe provider names,
    dispatch resolution, and allow-list matching; ``tool_name`` stays bare
    even when several servers expose the same tool.
    """
    tool_names = set((agent.tool_config_data or {}).get("tool_names") or [])
    description_prefixes = (agent.tool_config_data or {}).get("tool_descriptions") or {}
    if not tool_names:
        return []
    catalog = get_registry().get_all_tools(owner_id=agent.owner_id)
    admitted: list[dict[str, Any]] = []
    for tool in catalog:
        if tool["name"] not in tool_names:
            continue
        prefix = str(description_prefixes.get(tool["name"]) or "").strip()
        if prefix:
            description = str(tool.get("description") or "").strip()
            tool = {**tool, "description": f"{prefix}\n\n{description}" if description else prefix}
        admitted.append(tool)
    return admitted
