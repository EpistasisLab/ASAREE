"""Compiling and running a Protocol's graph.

``topological_order`` is pure (no DB, no network) -- validated by unit tests
alone. ``run_protocol`` is the orchestrator, meant to run inside the arq
worker (see ``asaree.worker.tasks.execute_protocol_run_task``), calling
agentic-core's runner functions directly -- the same "direct call, not a
nested enqueue" approach ``execute_run_task`` already uses for one agent run.

A ``critic_gate`` node is never run on its own turn in the main loop -- its
worker's ``find_gated_pairs`` entry means the worker's own turn dispatches to
``_run_gated_worker``, which resolves BOTH nodes' outcomes together (see its
docstring). This keeps ``topological_order``'s graph shape completely
ordinary: Worker -> CriticGate -> NextNode is a plain forward DAG edge: the
revision "loop" lives entirely inside how one pair is executed, not in the
graph structure.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from typing import Any

from agentic_core.mcp.registry import get_registry
from agentic_core.runner import create_agent, create_run, execute_run, get_agent_by_name, get_run, update_agent
from agentic_core.schemas.agent import ModelConfig
from agentic_core.schemas.output import parse_envelope
from agentic_core.schemas.pattern import PatternConfig
from agentic_core.services.mcp_service import call_server_tool
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.config import get_settings
from asaree.models.database import get_session
from asaree.models.protocol_run import ProtocolRun
from asaree.services.factorial_cells import list_cells, upsert_cell
from asaree.services.protocol_runs import create_protocol_run, get_protocol_run, set_status, update_node_run
from asaree.services.protocols import get_protocol
from asaree.services.run_tools import gather_tools

# Mirrors the notebook's CRITIC_CONTRACT exactly (spinal_pipeline.ipynb cell
# 15) -- hardcoded, not user-editable/stored in the graph, so the executor
# can always trust these field names when reading a critic's verdict.
CRITIC_OUTPUT_CONTRACT: dict[str, Any] = {
    "name": "CriticVerdict",
    "fields": [
        {
            "name": "approved",
            "type": "bool",
            "description": "true if the output passes every criterion, false if it needs revision",
        },
        {
            "name": "feedback",
            "type": "str",
            "default": "",
            "description": "actionable revision instructions when not approved; empty when approved",
        },
        {
            "name": "rejection_scope",
            "type": "str",
            "default": "",
            "description": (
                "when not approved: 'partial' if the failing criteria are localized and every "
                "uncriticized decision must be preserved, 'full' if the approach must be "
                "reconsidered from first principles; empty when approved"
            ),
        },
    ],
}

# Generalized from the notebook's own scope-clause text (run_stage, cell 19) --
# dropped the workspace-manifest "prior_block" and tool-call-repeat warning,
# neither of which has a generic-canvas equivalent (both assume the
# file-based workspace handoff this one use case's MCP tools happen to use).
_SCOPE_CLAUSES: dict[str, str] = {
    "partial": (
        "SCOPE -- this is a targeted correction, not a redesign. Change ONLY what the feedback "
        "names. Every other decision in your previous output went uncriticized: reproduce it "
        "exactly."
    ),
    "full": (
        "SCOPE -- the reviewer rejected this output's approach, not one detail of it. Reconsider "
        "it from first principles: you may change any decision, including ones the feedback does "
        "not name. Do not anchor on your previous output -- it is shown below as a record of what "
        "was tried and found wanting, not as a baseline to preserve."
    ),
}


class ProtocolValidationError(Exception):
    """The graph can't be run as-is (empty, a cycle, or a malformed critic-gate topology)."""


def _adjacency(
    graph: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], dict[str, list[str]]]:
    nodes = {n["id"]: n for n in graph.get("nodes") or []}
    downstream: dict[str, list[str]] = {nid: [] for nid in nodes}
    upstream: dict[str, list[str]] = {nid: [] for nid in nodes}
    for edge in graph.get("edges") or []:
        source, target = edge.get("source"), edge.get("target")
        if source not in nodes or target not in nodes:
            continue  # a dangling edge is not this function's problem to reject
        downstream[source].append(target)
        upstream[target].append(source)
    return nodes, downstream, upstream


