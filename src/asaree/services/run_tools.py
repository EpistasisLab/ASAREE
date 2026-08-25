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

    Descriptors whose bare ``tool_name`` is exposed by more than one
    connected server get that field rewritten to the namespaced
    ``"{server}.{tool}"`` form (``name`` already is). Two things downstream
    read the bare name and both break on a collision:

    * ``motoro.mcp.adapters.tools_to_openai_format`` names the provider-facing
      function after it, so two servers with a ``ping`` bind two tools called
      ``ping`` -- which Anthropic/Azure reject outright ("tools: Tool names
      must be unique"), failing the whole run before the first LLM call.
    * ``MCPServerRegistry.lookup_tool`` resolves a bare name through an index
      spanning every connected server and silently returns the *first* match,
      so a call meant for one server's ``ping`` can be dispatched to another's
      (or bounce off the allow-list as "not allowed" once it resolves to a
      server this run was never granted).

    Namespacing only the colliding entries keeps the common case's clean tool
    names -- the namespaced form has to keep its ``.`` for ``lookup_tool`` to
    resolve it directly, which costs the provider-facing name a sanitising
    hash suffix, so it isn't worth applying to tools that don't need it.
    Collisions are computed over the whole registry, not just this run's
    allow-list, because ``lookup_tool``'s bare-name index is registry-wide.
    """
    tool_names = set((agent.tool_config_data or {}).get("tool_names") or [])
    if not tool_names:
        return []
    catalog = get_registry().get_all_tools()
    servers_by_bare_name: dict[str, set[str]] = {}
    for tool in catalog:
        bare = str(tool.get("tool_name") or "")
        servers_by_bare_name.setdefault(bare, set()).add(str(tool.get("server") or ""))
    admitted: list[dict[str, Any]] = []
    for tool in catalog:
        if tool["name"] not in tool_names:
            continue
        if len(servers_by_bare_name.get(str(tool.get("tool_name") or ""), ())) > 1:
            tool = {**tool, "tool_name": tool["name"]}
        admitted.append(tool)
    return admitted
