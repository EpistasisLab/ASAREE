"""Compiling and running a Protocol's graph.

``topological_order`` is pure (no DB, no network) -- validated by unit tests
alone. ``run_protocol`` is the orchestrator, meant to run inside the arq
worker (see ``asaree.worker.tasks.execute_protocol_run_task``), calling
agentic-core's runner functions directly -- the same "direct call, not a
nested enqueue" approach ``execute_run_task`` already uses for one agent run.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from agentic_core.mcp.registry import get_registry
from agentic_core.runner import create_agent, create_run, execute_run, get_agent_by_name, get_run, update_agent
from agentic_core.schemas.agent import ModelConfig
from agentic_core.schemas.output import parse_envelope
from agentic_core.schemas.pattern import PatternConfig
from agentic_core.services.mcp_service import call_server_tool

from asaree.config import get_settings
from asaree.models.database import get_session
from asaree.services.protocol_runs import get_protocol_run, set_status, update_node_run
from asaree.services.protocols import get_protocol
from asaree.services.run_tools import gather_tools


class ProtocolValidationError(Exception):
    """The graph can't be run as-is (empty, or contains a cycle)."""


def topological_order(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Kahn's algorithm. Raises :class:`ProtocolValidationError` on an empty
    graph or a cycle (any node Kahn's algorithm can't reach stays with a
    nonzero in-degree, which is exactly the cycle signature)."""
    nodes = {n["id"]: n for n in graph.get("nodes") or []}
    if not nodes:
        raise ProtocolValidationError("This protocol has no nodes.")

    in_degree = dict.fromkeys(nodes, 0)
    downstream: dict[str, list[str]] = {nid: [] for nid in nodes}
    for edge in graph.get("edges") or []:
        source, target = edge.get("source"), edge.get("target")
        if source not in nodes or target not in nodes:
            continue  # a dangling edge is not this function's problem to reject
        downstream[source].append(target)
        in_degree[target] += 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    ordered: list[str] = []
    while queue:
        nid = queue.pop(0)
        ordered.append(nid)
        for nxt in downstream[nid]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    if len(ordered) != len(nodes):
        raise ProtocolValidationError("This protocol's graph has a cycle -- it can't be run in dependency order.")
    return [nodes[nid] for nid in ordered]


def _upstream_ids(graph: dict[str, Any], node_id: str) -> list[str]:
    return [e["source"] for e in graph.get("edges") or [] if e.get("target") == node_id]


def _build_user_input(node: dict[str, Any], graph: dict[str, Any], node_runs: dict[str, Any]) -> str:
    """The node's own goal, plus (flat, unstructured -- a deliberate V1
    simplification) each already-completed upstream node's output_text as
    context. Real structured handoff via output_contract.payload is a
    fast-follow, the same way the source notebook's own stage-report-block
    pattern could graduate to using it."""
    data = node.get("data") or {}
    goal = data.get("config", {}).get("goal") or data.get("label", "")
    upstream_ids = _upstream_ids(graph, node["id"])
    if not upstream_ids:
        return goal
    context = [
        f"[{uid}]: {node_runs[uid]['output_text']}"
        for uid in upstream_ids
        if node_runs.get(uid, {}).get("output_text")
    ]
    if not context:
        return goal
    return f"{goal}\n\nUpstream context:\n" + "\n\n".join(context)


async def _run_agent_node(
    node: dict[str, Any], *, protocol_id: uuid.UUID, protocol_run_id: uuid.UUID, owner_id: uuid.UUID, user_input: str
) -> tuple[str | None, str | None]:
    """Create-or-sync the real agent and run it to completion. Returns
    ``(output_text, error)`` -- exactly one is ``None``."""
    config = node["data"]["config"]
    # Deterministic, not config["name"]: Agent.name is unique per OWNER, not
    # per protocol, so trusting the freeform (often identically-defaulted)
    # config.name directly risks two unrelated nodes silently overwriting
    # each other's agent definition on every run. config.name is folded
    # into the description instead, purely as a human label.
    agent_name = f"protocol-{protocol_id}-{node['id']}"
    model_config_data = {k: v for k, v in (config.get("model_config_data") or {}).items() if v is not None}
    model_config = ModelConfig(**model_config_data)
    pattern_config_data = config.get("pattern_config") or {}
    pattern_config = PatternConfig(
        execution_pattern=pattern_config_data.get("execution_pattern"),
        pattern_params=pattern_config_data.get("pattern_params") or {},
    ).model_dump()
    description = config.get("description") or ""
    label = node.get("data", {}).get("label")
    if label:
        description = f"{description} (canvas label: {label})".strip()

    existing = await get_agent_by_name(agent_name, owner_id=owner_id)
    if existing is not None:
        agent = await update_agent(
            existing.id,
            goal=config.get("goal") or "",
            description=description,
            system_prompt=config.get("system_prompt") or "",
            model_config=model_config,
            pattern_config=pattern_config,
            tool_config=config.get("tool_config"),
            output_contract=config.get("output_contract"),
            budget_limit_usd=config.get("budget_limit_usd"),
            max_run_duration_seconds=config.get("max_run_duration_seconds"),
        )
    else:
        agent = await create_agent(
            name=agent_name,
            goal=config.get("goal") or "",
            description=description,
            system_prompt=config.get("system_prompt") or "",
            model_config=model_config,
            pattern_config=pattern_config,
            tool_config=config.get("tool_config"),
            output_contract=config.get("output_contract"),
            budget_limit_usd=config.get("budget_limit_usd"),
            max_run_duration_seconds=config.get("max_run_duration_seconds"),
            owner_id=owner_id,
        )
    assert agent is not None

    run = await create_run(
        agent_id=agent.id,
        user_input=user_input,
        owner_id=owner_id,
        metadata={"protocol_id": str(protocol_id), "protocol_run_id": str(protocol_run_id), "node_id": node["id"]},
    )
    timeout = agent.max_run_duration_seconds or get_settings().worker_job_timeout_seconds
    try:
        await asyncio.wait_for(
            execute_run(run_id=run.id, registry=get_registry(), available_tools=gather_tools(agent)),
            timeout=timeout,
        )
    except TimeoutError:
        return None, f"run exceeded its {timeout}s execution budget"
    except Exception as e:  # noqa: BLE001 -- same boundary reasoning as execute_run_task
        return None, f"{type(e).__name__}: {e}"

    finished = await get_run(run.id)
    if finished is None:
        return None, "run vanished after execution"
    if finished.error:
        return None, finished.error
    envelope = parse_envelope(finished.output)
    output_text = envelope.result if envelope is not None else (finished.output or "")
    return output_text, None


async def _run_mcp_tool_node(node: dict[str, Any]) -> tuple[str | None, str | None]:
    """Calls the tool directly -- no AgentRun, no LLM loop. Empty arguments
    in V1 (no argument-mapping UI yet; a real, documented limitation)."""
    config = node["data"]["config"]
    server_id = config.get("server_id")
    tool_name = config.get("tool_name")
    if not server_id or not tool_name:
        return None, "this MCP Tool node has no server/tool selected"
    try:
        outcome = await call_server_tool(uuid.UUID(server_id), tool_name, {})
    except RuntimeError as e:
        return None, str(e)
    if outcome is None:
        return None, f"no such server: {server_id}"
    is_error, content = outcome
    return (None, content) if is_error else (content, None)


async def run_protocol(protocol_run_id: uuid.UUID) -> None:
    async with get_session() as db:
        run = await get_protocol_run(db, protocol_run_id)
        if run is None:
            return
        protocol = await get_protocol(db, run.protocol_id)
        if protocol is None:
            await set_status(db, protocol_run_id, status="failed", error="protocol no longer exists")
            return
        protocol_id, owner_id, graph = protocol.id, run.owner_id, protocol.graph

    try:
        order = topological_order(graph)
    except ProtocolValidationError as e:
        async with get_session() as db:
            await set_status(db, protocol_run_id, status="failed", error=str(e))
        return

    async with get_session() as db:
        await set_status(db, protocol_run_id, status="running")

    node_runs: dict[str, Any] = {}
    failed = False
    for node in order:
        node_id = node["id"]
        if failed:
            node_runs[node_id] = {"status": "skipped"}
            async with get_session() as db:
                await update_node_run(db, protocol_run_id, node_id, {"status": "skipped"})
            continue

        async with get_session() as db:
            await update_node_run(db, protocol_run_id, node_id, {"status": "running"})

        if node.get("type") == "mcp_tool":
            output_text, error = await _run_mcp_tool_node(node)
        else:
            user_input = _build_user_input(node, graph, node_runs)
            output_text, error = await _run_agent_node(
                node, protocol_id=protocol_id, protocol_run_id=protocol_run_id, owner_id=owner_id, user_input=user_input
            )

        node_runs[node_id] = {"status": "failed" if error else "completed", "output_text": output_text, "error": error}
        async with get_session() as db:
            await update_node_run(db, protocol_run_id, node_id, node_runs[node_id])
        if error:
            failed = True

    async with get_session() as db:
        if failed:
            await set_status(db, protocol_run_id, status="failed", error="one or more nodes failed")
        else:
            await set_status(db, protocol_run_id, status="completed")