def topological_order(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Kahn's algorithm. Raises :class:`ProtocolValidationError` on an empty
    graph, a cycle (any node Kahn's algorithm can't reach stays with a
    nonzero in-degree, which is exactly the cycle signature), or a malformed
    critic-gate topology: a ``critic_gate`` node must have exactly one
    incoming edge, from an ``agent`` node, and that agent node's *only*
    outgoing edge must be to this gate -- no fan-out around a gate, since
    anything wanting the reviewed output must consume it after the gate."""
    nodes, downstream, upstream = _adjacency(graph)
    if not nodes:
        raise ProtocolValidationError("This protocol has no nodes.")

    in_degree = {nid: len(ups) for nid, ups in upstream.items()}
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

    for nid, node in nodes.items():
        if node.get("type") != "critic_gate":
            continue
        ups = upstream[nid]
        if len(ups) != 1:
            raise ProtocolValidationError(
                f"Critic Gate node {nid!r} must have exactly one incoming connection (found {len(ups)})."
            )
        worker_id = ups[0]
        if nodes[worker_id].get("type") != "agent":
            raise ProtocolValidationError(
                f"Critic Gate node {nid!r}'s incoming connection must come from an Agent node."
            )
        if len(downstream[worker_id]) != 1:
            raise ProtocolValidationError(
                f"Agent node {worker_id!r} is gated by a Critic Gate and can't have any other outgoing connections."
            )

    return [nodes[nid] for nid in ordered]


def sink_node_ids(graph: dict[str, Any]) -> list[str]:
    """Every node with no outgoing edges -- used both to validate a graph is
    runnable per-cell (exactly one sink required, see ``plan_cell_runs``) and
    by ``run_protocol`` itself to find the node whose output becomes a cell's
    result."""
    nodes, downstream, _upstream = _adjacency(graph)
    return [nid for nid in nodes if not downstream[nid]]


def _set_path(root: dict[str, Any], dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    target = root
    for part in parts[:-1]:
        if not isinstance(target.get(part), dict):
            return  # malformed path -- nothing to set into, skip silently
        target = target[part]
    target[parts[-1]] = value


def apply_factor_bindings(graph: dict[str, Any], factor_values: dict[str, Any]) -> dict[str, Any]:
    """Returns a deep copy of *graph* with every node's ``data.factor_bindings``
    substituted in from *factor_values* -- e.g. a node with
    ``data.factor_bindings == {"config.model_config_data.temperature":
    "Temperature"}`` gets ``node["data"]["config"]["model_config_data"]
    ["temperature"]`` set to ``factor_values["Temperature"]``, if that factor
    name is present. A binding to a factor absent from *factor_values*, or a
    malformed field path, is silently skipped -- best-effort, the same way
    the rest of this executor treats a missing/malformed config value rather
    than raising."""
    patched = copy.deepcopy(graph)
    for node in patched.get("nodes") or []:
        data = node.get("data") or {}
        bindings = data.get("factor_bindings") or {}
        for field_path, factor_name in bindings.items():
            if factor_name in factor_values:
                _set_path(data, field_path, factor_values[factor_name])
    return patched


def find_gated_pairs(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Maps worker_node_id -> its critic_gate node, for every gated pair.
    Trusts the graph is already validated (call after ``topological_order``,
    which is what actually enforces this shape)."""
    nodes, _downstream, upstream = _adjacency(graph)
    pairs: dict[str, dict[str, Any]] = {}
    for nid, node in nodes.items():
        if node.get("type") == "critic_gate" and upstream[nid]:
            pairs[upstream[nid][0]] = node
    return pairs


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


def _build_revision_instruction(base_instruction: str, verdict: dict[str, Any], previous_output: str) -> str:
    scope_clause = _SCOPE_CLAUSES.get(verdict.get("rejection_scope") or "", "")
    feedback = verdict.get("feedback") or ""
    parts = [
        base_instruction,
        "--- REVISION REQUESTED ---\nA reviewer rejected your previous output. Produce a "
        "corrected, complete output that addresses every point below.",
    ]
    if scope_clause:
        parts.append(scope_clause)
    parts.append(f"Reviewer feedback:\n{feedback}")
    parts.append(f"Your previous output (for reference):\n\n{previous_output}")
    return "\n\n".join(parts)


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


async def _run_critic(
    gate: dict[str, Any], *, protocol_id: uuid.UUID, protocol_run_id: uuid.UUID, owner_id: uuid.UUID, worker_output: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Create-or-sync the gate's own critic agent and run it once. Returns
    ``(verdict, error)`` -- exactly one is ``None``. The critic never gets
    tools and always runs single-pass (matches the notebook's own
    ``CRITIC_TOOLS = []`` / ``SINGLE_PASS_PATTERN``), and its
    ``output_contract`` is always :data:`CRITIC_OUTPUT_CONTRACT` -- not
    whatever (if anything) is in the node's own config."""
    config = gate["data"]["config"]
    agent_name = f"protocol-{protocol_id}-{gate['id']}"
    model_config_data = {k: v for k, v in (config.get("model_config_data") or {}).items() if v is not None}
    model_config = ModelConfig(**model_config_data)
    pattern_config = PatternConfig(execution_pattern="single_agent_baseline").model_dump()
    goal = config.get("goal") or "Review the given output and return an approval verdict with feedback."
    description = config.get("description") or ""
    label = gate.get("data", {}).get("label")
    if label:
        description = f"{description} (canvas label: {label})".strip()
    tool_config = {"server_names": [], "tool_names": []}

    existing = await get_agent_by_name(agent_name, owner_id=owner_id)
    if existing is not None:
        agent = await update_agent(
            existing.id,
            goal=goal,
            description=description,
            system_prompt=config.get("system_prompt") or "",
            model_config=model_config,
            pattern_config=pattern_config,
            tool_config=tool_config,
            output_contract=CRITIC_OUTPUT_CONTRACT,
        )
    else:
        agent = await create_agent(
            name=agent_name,
            goal=goal,
            description=description,
            system_prompt=config.get("system_prompt") or "",
            model_config=model_config,
            pattern_config=pattern_config,
            tool_config=tool_config,
            output_contract=CRITIC_OUTPUT_CONTRACT,
            owner_id=owner_id,
        )
    assert agent is not None

    instruction = f"Review the following output and return your verdict.\n\nOutput to review:\n\n{worker_output}"
    run = await create_run(
        agent_id=agent.id,
        user_input=instruction,
        owner_id=owner_id,
        metadata={"protocol_id": str(protocol_id), "protocol_run_id": str(protocol_run_id), "node_id": gate["id"]},
    )
    timeout = agent.max_run_duration_seconds or get_settings().worker_job_timeout_seconds
    try:
        await asyncio.wait_for(
            execute_run(run_id=run.id, registry=get_registry(), available_tools=gather_tools(agent)),
            timeout=timeout,
        )
    except TimeoutError:
        return None, f"critic run exceeded its {timeout}s execution budget"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"

    finished = await get_run(run.id)
    if finished is None:
        return None, "critic run vanished after execution"
    if finished.error:
        return None, finished.error
    envelope = parse_envelope(finished.output)
    if envelope is None or envelope.payload is None:
        return None, "critic did not return a structured verdict"
    return envelope.payload, None


async def _run_gated_worker(
    worker: dict[str, Any],
    gate: dict[str, Any],
    *,
    protocol_id: uuid.UUID,
    protocol_run_id: uuid.UUID,
    owner_id: uuid.UUID,
    graph: dict[str, Any],
    node_runs: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generalizes the notebook's ``run_stage`` revision loop (cell 19):
    run worker -> if the gate is enabled, run critic on its output -> on
    rejection, rebuild the instruction with the critic's feedback and rerun
    -> repeat up to ``max_revisions`` -- the FINAL attempt never calls the
    critic at all (its verdict would be ignored anyway) and force-accepts,
    the same optimization the notebook makes. No workspace-reset step here
    (a real, documented limitation): the notebook resets on-disk state
    between attempts via use-case-specific MCP tools with no generic canvas
    equivalent -- a revision attempt just reruns the same agent with a new
    instruction. Returns ``(worker_node_run, gate_node_run)``."""
    gate_config = gate["data"]["config"]
    max_revisions = max(int(gate_config.get("max_revisions") or 0), 0)
    enabled = bool(gate_config.get("enabled", True))
    base_instruction = _build_user_input(worker, graph, node_runs)
    instruction = base_instruction

    for attempt in range(max_revisions + 1):
        output_text, error = await _run_agent_node(
            worker,
            protocol_id=protocol_id,
            protocol_run_id=protocol_run_id,
            owner_id=owner_id,
            user_input=instruction,
        )
        if error:
            return (
                {"status": "failed", "output_text": None, "error": error, "attempts": attempt + 1},
                {"status": "skipped"},
            )

        if not enabled:
            return (
                {"status": "completed", "output_text": output_text, "error": None, "attempts": attempt + 1},
                {"status": "completed", "output_text": output_text, "approved": None, "revisions_used": 0},
            )

        if attempt == max_revisions:
            return (
                {"status": "completed", "output_text": output_text, "error": None, "attempts": attempt + 1},
                {
                    "status": "completed",
                    "output_text": output_text,
                    "approved": None,
                    "revisions_used": attempt,
                    "forced": True,
                },
            )

        verdict, verdict_error = await _run_critic(
            gate, protocol_id=protocol_id, protocol_run_id=protocol_run_id, owner_id=owner_id, worker_output=output_text
        )
        if verdict_error:
            # The critic itself failed to run -- fail the whole gated pair
            # rather than silently treating an unchecked output as approved.
            return (
                {
                    "status": "failed",
                    "output_text": output_text,
                    "error": f"critic failed: {verdict_error}",
                    "attempts": attempt + 1,
                },
                {"status": "failed", "output_text": None, "error": verdict_error},
            )

        if verdict.get("approved"):
            return (
                {"status": "completed", "output_text": output_text, "error": None, "attempts": attempt + 1},
                {"status": "completed", "output_text": output_text, "approved": True, "revisions_used": attempt},
            )

        instruction = _build_revision_instruction(base_instruction, verdict, output_text)

    raise AssertionError("_run_gated_worker fell through its attempt loop")  # unreachable


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


async def plan_cell_runs(
    db: AsyncSession,
    *,
    protocol_id: uuid.UUID,
    experiment_id: uuid.UUID | None,
    owner_id: uuid.UUID,
    graph: dict[str, Any],
) -> tuple[list[ProtocolRun], int]:
    """"Run all cells": creates one pending :class:`ProtocolRun` per
    not-yet-scored :class:`FactorialCellResult` under *experiment_id*, each
    carrying that cell's own ``factor_values`` for ``run_protocol`` to
    substitute at execution time via ``apply_factor_bindings``. Returns
    ``(created_runs, skipped_count)`` -- a cell already carrying
    ``metric_values`` is skipped (resume semantics: a repeat click doesn't
    re-run, and re-bill, an already-scored cell). Raises
    :class:`ProtocolValidationError` (same type the plain-run endpoint
    already 422s on) if there's no linked experiment, the graph itself is
    invalid, or the graph doesn't have exactly one sink node -- a cell's
    result has to come from somewhere unambiguous, mirroring the notebook's
    own single-pipeline (DC->FTE->FS->MLM) shape. Does NOT enqueue the
    created runs -- that's the caller's job, same create-then-enqueue split
    ``create_protocol_run_endpoint`` already uses for a plain run."""
    if experiment_id is None:
        raise ProtocolValidationError("This protocol has no linked experiment to run cells for.")
    topological_order(graph)  # raises ProtocolValidationError on a cycle/empty graph
    sinks = sink_node_ids(graph)
    if len(sinks) != 1:
        raise ProtocolValidationError(
            f"This protocol must have exactly one final node to run per experimental cell (found {len(sinks)})."
        )

    cells = await list_cells(db, experiment_id=experiment_id)
    pending = [c for c in cells if not c.metric_values]
    runs = [
        await create_protocol_run(
            db,
            protocol_id=protocol_id,
            owner_id=owner_id,
            cell_label=cell.cell_label,
            factor_values=cell.factor_values or {},
        )
        for cell in pending
    ]
    return runs, len(cells) - len(pending)


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
        experiment_id, cell_label, factor_values = protocol.experiment_id, run.cell_label, run.factor_values

    # Both None for a plain graph run. Set together only for a run created by
    # "run all cells" (plan_cell_runs) -- substitute this cell's factor
    # values into whichever fields the canvas bound to a matching factor
    # name before doing anything else, so every node below (including
    # topological_order's own validation) sees the already-patched graph.
    if factor_values:
        graph = apply_factor_bindings(graph, factor_values)

    try:
        order = topological_order(graph)
    except ProtocolValidationError as e:
        async with get_session() as db:
            await set_status(db, protocol_run_id, status="failed", error=str(e))
        return

    gated_by = find_gated_pairs(graph)

    async with get_session() as db:
        await set_status(db, protocol_run_id, status="running")
        if cell_label and experiment_id:
            # Pre-write, before any node executes: a crash/timeout mid-run
            # still leaves this cell's provenance recorded (mirrors the
            # notebook's own pre-scoring upsert_cell call).
            await upsert_cell(
                db,
                experiment_id=experiment_id,
                cell_label=cell_label,
                fields={"run_id": protocol_run_id, "factor_values": factor_values or {}},
            )

    node_runs: dict[str, Any] = {}
    failed = False
    for node in order:
        node_id = node["id"]
        if node_id in node_runs:
            continue  # already resolved -- a critic_gate node handled via its worker's turn below
        if failed:
            node_runs[node_id] = {"status": "skipped"}
            async with get_session() as db:
                await update_node_run(db, protocol_run_id, node_id, {"status": "skipped"})
            continue

        async with get_session() as db:
            await update_node_run(db, protocol_run_id, node_id, {"status": "running"})

        if node_id in gated_by:
            gate = gated_by[node_id]
            worker_run, gate_run = await _run_gated_worker(
                node, gate, protocol_id=protocol_id, protocol_run_id=protocol_run_id, owner_id=owner_id,
                graph=graph, node_runs=node_runs,
            )
            node_runs[node_id] = worker_run
            node_runs[gate["id"]] = gate_run
            async with get_session() as db:
                await update_node_run(db, protocol_run_id, node_id, worker_run)
                await update_node_run(db, protocol_run_id, gate["id"], gate_run)
            if worker_run["status"] == "failed" or gate_run["status"] == "failed":
                failed = True
            continue

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
            if cell_label and experiment_id:
                # Post-write, success only: fold the graph's single designated
                # output (the sink node's raw output_text) into this cell's
                # artifacts. metric_values is deliberately NOT auto-populated
                # here -- there's no concept yet of "which output_contract
                # field is the metric" (a real, documented limitation, not an
                # oversight); a user promotes artifacts into metric_values
                # manually via PUT /experiments/{id}/cells/{cell_label}, the
                # same manual step the notebook's own score_payload is today.
                sinks = sink_node_ids(graph)
                if len(sinks) == 1 and node_runs.get(sinks[0], {}).get("status") == "completed":
                    await upsert_cell(
                        db,
                        experiment_id=experiment_id,
                        cell_label=cell_label,
                        fields={
                            "artifacts": {
                                "output_text": node_runs[sinks[0]].get("output_text"),
                                "protocol_run_id": str(protocol_run_id),
                            }
                        },
                    )
