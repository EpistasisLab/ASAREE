"""Capture custom metrics reported by configured Agent nodes and their tools.

Custom metrics are declarations, not evaluator jobs. ASAREE never invokes an
Agent, Script, or MCP tool here; it only records an Agent's completed final
output or the last matching call already present in its immutable run trace.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from motoro.runner import get_run_steps
from motoro.schemas.llm import flatten_tool_call_records
from motoro.services import mcp_service

from asaree.models.protocol_run import ProtocolRun
from asaree.services.measurement_engine import (
    MeasurementEvaluation,
    MeasurementPlan,
    MetricObservation,
    ProducerBinding,
    ProducerProvenance,
    ValidationIssue,
    ValidationReport,
    preserved_binding_severity,
)
from asaree.services.protocol_graph import directly_connected_tool_pair, node_map

AGENT_OUTPUT_PRODUCER_ID = "asaree.agent_output"
PYTHON_SCRIPT_PRODUCER_ID = "asaree.python_script"
MCP_TOOL_PRODUCER_ID = "asaree.mcp_tool"
REPORTED_PRODUCER_IDS = frozenset({AGENT_OUTPUT_PRODUCER_ID, PYTHON_SCRIPT_PRODUCER_ID, MCP_TOOL_PRODUCER_ID})
MCP_TOOL_NODE_TYPES = frozenset({"mcp_tool", "mcp_scikit_learn", "mcp_client_tool"})
_SCRIPT_SERVER = "asaree-script"
_SCRIPT_TOOL = "run_wired_script"


def _tool_matches(recorded: Any, configured: str) -> bool:
    return isinstance(recorded, str) and (recorded == configured or recorded.endswith(f".{configured}"))


def _script_name(graph: Mapping[str, Any], node_id: str) -> str:
    for node in graph.get("nodes") or ():
        if not isinstance(node, Mapping) or str(node.get("id")) != node_id:
            continue
        data = node.get("data") if isinstance(node.get("data"), Mapping) else {}
        config = data.get("config") if isinstance(data.get("config"), Mapping) else {}
        return str(config.get("name") or "")
    return ""


def _agent_script_ids(graph: Mapping[str, Any], agent_node_id: str) -> set[str]:
    return {
        str(edge.get("source"))
        for edge in graph.get("edges") or ()
        if isinstance(edge, Mapping)
        and str(edge.get("target")) == agent_node_id
        and edge.get("targetHandle") == "tool"
        and any(
            isinstance(node, Mapping)
            and str(node.get("id")) == str(edge.get("source"))
            and node.get("type") == "script"
            for node in graph.get("nodes") or ()
        )
    }


async def _mcp_server_name(binding: ProducerBinding) -> str | None:
    server_id = binding.config.get("server_id")
    try:
        server = await mcp_service.get_server(uuid.UUID(str(server_id)))
    except (TypeError, ValueError):
        return None
    name = getattr(server, "name", None) if server is not None else None
    return str(name) if name else None


async def _last_matching_call(
    run: ProtocolRun,
    binding: ProducerBinding,
    graph: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    agent_node_id = str(binding.config.get("agent_node_id") or "")
    node_run = (run.node_runs or {}).get(agent_node_id)
    agent_run_id = (
        node_run.get("last_successful_run_id") or node_run.get("run_id") if isinstance(node_run, Mapping) else None
    )
    try:
        steps = await get_run_steps(uuid.UUID(str(agent_run_id)))
    except (TypeError, ValueError):
        return None

    server_name: str | None = None
    if binding.producer_id == MCP_TOOL_PRODUCER_ID:
        server_name = await _mcp_server_name(binding)
        if server_name is None:
            return None

    matched: Mapping[str, Any] | None = None
    for step in sorted(
        steps,
        key=lambda item: (getattr(item, "iteration", 0), getattr(item, "sequence", 0)),
    ):
        for call in flatten_tool_call_records(getattr(step, "tool_call", None)):
            if binding.producer_id == MCP_TOOL_PRODUCER_ID:
                if not _tool_matches(call.get("tool"), str(binding.config.get("tool_name") or "")):
                    continue
                if server_name and call.get("server") != server_name:
                    continue
            else:
                if call.get("server") != _SCRIPT_SERVER or not _tool_matches(call.get("tool"), _SCRIPT_TOOL):
                    continue
                script_id = str(binding.config.get("script_node_id") or "")
                script_ids = _agent_script_ids(graph, agent_node_id)
                selector = (
                    (call.get("arguments") or {}).get("script") if isinstance(call.get("arguments"), Mapping) else None
                )
                if len(script_ids) > 1 and selector not in {script_id, _script_name(graph, script_id)}:
                    continue
            matched = call
    return matched


async def collect_reported_metrics(
    run: ProtocolRun,
    plan: MeasurementPlan,
    graph: Mapping[str, Any],
) -> MeasurementEvaluation:
    """Return one observation per declared custom metric without executing tools."""
    metrics = {metric.id: metric for metric in plan.metrics}
    observations: list[MetricObservation] = []
    attempt_id = str(run.id)
    for binding in plan.producers:
        if binding.producer_id not in REPORTED_PRODUCER_IDS:
            continue
        node_run = (run.node_runs or {}).get(str(binding.config.get("agent_node_id") or ""))
        successful_output = (
            node_run.get("last_successful_output_text", node_run.get("output_text"))
            if isinstance(node_run, Mapping)
            else None
        )
        agent_output_available = (
            binding.producer_id == AGENT_OUTPUT_PRODUCER_ID
            and isinstance(node_run, Mapping)
            and (node_run.get("status") == "completed" or "last_successful_output_text" in node_run)
            and successful_output is not None
        )
        call = (
            None if binding.producer_id == AGENT_OUTPUT_PRODUCER_ID else await _last_matching_call(run, binding, graph)
        )
        measured = agent_output_available or call is not None
        provenance = ProducerProvenance(
            binding_id=binding.id,
            producer_id=binding.producer_id,
            kind=binding.kind,
            version="2",
            binding_config=dict(binding.config),
            evaluation={
                "source": (
                    "agent_final_output" if binding.producer_id == AGENT_OUTPUT_PRODUCER_ID else "agent_tool_trace"
                ),
                **(
                    {
                        "tool_call_success": call.get("success"),
                        "tool_call_error_type": call.get("error_type"),
                    }
                    if call is not None
                    else {}
                ),
            },
        )
        for metric_id in binding.outputs.values():
            metric = metrics.get(metric_id)
            if metric is None:
                continue
            observations.append(
                MetricObservation(
                    metric_id=metric.id,
                    metric_name=metric.name,
                    value_type=metric.value_type,
                    value=(
                        successful_output
                        if agent_output_available
                        else call.get("result")
                        if call is not None
                        else None
                    ),
                    status="measured" if measured else "unavailable",
                    error=(
                        None
                        if measured
                        else "The Agent did not produce a completed final output."
                        if binding.producer_id == AGENT_OUTPUT_PRODUCER_ID
                        else "The Agent did not call the configured metric tool."
                    ),
                    attempt_id=attempt_id,
                    producer=provenance,
                    input_provenance={},
                )
            )
    return MeasurementEvaluation(
        replicate_id=str(run.replicate_result_id or run.id),
        attempt_id=attempt_id,
        observations=tuple(observations),
        artifacts=(),
    )


def resolve_reported_metric_graph(graph: Mapping[str, Any], factor_values: Mapping[str, Any]) -> dict[str, Any]:
    """Apply whole-Script factor bindings needed to identify recorded calls."""
    resolved = deepcopy(dict(graph))
    nodes = resolved.get("nodes")
    if not isinstance(nodes, list):
        return resolved
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "script":
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            continue
        bindings = data.get("factor_bindings")
        factor_name = bindings.get("config") if isinstance(bindings, Mapping) else None
        if isinstance(factor_name, str) and isinstance(factor_values.get(factor_name), Mapping):
            data["config"] = deepcopy(dict(factor_values[factor_name]))
    return resolved


def _node_data(node: Mapping[str, Any] | None) -> Mapping[str, Any]:
    data = node.get("data") if isinstance(node, Mapping) else None
    return data if isinstance(data, Mapping) else {}


def _node_config(node: Mapping[str, Any] | None) -> Mapping[str, Any]:
    config = _node_data(node).get("config")
    return config if isinstance(config, Mapping) else {}


async def _registered_server(server_id: Any, owner_id: uuid.UUID | None) -> Any:
    try:
        server = await mcp_service.get_server(uuid.UUID(str(server_id)))
    except (TypeError, ValueError):
        return None
    if server is None or owner_id is None:
        return server
    if getattr(server, "owner_id", None) == owner_id or getattr(server, "is_system", False) is True:
        return server
    return None


def _agent_issue(
    binding: ProducerBinding,
    index: int,
    graph: Mapping[str, Any],
    preserved_binding_ids: set[str] | None,
) -> ValidationIssue | None:
    path = f"producers[{index}].config.agent_node_id"
    agent_id = binding.config.get("agent_node_id")
    agent = node_map(graph).get(agent_id) if isinstance(agent_id, str) else None
    prefix = {
        AGENT_OUTPUT_PRODUCER_ID: "agent_output",
        PYTHON_SCRIPT_PRODUCER_ID: "python_script",
        MCP_TOOL_PRODUCER_ID: "mcp",
    }[binding.producer_id]
    severity = preserved_binding_severity(
        binding.id,
        preserved_binding_ids,
        has_preserved_value=isinstance(agent_id, str) and bool(agent_id),
    )
    if agent is None or agent.get("type") not in ("agent", "sub_agent"):
        return ValidationIssue(f"{prefix}_agent_missing", "The source Agent is unavailable.", path, severity)
    if _node_data(agent).get("active") is False:
        return ValidationIssue(f"{prefix}_agent_disabled", "The source Agent is disabled.", path, severity)
    return None


async def validate_reported_measurement_plan(
    plan: MeasurementPlan,
    graph: Mapping[str, Any],
    *,
    owner_id: uuid.UUID | None = None,
    preserved_binding_ids: set[str] | None = None,
) -> ValidationReport:
    """Validate reported-source identity and wiring without interpreting values."""
    issues: list[ValidationIssue] = []
    for index, binding in enumerate(plan.producers):
        if binding.producer_id not in REPORTED_PRODUCER_IDS:
            continue
        path = f"producers[{index}]"
        if agent_issue := _agent_issue(binding, index, graph, preserved_binding_ids):
            issues.append(agent_issue)
        if binding.producer_id == AGENT_OUTPUT_PRODUCER_ID:
            continue

        agent_id = str(binding.config.get("agent_node_id") or "")
        source_key = "script_node_id" if binding.producer_id == PYTHON_SCRIPT_PRODUCER_ID else "mcp_node_id"
        source_id = str(binding.config.get(source_key) or "")
        agent, source, connected = directly_connected_tool_pair(graph, agent_id, source_id)
        severity = preserved_binding_severity(
            binding.id,
            preserved_binding_ids,
            has_preserved_value=bool(source_id),
        )
        if binding.producer_id == PYTHON_SCRIPT_PRODUCER_ID:
            if source is None or source.get("type") != "script":
                issues.append(
                    ValidationIssue(
                        "python_script_node_missing",
                        "The Python Script is unavailable.",
                        f"{path}.config.script_node_id",
                        severity,
                    )
                )
            elif _node_config(source).get("enabled") is False:
                issues.append(
                    ValidationIssue(
                        "python_script_disabled",
                        "The Python Script is disabled.",
                        f"{path}.config.script_node_id",
                        severity,
                    )
                )
            elif not str(_node_config(source).get("code") or "").strip():
                issues.append(
                    ValidationIssue(
                        "python_script_empty",
                        "The Python Script has no code.",
                        f"{path}.config.script_node_id",
                    )
                )
            if agent is not None and source is not None and not connected:
                issues.append(
                    ValidationIssue(
                        "python_script_not_connected",
                        "The Agent and Python Script are no longer directly connected.",
                        f"{path}.config",
                        preserved_binding_severity(binding.id, preserved_binding_ids),
                    )
                )
            continue

        if source is None or source.get("type") not in MCP_TOOL_NODE_TYPES:
            issues.append(
                ValidationIssue(
                    "mcp_node_missing",
                    "The MCP Tool is unavailable.",
                    f"{path}.config.mcp_node_id",
                    severity,
                )
            )
            continue
        config = _node_config(source)
        if not connected:
            issues.append(
                ValidationIssue(
                    "mcp_not_connected",
                    "The Agent and MCP Tool are no longer directly connected.",
                    f"{path}.config",
                    preserved_binding_severity(binding.id, preserved_binding_ids),
                )
            )
        if config.get("enabled") is False:
            issues.append(
                ValidationIssue(
                    "mcp_disabled",
                    "The MCP Tool is disabled.",
                    f"{path}.config.mcp_node_id",
                    preserved_binding_severity(binding.id, preserved_binding_ids),
                )
            )
        if binding.config.get("server_id") != config.get("server_id"):
            issues.append(
                ValidationIssue(
                    "mcp_server_changed",
                    "The MCP Server configuration has changed.",
                    f"{path}.config.server_id",
                    preserved_binding_severity(binding.id, preserved_binding_ids),
                )
            )
        tool_name = binding.config.get("tool_name")
        if not isinstance(tool_name, str) or tool_name not in (config.get("tool_names") or ()):
            issues.append(
                ValidationIssue(
                    "mcp_tool_disabled",
                    "The selected MCP tool is no longer enabled.",
                    f"{path}.config.tool_name",
                    preserved_binding_severity(binding.id, preserved_binding_ids),
                )
            )
            continue
        server = await _registered_server(binding.config.get("server_id"), owner_id)
        if server is None:
            issues.append(
                ValidationIssue(
                    "mcp_server_missing",
                    "The registered MCP Server is unavailable.",
                    f"{path}.config.server_id",
                    preserved_binding_severity(binding.id, preserved_binding_ids),
                )
            )
            continue
        capabilities = getattr(server, "capabilities", None)
        raw_tools = capabilities.get("tools") if isinstance(capabilities, Mapping) else None
        tools: Sequence[Any] = raw_tools if isinstance(raw_tools, Sequence) and not isinstance(raw_tools, str) else ()
        if not any(isinstance(tool, Mapping) and tool.get("name") == tool_name for tool in tools):
            issues.append(
                ValidationIssue(
                    "mcp_tool_unavailable",
                    "The selected MCP tool is unavailable on this server.",
                    f"{path}.config.tool_name",
                    preserved_binding_severity(binding.id, preserved_binding_ids),
                )
            )
    return ValidationReport(tuple(issues))


__all__ = [
    "AGENT_OUTPUT_PRODUCER_ID",
    "MCP_TOOL_NODE_TYPES",
    "MCP_TOOL_PRODUCER_ID",
    "PYTHON_SCRIPT_PRODUCER_ID",
    "REPORTED_PRODUCER_IDS",
    "collect_reported_metrics",
    "resolve_reported_metric_graph",
    "validate_reported_measurement_plan",
]
