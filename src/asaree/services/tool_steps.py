"""Deterministic Tool Step nodes: one MCP tool call in the main flow, no LLM.

A Tool Step is the canvas form of a harness-owned call like the spinal
notebook's ``score_payload``: it takes the typed payload its upstream node
handed on, optionally coerces it through a harness sanitizer, and calls one
MCP tool directly with fixed arguments. Nothing about the call is left to a
model, so the same approved payload always produces the same call -- which is
the whole point of scoring through it rather than through an Agent that is
*asked* to make the call.

Two guards make that checkable after the fact, and both fail the node rather
than record a result that can't be trusted:

* ``code_sha256`` -- when a Script node is wired, the tool must report the
  hash of exactly that script's stripped source (it executed verbatim).
* ``payload_sha256`` -- the tool must report the hash of exactly the
  canonical payload JSON this step sent (nothing rewrote it on the way).
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

# The MI/spinal MLM prompt's closed hyperparameter vocabulary and hard bounds
# (the notebook's PARAM_SPEC). A Tool Step can override it via
# ``config.param_spec``; this is the default for the one sanitizer that exists.
XGBOOST_PARAM_SPEC: dict[str, dict[str, Any]] = {
    "n_estimators": {"type": "int", "log": False, "low": 100, "high": 1000},
    "max_depth": {"type": "int", "log": False, "low": 3, "high": 10},
    "learning_rate": {"type": "float", "log": True, "low": 1e-3, "high": 0.3},
    "min_child_weight": {"type": "float", "log": False, "low": 1, "high": 20},
    "gamma": {"type": "float", "log": False, "low": 0, "high": 5},
    "subsample": {"type": "float", "log": False, "low": 0.5, "high": 1.0},
    "colsample_bytree": {"type": "float", "log": False, "low": 0.5, "high": 1.0},
    "reg_lambda": {"type": "float", "log": True, "low": 1e-3, "high": 10},
    "reg_alpha": {"type": "float", "log": True, "low": 1e-3, "high": 10},
}
DEFAULT_N_TRIALS = 30
XGBOOST_SANITIZER = "xgboost_hyperparameters"
SANITIZERS = frozenset({XGBOOST_SANITIZER})


def canonical_payload_json(payload: Any) -> str:
    """Deterministic JSON serialization used for both the call and its hash guard."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(float(value))


def _sanitize_n_trials(value: Any, notes: list[str], default: int) -> int:
    if not _is_number(value):
        notes.append(f"n_trials was not a number ({value!r}); defaulted to {default}")
        return default
    rounded = int(round(float(value)))
    if rounded < 1:
        notes.append(f"n_trials {value} < 1; raised to 1")
        return 1
    if rounded > 100:
        notes.append(f"n_trials {value} > 100; capped at 100")
        return 100
    return rounded


def sanitize_xgboost_payload(
    payload: Any,
    param_spec: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    default_n_trials: int = DEFAULT_N_TRIALS,
) -> tuple[dict[str, Any], list[str]]:
    """Coerce an MLM hyperparameter payload into one the fixed script can run.

    A port of the spinal notebook's ``sanitize_payload``, applied uniformly to
    every arm: every suggestion the agent was not allowed to make is DROPPED
    (falling back to the script's default) and noted, so the replicate still
    scores instead of crashing on e.g. a ``"name"`` key where ``"param"`` was
    required, a harness-fixed ``objective`` in ``fixed_params``, or a log-scale
    ``low=0``. Returns ``(clean_payload, notes)``; notes is empty when the
    payload was already clean.
    """
    spec = param_spec or XGBOOST_PARAM_SPEC
    notes: list[str] = []
    if not isinstance(payload, Mapping):
        notes.append(
            f"payload was not a JSON object ({type(payload).__name__}); scored with all-default hyperparameters"
        )
        return {"search_space": [], "fixed_params": {}, "n_trials": default_n_trials}, notes

    def in_bounds(name: str, value: float) -> bool:
        return float(spec[name]["low"]) <= value <= float(spec[name]["high"])

    clean_search: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_search = payload.get("search_space")
    if isinstance(raw_search, list):
        for index, entry in enumerate(raw_search):
            where = f"search_space[{index}]"
            if not isinstance(entry, Mapping):
                notes.append(f"dropped {where}: not an object")
                continue
            name = entry.get("param")
            if name is None:
                notes.append(
                    f'dropped {where}: missing the "param" key (keys: {sorted(entry)}) -> that param uses its default'
                )
                continue
            if name not in spec:
                notes.append(
                    f"dropped {where}: {name!r} is not a tunable parameter -> not passed to the model "
                    "(uses its default)"
                )
                continue
            if name in seen:
                notes.append(f"dropped {where}: {name!r} already tuned earlier (duplicate)")
                continue
            low, high = entry.get("low"), entry.get("high")
            if not _is_number(low) or not _is_number(high):
                notes.append(f"dropped {where} ({name!r}): non-numeric low/high -> uses its default")
                continue
            if low >= high:
                notes.append(
                    f"dropped {where} ({name!r}): needs low < high, got low={low}, high={high} -> uses its default"
                )
                continue
            if not in_bounds(name, low) or not in_bounds(name, high):
                notes.append(
                    f"dropped {where} ({name!r}): range [{low}, {high}] outside its hard bound "
                    f"[{spec[name]['low']}, {spec[name]['high']}] -> uses its default"
                )
                continue
            seen.add(name)
            clean_search.append({"param": name, "low": low, "high": high})
    elif raw_search is not None:
        notes.append(f"ignored search_space: not a list ({type(raw_search).__name__})")

    clean_fixed: dict[str, Any] = {}
    raw_fixed = payload.get("fixed_params")
    if isinstance(raw_fixed, Mapping):
        for name, value in raw_fixed.items():
            if name not in spec:
                notes.append(
                    f"dropped fixed_params[{name!r}]: not a settable parameter (harness-fixed or unknown) "
                    "-> the script's own default stands"
                )
                continue
            if name in seen:
                notes.append(
                    f"dropped fixed_params[{name!r}]: already tuned in search_space (cannot be both) "
                    "-> kept the tuned range"
                )
                continue
            if not _is_number(value):
                notes.append(f"dropped fixed_params[{name!r}]: non-numeric value {value!r} -> uses its default")
                continue
            if not in_bounds(name, value):
                notes.append(
                    f"dropped fixed_params[{name!r}]={value}: outside its hard bound "
                    f"[{spec[name]['low']}, {spec[name]['high']}] -> uses its default"
                )
                continue
            clean_fixed[name] = value
    elif raw_fixed is not None:
        notes.append(f"ignored fixed_params: not an object ({type(raw_fixed).__name__})")

    n_trials = _sanitize_n_trials(payload.get("n_trials"), notes, default_n_trials)
    if not clean_search and n_trials > 1:
        notes.append(f"no tunable parameters survived; n_trials reduced {n_trials} -> 1")
        n_trials = 1
    return {"search_space": clean_search, "fixed_params": clean_fixed, "n_trials": n_trials}, notes


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
    sanitizer = config.get("sanitizer")
    if sanitizer and sanitizer not in SANITIZERS:
        return f"names an unknown sanitizer {sanitizer!r}"
    arguments = config.get("arguments")
    if arguments is not None and not isinstance(arguments, Mapping):
        return "has arguments that are not an object"
    return None


