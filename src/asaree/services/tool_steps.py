"""Deterministic Tool Step nodes: one MCP tool call in the main flow, no LLM.

A Tool Step is the canvas form of a harness-owned call like the spinal
notebook's ``score_payload``: nothing about the call is left to a model, so
the same upstream payload always produces the same call. It is deliberately
use-case agnostic -- it knows nothing about any tool's parameters. Each
argument the node sends names where its value comes from:

* ``{"source": "value", "value": ...}`` -- a fixed value.
* ``{"source": "upstream_payload", "format": "json_string" | "object"}`` --
  the typed payload the upstream node handed on, either as canonical JSON
  text or as the object itself. ``null`` when upstream produced none; whether
  that's acceptable is the tool's call, not the step's.
* ``{"source": "script_code"}`` -- the wired Script node's source.
* ``{"source": "workspace_id"}`` -- the experiment replicate's workspace.

An argument that isn't mapped isn't sent. Anything a tool needs to do to its
inputs (e.g. sanitizing a hyperparameter payload) is the tool's own job.

``hash_checks`` (``{result_field: argument_name}``) makes the call checkable
after the fact: the tool must report, in *result_field*, the SHA-256 of that
argument exactly as sent -- e.g. that the script it executed is verbatim the
wired one. A mismatch fails the node rather than record a result that can't be
trusted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeGuard

from motoro.services import mcp_service

TOOL_STEP_NODE_TYPES = frozenset({"tool_step"})
_MCP_TOOL_NODE_TYPES = frozenset({"mcp_tool", "mcp_scikit_learn", "mcp_client_tool"})
ARGUMENT_SOURCES = frozenset({"value", "upstream_payload", "script_code", "workspace_id"})
PAYLOAD_FORMATS = frozenset({"json_string", "object"})


def canonical_payload_json(payload: Any) -> str:
    """Deterministic JSON serialization used for both the call and its hash check."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(float(value))