def _call_arguments(
    config: Mapping[str, Any],
    *,
    code: str | None,
    payload_json: str | None,
    workspace_id: str | None,
) -> dict[str, Any]:
    arguments = dict(config.get("arguments") or {})
    if code is not None:
        arguments[str(config.get("code_argument") or "code")] = code
    if payload_json is not None:
        arguments[str(config.get("payload_argument") or "payload_json")] = payload_json
    workspace_argument = config.get("workspace_argument", "workspace_id")
    if workspace_id and workspace_argument:
        arguments.setdefault(str(workspace_argument), workspace_id)
    return arguments


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
    crash or timeout mid-call still leaves the approved payload on record --
    the notebook's pre-scoring upsert.
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

    code: str | None = None
    if script_nodes:
        script_config = _node_config(script_nodes[0])
        if script_config.get("enabled") is False:
            return failed("Tool Step's Script is disabled.")
        code = str(script_config.get("code") or "")
        if not code.strip():
            return failed("Tool Step's Script has no code.")
        step["script_node_id"] = str(script_nodes[0].get("id"))
        step["code_sha256"] = sha256_text(code.strip())

    payload_json: str | None = None
    if config.get("payload_argument", "payload_json"):
        raw_payload = resolve_upstream_payload(upstream_runs)
        step["raw_payload"] = raw_payload
        payload: Any = raw_payload
        if config.get("sanitizer") == XGBOOST_SANITIZER:
            param_spec = config.get("param_spec") if isinstance(config.get("param_spec"), Mapping) else None
            default_n_trials = config.get("default_n_trials")
            payload, notes = sanitize_xgboost_payload(
                raw_payload,
                param_spec,
                default_n_trials=int(default_n_trials) if _is_number(default_n_trials) else DEFAULT_N_TRIALS,
            )
            step["sanitize_notes"] = notes
        elif raw_payload is None:
            return failed("Tool Step found no JSON payload in its upstream node's output.")
        payload_json = canonical_payload_json(payload)
        step["payload"] = payload
        step["payload_sha256"] = sha256_text(payload_json)

    arguments = _call_arguments(config, code=code, payload_json=payload_json, workspace_id=workspace_id)
    # The code and payload are already recorded above, by hash and by value.
    step["arguments"] = {
        key: value
        for key, value in arguments.items()
        if key not in {config.get("code_argument") or "code", config.get("payload_argument") or "payload_json"}
    }
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
    if config.get("verify_hashes", True):
        reported = result if isinstance(result, dict) else {}
        if "code_sha256" in step and reported.get("code_sha256") != step["code_sha256"]:
            return {
                **record,
                "status": "failed",
                "error": "Verbatim guard failed: the executed script is not the wired Script.",
            }
        if "payload_sha256" in step and reported.get("payload_sha256") != step["payload_sha256"]:
            return {
                **record,
                "status": "failed",
                "error": "Payload guard failed: the scored payload is not the payload this step sent.",
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
    "DEFAULT_N_TRIALS",
    "SANITIZERS",
    "TOOL_STEP_NODE_TYPES",
    "XGBOOST_PARAM_SPEC",
    "XGBOOST_SANITIZER",
    "canonical_payload_json",
    "execute_tool_step",
    "flatten_paths",
    "parse_json_object",
    "resolve_upstream_payload",
    "sanitize_xgboost_payload",
    "sha256_text",
    "validate_tool_step",
    "wired_tool_sources",
]