_FENCED_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def parse_json_object(text: str | None) -> dict[str, Any] | None:
    """Best-effort read of a JSON object out of agent prose.

    Last fenced block first (an agent's final answer), then the whole text, then
    the outermost ``{...}`` span. ``None`` when nothing parses to an object.
    """
    if not text:
        return None
    candidates = [block.strip() for block in reversed(_FENCED_BLOCK.findall(text))]
    stripped = text.strip()
    candidates.append(stripped)
    start, end = stripped.find("{"), stripped.rfind("}")
    if 0 <= start < end:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def resolve_upstream_payload(upstream_runs: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The typed payload the step consumes: the Output-Parser payload the
    upstream node (or the gate forwarding it) handed on, else one scraped from
    its prose -- the notebook's ``resolve_payload``."""
    for run in upstream_runs:
        payload = run.get("payload")
        if isinstance(payload, dict) and payload:
            return payload
    for run in upstream_runs:
        parsed = parse_json_object(run.get("output_text"))
        if parsed:
            return parsed
    return None


def _node_config(node: Mapping[str, Any] | None) -> Mapping[str, Any]:
    data = node.get("data") if isinstance(node, Mapping) else None
    config = data.get("config") if isinstance(data, Mapping) else None
    return config if isinstance(config, Mapping) else {}


def wired_tool_sources(
    graph: Mapping[str, Any], node_id: str
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """``(mcp_nodes, script_nodes)`` wired into the step's Tool connector, in wiring order."""
    nodes = {str(node.get("id")): node for node in graph.get("nodes") or () if isinstance(node, Mapping)}
    mcp_nodes: list[Mapping[str, Any]] = []
    script_nodes: list[Mapping[str, Any]] = []
    for edge in graph.get("edges") or ():
        if str(edge.get("target")) != node_id or edge.get("targetHandle") != "tool":
            continue
        source = nodes.get(str(edge.get("source")))
        if source is None:
            continue
        if source.get("type") in _MCP_TOOL_NODE_TYPES:
            mcp_nodes.append(source)
        elif source.get("type") == "script":
            script_nodes.append(source)
    return mcp_nodes, script_nodes


def validate_tool_step(graph: Mapping[str, Any], node: Mapping[str, Any]) -> str | None:
    """Why this Tool Step can't run, or ``None``. Shared by graph validation and execution."""
    node_id = str(node.get("id"))
    config = _node_config(node)
    mcp_nodes, script_nodes = wired_tool_sources(graph, node_id)
    tool_edges = [
        edge
        for edge in graph.get("edges") or ()
        if str(edge.get("target")) == node_id and edge.get("targetHandle") == "tool"
    ]
    if len(tool_edges) != len(mcp_nodes) + len(script_nodes):
        return "can only take MCP Tool and Script nodes on its Tool connection"
    if len(mcp_nodes) != 1:
        return f"needs exactly one MCP Tool connection (found {len(mcp_nodes)})"
    if len(script_nodes) > 1:
        return f"can have at most one Script connection (found {len(script_nodes)})"
    tool_name = config.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        return "has no tool selected"
    mcp_config = _node_config(mcp_nodes[0])
    if mcp_config.get("enabled") is False:
        return "is wired to a disabled MCP Tool"
    if tool_name not in (mcp_config.get("tool_names") or ()):
        return f"calls {tool_name!r}, which its MCP Tool node does not enable"
    arguments = config.get("arguments") or {}
    if not isinstance(arguments, Mapping):
        return "has arguments that are not an object"
    if "sanitizer" in config or "verify_hashes" in config:
        return (
            "uses the old Tool Step format (sanitizer/verify_hashes); re-import the experiment definition "
            "or re-map its arguments in the Tool Step inspector, then publish"
        )
    for name, spec in arguments.items():
        source = spec.get("source") if isinstance(spec, Mapping) else None
        if source not in ARGUMENT_SOURCES:
            return f"argument {name!r} has no valid source"
        if source == "upstream_payload" and spec.get("format", "json_string") not in PAYLOAD_FORMATS:
            return f"argument {name!r} has an unknown payload format {spec.get('format')!r}"
        if source == "script_code" and not script_nodes:
            return f"argument {name!r} takes the Script's code, but no Script is wired"
    hash_checks = config.get("hash_checks") or {}
    if not isinstance(hash_checks, Mapping):
        return "has hash checks that are not an object"
    for field, argument in hash_checks.items():
        if argument not in arguments:
            return f"hash check {field!r} names {argument!r}, which is not a sent argument"
    return None


def _argument_text(value: Any) -> str:
    """The exact string a hash check hashes: strings as sent, anything else as canonical JSON."""
    return value if isinstance(value, str) else canonical_payload_json(value)


async def execute_tool_step(
    node: Mapping[str, Any],
    *,
    graph: Mapping[str, Any],
    upstream_runs: list[Mapping[str, Any]],
    workspace_id: str | None,
    record_provenance: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Run one Tool Step and return its node-run record.

    The record carries the parsed tool result as ``payload`` (so downstream
    nodes and the ``asaree.tool_step`` metric producer read it like any typed
    handoff) and the step's own provenance under ``tool_step``.
    *record_provenance* is awaited with that provenance BEFORE the call, so a
    crash or timeout mid-call still leaves what was sent on record -- the
    notebook's pre-scoring upsert.
    """
    node_id = str(node.get("id"))
    config = _node_config(node)
    step: dict[str, Any] = {"tool_name": config.get("tool_name")}

    def failed(message: str) -> dict[str, Any]:
        return {"status": "failed", "output_text": None, "error": message, "run_id": None, "tool_step": step}

    if problem := validate_tool_step(graph, node):
        return failed(f"Tool Step {problem}.")
    mcp_nodes, script_nodes = wired_tool_sources(graph, node_id)
    mcp_config = _node_config(mcp_nodes[0])
    step["server_id"] = mcp_config.get("server_id")
    step["server_name"] = mcp_config.get("server_name")

    arguments: dict[str, Any] = {}
    # Large or sensitive inputs (script source, upstream payload) are recorded
    # once by value/hash below rather than again inside `arguments`.
    recorded_arguments: dict[str, Any] = {}
    argument_sha256: dict[str, str] = {}
    for name, spec in (config.get("arguments") or {}).items():
        source = spec["source"]
        if source == "value":
            arguments[name] = spec.get("value")
            recorded_arguments[name] = arguments[name]
        elif source == "workspace_id":
            if not workspace_id:
                return failed(f"Tool Step argument {name!r} needs a workspace, and this run has none.")
            arguments[name] = workspace_id
            recorded_arguments[name] = workspace_id
        elif source == "script_code":
            script_config = _node_config(script_nodes[0])
            if script_config.get("enabled") is False:
                return failed("Tool Step's Script is disabled.")
            code = str(script_config.get("code") or "").strip()
            if not code:
                return failed("Tool Step's Script has no code.")
            step["script_node_id"] = str(script_nodes[0].get("id"))
            arguments[name] = code
        else:
            payload = resolve_upstream_payload(upstream_runs)
            step["payload"] = payload
            arguments[name] = (
                payload if spec.get("format", "json_string") == "object" else canonical_payload_json(payload)
            )
        if source in {"script_code", "upstream_payload"}:
            argument_sha256[name] = sha256_text(_argument_text(arguments[name]))
    step["arguments"] = recorded_arguments
    step["argument_sha256"] = argument_sha256
    if record_provenance is not None:
        await record_provenance(dict(step))

    try:
        server_id = uuid.UUID(str(step["server_id"]))
    except (TypeError, ValueError):
        return failed("Tool Step's MCP Tool has no registered server.")
    timeout = config.get("timeout_seconds")
    try:
        call = mcp_service.call_server_tool(server_id, str(config["tool_name"]), arguments)
        outcome = await (asyncio.wait_for(call, float(timeout)) if _is_number(timeout) else call)
    except TimeoutError:
        return failed(f"Tool Step's call to {config['tool_name']!r} timed out after {timeout}s.")
    except RuntimeError as exc:
        return failed(f"Tool Step could not call {config['tool_name']!r}: {exc}")
    if outcome is None:
        return failed("Tool Step's MCP server is not registered.")
    is_error, content = outcome
    record: dict[str, Any] = {
        "status": "completed",
        "output_text": content,
        "error": None,
        "run_id": None,
        "tool_step": step,
    }
    if is_error:
        return {**record, "status": "failed", "error": f"{config['tool_name']} returned an error: {content[:2000]}"}
    try:
        result = json.loads(content)
    except (ValueError, TypeError):
        result = None
    if isinstance(result, dict):
        record["payload"] = result
        if result.get("error"):
            return {**record, "status": "failed", "error": f"{config['tool_name']} error: {result['error']}"}
    reported = result if isinstance(result, dict) else {}
    for field, argument in (config.get("hash_checks") or {}).items():
        expected = argument_sha256.get(argument) or sha256_text(_argument_text(arguments.get(argument)))
        if reported.get(field) != expected:
            return {
                **record,
                "status": "failed",
                "error": f"Hash check failed: the tool's {field!r} does not match the {argument!r} this step sent.",
            }
    return record


def _flatten(value: Any, prefix: str, into: dict[str, Any]) -> None:
    if prefix:
        into[prefix] = value
    if isinstance(value, Mapping):
        for key, child in value.items():
            _flatten(child, f"{prefix}.{key}" if prefix else str(key), into)


def flatten_paths(document: Any) -> dict[str, Any]:
    """Every node of *document* keyed by its dotted path.

    Exact-key lookup rather than a split on ``.``: result keys like
    ``metrics_at_0.5`` contain dots themselves, so ``test_metrics.metrics_at_0.5.f1``
    is only unambiguous as a whole string.
    """
    paths: dict[str, Any] = {}
    _flatten(document, "", paths)
    return paths


__all__ = [
    "ARGUMENT_SOURCES",
    "PAYLOAD_FORMATS",
    "TOOL_STEP_NODE_TYPES",
    "canonical_payload_json",
    "execute_tool_step",
    "flatten_paths",
    "parse_json_object",
    "resolve_upstream_payload",
    "sha256_text",
    "validate_tool_step",
    "wired_tool_sources",
]
