"""Compiling and running a Protocol's graph.

``topological_order`` is pure (no DB, no network) -- validated by unit tests
alone. ``run_protocol`` is the orchestrator, meant to run inside the arq
worker (see ``asaree.worker.tasks.execute_protocol_run_task``), calling
Motoro's runner functions directly -- the same "direct call, not a
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
import contextlib
import copy
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from asaree_workspace_core import WORKSPACE_ROOT
from motoro.mcp.registry import get_registry
from motoro.models.run import RunStatus
from motoro.runner import create_agent, create_run, execute_run, get_agent_by_name, get_run, update_agent
from motoro.schemas.agent import ModelConfig
from motoro.schemas.output import parse_envelope
from motoro.schemas.pattern import PatternConfig
from motoro.services.mcp_service import hydrate_registry
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.config import get_settings
from asaree.models.database import get_session
from asaree.models.protocol_run import ProtocolRun
from asaree.services.dataset_workspaces import WorkspaceSeedError, seed_cell_workspace
from asaree.services.experiments import get_experiment
from asaree.services.factorial_cells import get_cell, list_cells, upsert_cell
from asaree.services.metric_promotion import promote_cell_score_metrics
from asaree.services.protocol_runs import (
    create_protocol_run,
    get_cancel_requested_at,
    get_protocol_run,
    set_status,
    update_node_run,
)
from asaree.services.protocols import get_protocol
from asaree.services.run_tools import gather_tools
from asaree.services.system_mcp_servers import WORKSPACE_AGENT_TOOLS, WORKSPACE_SERVER_NAME

logger = logging.getLogger(__name__)

# Internal sentinel for _run_agent_node/_run_critic's `error` return slot --
# never a real error message, so callers can check `error == _AGENT_CANCELLED`
# unambiguously to record a node as "cancelled" rather than "failed". A
# cancelled Motoro run has finished.error == None (see
# motoro.runner.execute_run's own write-back), so without this
# sentinel a mid-run Stop would silently look identical to a normal
# completion with an empty output -- this is what actually distinguishes it.
_AGENT_CANCELLED = "__cancelled__"

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


# ---------------------------------------------------------------------------
# Adding a connector? Pick its route first.
# ---------------------------------------------------------------------------
# Every connector contributes exactly one of three things, and which one it is
# decides where it goes. Getting this wrong is how something ends up narrated
# into a prompt that should never have been in the context window at all. The
# same three routes are written up from the engine's side in Motoro's
# ``engine/sense.py`` -- read that docstring alongside this one.
#
# 1. CAPABILITY -- what the agent can DO: the model, the execution pattern, the
#    tool allow-list, knowledge servers, skills. Route: resolve it into the
#    agent's stored config (``_resolve_llm_config``, ``_resolve_tool_config``,
#    ``_resolve_pattern_config``, ``_resolve_skill_config``, ...) and let
#    Motoro carry it on ``RunContext``. Never prompt text: a capability is
#    something the runtime arranges, not something the model is told about.
#
# 2. REFERENCE -- an id pointing at data held somewhere else: the workspace,
#    a registered dataset, an artifact path. Route: ``_ambient_meta_for``, which
#    Motoro binds into every MCP tool call's request ``_meta``. The model never
#    sees it and so can never mistype it; the tool loads the contents on demand.
#    The Dataset connector is the worked example -- it used to spell out an
#    ``open_workspace(...)`` call in the prompt and hope for a clean
#    transcription, and now the tool takes no arguments at all.
#
# 3. CONTENT -- text that genuinely belongs in the prompt and is small enough to
#    live there: the node's own prompt, an upstream node's ``output_text``.
#    Route: ``_build_user_input``. This is the only route that costs context
#    window on every single turn, so it is the last resort, not the default.
#
# A new connector is almost always 1 or 2. If it seems to be 3, check whether
# what you actually have is a reference to something a tool could fetch.
#
# One connector may take more than one route -- Dataset takes all three. It is a
# REFERENCE (its name, bound ambiently), it implies a CAPABILITY (the
# asaree-workspace tools, granted by ``_resolve_dataset_tool_config`` -- wiring
# the data is what declares that this agent works on data, so the user should
# not also have to wire the tools to do it), and it leaves one line of CONTENT
# behind: the fact that the data is there, which no tool call can tell an agent
# that never thinks to look.
#
# The connector-typed slots on an agent/critic_gate node. ai/tool/memory are
# a deliberately closed set; architectural_pattern and dataset are
# ASAREE-specific -- architectural_pattern for ARES's pluggable
# architectural patterns, dataset for the data an agent operates ON as
# opposed to the capabilities it operates WITH -- visual/
# validation scaffolding only for now, same deliberate non-implementation as
# "memory" (see ArchitecturalPatternNodeData on the frontend). Reuses
# ProtocolEdge's existing sourceHandle/targetHandle fields rather than adding
# a new "connection type" concept. A "main" edge (today's plain pipeline
# data-flow) is any edge whose targetHandle is one of these -- everything
# else. The type marker always lives on the target side of an edge.
#
# "llm" and "resource" are in here purely as pre-rename spellings of "ai" and
# "dataset" (see _LEGACY_AI_HANDLES/_LEGACY_DATASET_HANDLES): an un-migrated
# edge must still be recognised as a connector, or it would be misread as a
# main pipeline edge and turn a perfectly good graph into a cycle/ordering
# error.
_CONNECTOR_HANDLES = frozenset(
    {"ai", "llm", "tool", "memory", "architectural_pattern", "dataset", "resource", "skill", "knowledge"}
)

# Each connector slot accepts a FAMILY of node types, not one exact type --
# mirrors how "tool" already accepts any mcp_tool node. LLM and Architectural
# Pattern are each split one-node-per-provider/pattern -- a dedicated node per
# capability rather than one generic node with an internal picker -- instead of
# a single generic node with a Provider/kind field -- config shape is identical
# across LLM providers (provider is baked into the node type instead of a
# user-editable field), but genuinely differs per architectural pattern (see
# each pattern's own NodeConfig on the frontend), so the LLM family shares
# one inspector while each pattern gets its own.
_LLM_NODE_TYPES = frozenset({"llm_anthropic", "llm_openai", "llm_azure_foundry"})
# Only two builtin execution patterns exist in Motoro today
# (engine/patterns/builtin/) -- PatternConfig already has unused slots for
# safety_patterns/coordination_pattern/knowledge_patterns/quality_patterns/
# routing_pattern/resolution_patterns, which are *lists* (several active at
# once is meaningful for those), unlike execution_pattern which is a single
# value -- an agent can't run two execution loops at once. So this set is
# named for its category specifically: the "at most one" cap below
# (_validate_pattern_connections) only ever counts members of THIS set, not
# the whole Architectural Pattern connector -- a future safety/quality
# pattern node type would get its own set and its own (probably unbounded)
# cap, coexisting freely with one connected execution-pattern node.
_EXECUTION_PATTERN_NODE_TYPES = frozenset({"pattern_reason_act", "pattern_single_agent_baseline"})
_MEMORY_NODE_TYPES = frozenset({"memory"})  # one today; kept as a set for symmetry with the other two families
# An MCP-tool node is always a Tool-connector source (one server connection,
# allow-listing a subset of its tools) -- it never gets its own execution
# turn, matching every other connector-source family. There's no more
# "standalone pipeline step" role: calling one tool directly with no agent
# isn't supported (a real, deliberate feature removal -- see the removed
# _run_mcp_tool_node).
#
# Two types, one identical config shape (McpToolNodeConfig on the frontend:
# server_id/server_name/tool_names/enabled), so everything downstream --
# _resolve_tool_config included -- treats them interchangeably and only this
# set has to know both exist. They differ purely in how the server gets
# chosen: "mcp_tool" is the generic node whose inspector has a server
# dropdown, "mcp_scikit_learn" is a node dedicated to one specific server,
# picked from the canvas's MCP Servers browser and pinned at creation, and
# "mcp_client_tool" is a server the user REGISTERED from that browser (a
# stdio command or a streamable-HTTP URL they typed) rather than one the
# deployment already had. A future dedicated node for another server joins
# this set the same way.
_MCP_TOOL_NODE_TYPES = frozenset({"mcp_tool", "mcp_scikit_learn", "mcp_client_tool"})
# One today each; kept as sets for symmetry with the other connector
# families. Dataset declares which registered dataset an agent's workspace
# tools operate on (_resolve_dataset_configs, folded into _build_user_input's
# own "Dataset context" block); Script carries a fixed piece of code an
# agent passes verbatim as some tool's own code-shaped argument (e.g.
# run_model_script's `code`) -- neither is executed by ASAREE itself, same
# "pure config source" status as every other connector.
_DATASET_NODE_TYPES = frozenset({"dataset"})
_SCRIPT_NODE_TYPES = frozenset({"script"})
# A Skill node names one registered Agent Skill (a SKILL.md document stored in
# core -- see motoro.models.skill.Skill and asaree.api.skills). Unlike Dataset
# and Script it gets its OWN connector slot rather than sharing Tool's, because
# core has a real slot for it: skill_config is an agent capability axis
# alongside model/tool/memory/pattern, resolved by Motoro into the run's own
# skill index (_resolve_skill_config below), not folded into the prompt by
# ASAREE. Repeatable and uncapped, like Tool -- carrying five skills is the
# normal case, since level-1 metadata is ~100 tokens each and a body only
# loads when the model asks for it.
_SKILL_NODE_TYPES = frozenset({"skill"})
# An OKF Bundle node names one registered OKF bundle -- a directory of
# markdown concepts the user pointed ASAREE at, served by its own MCP server
# process (see asaree.services.okf_bundles for why it's a server per bundle
# and not a path argument). It gets its own Knowledge connector rather than
# sharing Tool's, because what it contributes is a *knowledge base the agent
# reads and writes*, not one more capability: the distinction the canvas is
# making is the same one OKF itself makes, and burying a bundle among five
# MCP servers on the Tool slot would lose it.
#
# Mechanically, though, it IS an MCP server, so it resolves into the very same
# tool_config as the Tool connector (_resolve_knowledge_config, merged in
# _run_agent_node) -- the split is at the level of what the user is saying,
# not how the run consumes it. Repeatable and uncapped, like Skill and Tool:
# an agent may legitimately read from a shared team bundle and write to its
# own.
_OKF_BUNDLE_NODE_TYPES = frozenset({"okf_bundle"})
# An OKF Document node names one UPLOADED single-concept document (see
# asaree.services.okf_documents). Mechanically identical to a bundle node --
# ASAREE stores the upload as a one-concept bundle directory and serves it
# with the same per-bundle OKF server, so the node carries the same
# server_name/tool_names and resolves through the same code path. It's a
# separate node type purely because the two answer different questions on the
# canvas: "point at knowledge the server already has" versus "here is a
# concept file from my machine". Same split, same reason, as picking a
# registered MCP server versus registering your own.
_OKF_DOCUMENT_NODE_TYPES = frozenset({"okf_document"})
# The Knowledge connector's whole family -- what that slot accepts, and what
# _resolve_knowledge_config reads. Everything downstream treats the two
# interchangeably, so only this union has to know both exist.
_KNOWLEDGE_NODE_TYPES = _OKF_BUNDLE_NODE_TYPES | _OKF_DOCUMENT_NODE_TYPES

# Every node type that's a pure config source -- never gets its own execution
# turn, never a pipeline "final output" (see sink_node_ids/run_protocol's
# main loop), and may only ever emit its own connector-typed edge (see the
# "outgoing wrong handle" check in topological_order below).
_PURE_CONFIG_SOURCE_TYPES = (
    _LLM_NODE_TYPES
    | _EXECUTION_PATTERN_NODE_TYPES
    | _MEMORY_NODE_TYPES
    | _MCP_TOOL_NODE_TYPES
    | _DATASET_NODE_TYPES
    | _SCRIPT_NODE_TYPES
    | _SKILL_NODE_TYPES
    | _KNOWLEDGE_NODE_TYPES
)

# Which connector handle each pure-config-source node type may exclusively
# emit into, and the human-facing label for that handle -- both keyed off
# the same family grouping so a new provider/pattern node type only needs
# adding to _LLM_NODE_TYPES/_EXECUTION_PATTERN_NODE_TYPES above, not a
# second lookup.
_NODE_TYPE_TO_HANDLE: dict[str, str] = {
    **{t: "ai" for t in _LLM_NODE_TYPES},
    **{t: "architectural_pattern" for t in _EXECUTION_PATTERN_NODE_TYPES},
    **{t: "memory" for t in _MEMORY_NODE_TYPES},
    # Script still shares the Tool connector rather than getting its own slot
    # -- one connector accepting a FAMILY of node types (see this dict's own
    # docstring above _LLM_NODE_TYPES). Both are pure config sources an
    # agent's Tool "+" panel can add (AddNodePanel filters its catalog by
    # CONNECTOR_PANEL_INFO.tool's allowedTypes on the frontend); which one a
    # given wired node actually IS is recovered by checking the source node's
    # own `type`, not by which handle it's on (see _resolve_tool_config/
    # _resolve_script_config, and the per-agent validation block below).
    #
    # Dataset used to be in that same shared bucket and no longer is: it has
    # its own slot, named after the node type itself since `dataset` is the
    # only member of the family.
    **{t: "tool" for t in _MCP_TOOL_NODE_TYPES},
    **{t: "dataset" for t in _DATASET_NODE_TYPES},
    **{t: "tool" for t in _SCRIPT_NODE_TYPES},
    **{t: "skill" for t in _SKILL_NODE_TYPES},
    **{t: "knowledge" for t in _KNOWLEDGE_NODE_TYPES},
}
# The user-facing name of each connector slot -- mirrors
# CONNECTOR_SLOT_LABELS on the frontend, so a validation error always names
# the connector by the caption printed next to it on the canvas.
_HANDLE_LABELS: dict[str, str] = {
    "ai": "AI",
    "llm": "AI",  # pre-rename spelling, same slot -- see _LEGACY_AI_HANDLES
    "memory": "Memory",
    "architectural_pattern": "Architectural Pattern",
    "tool": "Tool",
    "dataset": "Dataset",
    "resource": "Dataset",  # pre-rename spelling, same slot -- see _LEGACY_DATASET_HANDLES
    "skill": "Skill",
    "knowledge": "Knowledge",
}

# Connector slots have been renamed twice since graphs started being saved,
# and a stored graph is an opaque JSONB blob, so every spelling has to keep
# resolving:
#
#   "llm" -> "ai"       the AI connector (its caption was renamed first, the
#                       handle id after -- migration 3f1a7c9b2e04)
#   "tool" -> "resource" for a Dataset source, when Dataset stopped sharing
#                       the Tool slot (same migration)
#   "resource" -> "dataset"  when that slot, whose only member is the Dataset
#                       node, was renamed after it and moved next to Skill
#                       (migration b7c2d9e14a35)
#
# Those migrations rewrite every stored graph, and the canvas rewrites any
# graph it opens (migrateLegacyHandles in ProtocolCanvas.tsx), so these sets
# are not load-bearing for data at rest. They exist so the deploy is
# ORDER-INDEPENDENT: a browser still running pre-rename JS keeps autosaving
# old-spelling edges at whatever moment the new backend goes live, and an
# SDK/notebook caller pinned to an older graph shape keeps working. Nothing
# creates an old-spelling edge going forward -- isValidConnection won't.
_LEGACY_AI_HANDLES = frozenset({"ai", "llm"})
_LEGACY_DATASET_HANDLES = frozenset({"dataset", "resource", "tool"})
# Keyed by the CURRENT slot id -- every spelling an edge into that slot may
# legitimately still carry *on the handle alone*, i.e. every rename that was
# TOTAL. "llm" and "resource" both qualify: no other slot has ever used
# either, so an old-spelling edge can be resolved without looking at its
# source node. "tool" does not -- it still means the Tool slot for
# mcp_tool/Script sources, so a pre-Resource dataset edge can only be picked
# out by ALSO checking its source node's type, which is why the wider
# _LEGACY_DATASET_HANDLES is applied at its own call sites instead.
_LEGACY_HANDLES_BY_SLOT: dict[str, frozenset[str]] = {
    "ai": _LEGACY_AI_HANDLES,
    "dataset": frozenset({"dataset", "resource"}),
}

# node type -> Motoro PatternConfig slug, for _resolve_pattern_config.
_EXECUTION_PATTERN_SLUGS: dict[str, str] = {
    "pattern_reason_act": "reason_act",
    "pattern_single_agent_baseline": "single_agent_baseline",
}

# design_spec.coordination_strategy.slug -- an EXPERIMENT-level declaration
# (ResearchExperiment.design_spec, edited from the Design tab), not a canvas
# connector node the way an execution pattern is: this is a multi-agent-
# system concern, not one agent's own architectural pattern. "sequential"
# (absent/default) is a no-op -- today's exact existing DAG-handoff
# behavior, unchanged. "critic_gate" promotes the existing gated-pair
# mechanism (find_gated_pairs/_run_gated_worker, unchanged) from purely
# implicit-in-the-graph to an explicit, checked declaration: the graph must
# actually contain a gated pair, or the declared intent doesn't match
# reality. The rest mirror ARES's own coordination-category patterns
# (supervisor/swarm/task-bidding/supervision-tree/event-driven/multi-agent-
# planning) -- named placeholders pending a later ARES -> Motoro
# migration (the user's own call), matching this codebase's "declares
# intent, no runtime effect yet" posture for Memory nodes -- except a
# placeholder coordination strategy is REJECTED at run time rather than
# silently inert, since (unlike a Memory node) choosing one is a claim about
# how the whole graph runs, not a connector with no effect either way.
_PLACEHOLDER_COORDINATION_STRATEGIES = frozenset(
    {
        "supervisor_architecture",
        "swarm_architecture",
        "task_bidding",
        "supervision_tree_with_guarded_capabilities",
        "event_driven_reactivity",
        "multi_agent_planning",
    }
)


def validate_coordination_strategy(design_spec: dict[str, Any] | None, *, has_gated_pair: bool) -> None:
    slug = ((design_spec or {}).get("coordination_strategy") or {}).get("slug") or "sequential"
    if slug == "sequential":
        return
    if slug == "critic_gate":
        if not has_gated_pair:
            raise ProtocolValidationError(
                "This experiment's coordination strategy is 'Critic Gate' but this protocol has no Critic Gate "
                "node wired in -- add one, or change the coordination strategy on the Design tab."
            )
        return
    if slug in _PLACEHOLDER_COORDINATION_STRATEGIES:
        raise ProtocolValidationError(
            f"Coordination strategy {slug!r} isn't implemented yet -- coming with the ARES pattern migration."
        )
    raise ProtocolValidationError(f"Unknown coordination strategy: {slug!r}")


_NODE_TYPE_DISPLAY_NAMES: dict[str, str] = {
    "agent": "Agent",
    "critic_gate": "Critic Gate",
    "mcp_tool": "MCP Tool",
    "mcp_scikit_learn": "Scikit-learn MCP",
    "mcp_client_tool": "MCP Client Tool",
    "memory": "Memory",
    "dataset": "Dataset",
    "script": "Script",
    "skill": "Skill",
    "okf_bundle": "OKF Bundle",
    "okf_document": "OKF Document",
    "pattern_reason_act": "Reason + Act",
    "pattern_single_agent_baseline": "Single-Agent Baseline",
    "llm_anthropic": "Anthropic",
    "llm_openai": "OpenAI",
    "llm_azure_foundry": "Azure AI Foundry",
}


def _node_display_name(node: dict[str, Any]) -> str:
    """A validation-error-friendly name for a node -- its canvas label if the
    user has set one (matching what they'd actually see in the inspector
    header/on the card), else the same placeholder text the frontend shows
    for an unnamed node of that type (EditableNodeTitle's own `placeholder`
    prop, or the provider label for the three LLM node types). Never the
    bare internal node id -- that's graph bookkeeping (see newNodeId on the
    frontend), meaningless to a user reading a failed-validation message."""
    data = node.get("data")
    label = data.get("label") if isinstance(data, dict) else None
    if isinstance(label, str) and label:
        return label
    node_type = node.get("type")
    if isinstance(node_type, str):
        return _NODE_TYPE_DISPLAY_NAMES.get(node_type, node_type)
    node_id = node.get("id")
    return node_id if isinstance(node_id, str) else "node"


def _default_system_prompt(label: str | None, placeholder: str) -> str:
    """ASAREE's own explicit default for a blank System Prompt field --
    used in place of just passing Motoro's own create_agent/
    update_agent an empty string, which would otherwise fall back to
    ``f"You are {name}. {description}"`` using `agent_name`, an internal
    "protocol-{protocol_id}-{node_id}" bookkeeping id no user ever sees,
    not this node's own canvas identity. `placeholder` matches whichever
    fallback text the node's own canvas card already shows when unlabeled
    (AgentNode.tsx's "Agent", CriticGateNode.tsx's "Critic Gate"), so an
    unlabeled, unconfigured node's default prompt still reads sensibly."""
    return f"You are {label or placeholder}."


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
        # Main-pipeline incoming edges only -- a gate's own LLM connector
        # edge is a separate concept (validated below) and must not count
        # towards "how many things feed this gate on the main pipeline."
        ups = _upstream_ids(graph, nid)
        if len(ups) != 1:
            raise ProtocolValidationError(
                f"Critic Gate node {_node_display_name(node)!r} must have exactly one incoming connection "
                f"(found {len(ups)})."
            )
        worker_id = ups[0]
        worker_node = nodes[worker_id]
        if worker_node.get("type") != "agent":
            raise ProtocolValidationError(
                f"Critic Gate node {_node_display_name(node)!r}'s incoming connection must come from an Agent node."
            )
        if len(downstream[worker_id]) != 1:
            raise ProtocolValidationError(
                f"Agent node {_node_display_name(worker_node)!r} is gated by a Critic Gate and can't have any "
                "other outgoing connections."
            )
        if not _is_node_active(worker_node):
            raise ProtocolValidationError(
                f"Agent node {_node_display_name(worker_node)!r} is gated by a Critic Gate and can't be "
                "deactivated -- deactivate the Critic Gate instead."
            )

    for nid, node in nodes.items():
        node_type = node.get("type")
        name = _node_display_name(node)

        if node_type in ("agent", "critic_gate"):
            llm_edges = _edges_with_handle(graph, nid, "ai", direction="incoming")
            if len(llm_edges) != 1:
                raise ProtocolValidationError(
                    f"Node {name!r} must have exactly one AI connection (found {len(llm_edges)})."
                )
            llm_source = nodes.get(llm_edges[0]["source"])
            if llm_source is None or llm_source.get("type") not in _LLM_NODE_TYPES:
                raise ProtocolValidationError(f"Node {name!r}'s AI connection must come from an AI node.")

        tool_edges = _edges_with_handle(graph, nid, "tool", direction="incoming")
        memory_edges = _edges_with_handle(graph, nid, "memory", direction="incoming")
        pattern_edges = _edges_with_handle(graph, nid, "architectural_pattern", direction="incoming")
        dataset_slot_edges = _edges_with_handle(graph, nid, "dataset", direction="incoming")
        skill_edges = _edges_with_handle(graph, nid, "skill", direction="incoming")
        knowledge_edges = _edges_with_handle(graph, nid, "knowledge", direction="incoming")
        if node_type == "agent":
            # The Tool connector accepts a family of source types -- an
            # mcp_tool node contributes a callable capability, while a
            # Script node contributes declarative config/context (see
            # _resolve_tool_config/_resolve_script_config) -- so which
            # sub-kind a given edge is can only be recovered from its source
            # node's own `type`, not the (shared) handle. (A pre-Dataset-
            # connector graph still has its dataset edges on "tool" -- see
            # _LEGACY_DATASET_HANDLES -- which is why a Dataset source is
            # accepted on this handle too; _resolve_dataset_configs scans
            # every legacy spelling when it comes to actually reading them.)
            for edge in tool_edges:
                tool_source = nodes.get(edge["source"])
                source_type = tool_source.get("type") if tool_source else None
                if source_type not in (_MCP_TOOL_NODE_TYPES | _DATASET_NODE_TYPES | _SCRIPT_NODE_TYPES):
                    raise ProtocolValidationError(
                        f"Node {name!r}'s Tool connection must come from an MCP Tool or Script node."
                    )
            for edge in dataset_slot_edges:
                dataset_source = nodes.get(edge["source"])
                if dataset_source is None or dataset_source.get("type") not in _DATASET_NODE_TYPES:
                    raise ProtocolValidationError(f"Node {name!r}'s Dataset connection must come from a Dataset node.")
            # Deliberately uncapped, like Tool/Dataset and unlike Memory/
            # Script: several skills on one agent is the normal case, not an
            # ambiguity to resolve -- each contributes ~100 tokens of level-1
            # metadata and its body only loads if the model asks. Duplicates
            # aren't rejected either; _resolve_skill_config de-dupes.
            for edge in skill_edges:
                skill_source = nodes.get(edge["source"])
                if skill_source is None or skill_source.get("type") not in _SKILL_NODE_TYPES:
                    raise ProtocolValidationError(f"Node {name!r}'s Skill connection must come from a Skill node.")
            # Uncapped for the same reason as Skill: reading a shared team
            # bundle while writing to a personal one is a normal setup, not an
            # ambiguity. _resolve_knowledge_config de-dupes by server name, so
            # two nodes naming the same bundle cost nothing.
            for edge in knowledge_edges:
                knowledge_source = nodes.get(edge["source"])
                if knowledge_source is None or knowledge_source.get("type") not in _KNOWLEDGE_NODE_TYPES:
                    raise ProtocolValidationError(
                        f"Node {name!r}'s Knowledge connection must come from an OKF Bundle or OKF Document node."
                    )
            script_edges = [e for e in tool_edges if (nodes.get(e["source"]) or {}).get("type") in _SCRIPT_NODE_TYPES]
            if len(memory_edges) > 1:
                raise ProtocolValidationError(
                    f"Node {name!r} can have at most one Memory connection (found {len(memory_edges)})."
                )
            for edge in memory_edges:
                memory_source = nodes.get(edge["source"])
                if memory_source is None or memory_source.get("type") != "memory":
                    raise ProtocolValidationError(f"Node {name!r}'s Memory connection must come from a Memory node.")
            # Uncapped, like Skill and Knowledge above. It used to be capped
            # at one, on the assumption that an agent operates on "the"
            # dataset -- but comparing a model across several datasets, or
            # joining a cohort table to a measurements table, is ordinary
            # science, and the cap made it unexpressible. Each wired dataset
            # is named in the agent's Dataset-context block and opened as its
            # own workspace; duplicates aren't rejected because
            # _resolve_dataset_configs de-dupes by dataset_id.
            if len(script_edges) > 1:
                raise ProtocolValidationError(
                    f"Node {name!r} can have at most one Script connection (found {len(script_edges)})."
                )
            # Capped at one, but scoped to the execution-pattern family
            # specifically (see _EXECUTION_PATTERN_NODE_TYPES's own comment)
            # -- a future non-execution pattern node type connected
            # alongside one execution-pattern node is not this check's
            # business.
            execution_pattern_edges = [
                e for e in pattern_edges if (nodes.get(e["source"]) or {}).get("type") in _EXECUTION_PATTERN_NODE_TYPES
            ]
            if len(execution_pattern_edges) > 1:
                raise ProtocolValidationError(
                    f"Node {name!r} can have at most one execution-pattern connection "
                    f"(found {len(execution_pattern_edges)})."
                )
            for edge in pattern_edges:
                pattern_source = nodes.get(edge["source"])
                if pattern_source is None or pattern_source.get("type") not in _EXECUTION_PATTERN_NODE_TYPES:
                    raise ProtocolValidationError(
                        f"Node {name!r}'s Architectural Pattern connection must come from an Architectural "
                        "Pattern node."
                    )
        elif tool_edges or memory_edges or pattern_edges or dataset_slot_edges or skill_edges or knowledge_edges:
            raise ProtocolValidationError(
                f"Only Agent nodes can have a Tool, Memory, Architectural Pattern, Skill, Dataset, or "
                f"Knowledge connection (node {name!r})."
            )

        if node_type in _NODE_TYPE_TO_HANDLE:
            expected_handle = _NODE_TYPE_TO_HANDLE[node_type]
            allowed_handles = (
                _LEGACY_DATASET_HANDLES
                if node_type in _DATASET_NODE_TYPES
                else _LEGACY_HANDLES_BY_SLOT.get(expected_handle, frozenset({expected_handle}))
            )
            outgoing_wrong_handle = [
                e
                for e in graph.get("edges") or []
                if e.get("source") == nid and e.get("targetHandle") not in allowed_handles
            ]
            if outgoing_wrong_handle:
                handle_label = _HANDLE_LABELS[expected_handle]
                # Script shares the Tool handle and the OKF node types own
                # Knowledge, but none is literally a "Tool"/"Knowledge" node --
                # use their own display name as the leading noun (e.g. "Script
                # node 'X' can only connect to a node's Tool slot") while every
                # other family's leading noun still matches its handle label
                # 1:1, unchanged. Dataset is in the list for symmetry only:
                # its slot is now named after it, so both halves read
                # "Dataset" either way.
                leading_label = (
                    _NODE_TYPE_DISPLAY_NAMES[node_type]
                    if node_type in _DATASET_NODE_TYPES | _SCRIPT_NODE_TYPES | _KNOWLEDGE_NODE_TYPES
                    else handle_label
                )
                raise ProtocolValidationError(
                    f"{leading_label} node {name!r} can only connect to a node's {handle_label} slot, not a "
                    "regular pipeline edge."
                )

    return [nodes[nid] for nid in ordered]


def _edges_with_handle(graph: dict[str, Any], node_id: str, handle: str, *, direction: str) -> list[dict[str, Any]]:
    """Edges into/out of *node_id* wired into the *handle* connector slot --
    the connector-type marker always lives on the target side of an edge
    (see ``_CONNECTOR_HANDLES``), regardless of which end is being queried.
    ``direction`` is ``"incoming"`` (*node_id* is the edge's target) or
    ``"outgoing"`` (*node_id* is the edge's source).

    Matches every spelling that slot has ever been saved under, not just its
    current id (see ``_LEGACY_HANDLES_BY_SLOT``), so callers can name the
    current slot and never think about the rename again."""
    key = "target" if direction == "incoming" else "source"
    handles = _LEGACY_HANDLES_BY_SLOT.get(handle, frozenset({handle}))
    return [e for e in graph.get("edges") or [] if e.get(key) == node_id and e.get("targetHandle") in handles]


def sink_node_ids(graph: dict[str, Any]) -> list[str]:
    """Every node with no outgoing edges -- used both to validate a graph is
    runnable per-cell (exactly one sink required, see ``plan_cell_runs``) and
    by ``run_protocol`` itself to find the node whose output becomes a cell's
    result. Excludes every pure-config-source node type (every LLM provider/
    architectural pattern node, plus ``memory`` and ``mcp_tool``) -- these are
    never a pipeline's "final output," whether or not they're connected to
    anything (an unwired one would otherwise falsely count as an extra
    sink)."""
    nodes, downstream, _upstream = _adjacency(graph)
    return [
        nid for nid, node in nodes.items() if not downstream[nid] and node.get("type") not in _PURE_CONFIG_SOURCE_TYPES
    ]


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
    """Only "main" pipeline edges -- a pure-config-source node's (llm/memory/
    pattern/mcp_tool) inert placeholder output must never be treated as
    upstream pipeline context (see ``_build_user_input``/
    ``_upstream_output_text``)."""
    return [
        e["source"]
        for e in graph.get("edges") or []
        if e.get("target") == node_id and e.get("targetHandle") not in _CONNECTOR_HANDLES
    ]


# design_spec factor names (e.g. "Azure Foundry:Model", "Critic enabled") are
# free text, joined into a real cell_label like "Azure Foundry:Effort_medium__
# Azure Foundry:Model_claude-sonnet-5__Critic enabled_false" -- a string
# asaree_workspace_core's own _SAFE_COMPONENT regex rejects outright (spaces,
# colons). Sanitized here, once, rather than left for each agent to guess a
# safe cell_label on its own before calling open_workspace: an LLM asked to
# pass that raw string verbatim (see _build_user_input's Dataset-context
# block below) may improvise ITS OWN sanitization -- inconsistently across
# agents/attempts -- while run_model_script (and every other tool that falls
# back to the ambient _meta workspace_id instead of an explicit cell_label
# arg) always gets this function's raw, unsanitized output. That mismatch
# left run_model_script looking for a workspace directory that was never
# created under that exact raw name, failing every Score stage with a
# "workspace not initialized" error even after DC/FTE/FS/MLM completed
# cleanly. Sanitizing centrally, here, keeps what's shown to the agent and
# what's used ambiently byte-for-byte identical, matching this function's own
# existing contract to keep those two identities from drifting apart.
_UNSAFE_WORKSPACE_LABEL_CHAR = re.compile(r"[^A-Za-z0-9._=,-]")


def _effective_cell_label(cell_label: str | None, protocol_run_id: uuid.UUID) -> str:
    """``cell_label`` for a real factorial-cell run, else a synthetic
    per-run label -- shared by ``_compute_workspace_id`` and the Dataset
    connector's own context block (``_build_user_input``) so the two
    identities can never drift apart. Sanitized to a filesystem-safe token
    (see ``_UNSAFE_WORKSPACE_LABEL_CHAR`` above) since a real cell_label is
    built from free-text factor names -- the DB's own stored ``cell_label``
    column (display, matching, uniqueness) is untouched; only this
    execution-time copy changes."""
    label = cell_label or f"adhoc-{protocol_run_id}"
    return _UNSAFE_WORKSPACE_LABEL_CHAR.sub("_", label)


def _compute_workspace_id(
    experiment_id: uuid.UUID | None, cell_label: str | None, protocol_run_id: uuid.UUID
) -> str | None:
    """``{experiment_id}/{cell_label}`` -- the same convention the
    asaree-spinal-use-case notebook computes by hand, and what
    ``asaree_workspace_core``'s own ``resolve_workspace_id`` expects. ``None``
    when there's no experiment at all (an unlinked protocol run has no
    dataset to seed a workspace from). Computed unconditionally whenever an
    experiment IS linked -- regardless of whether any agent in this run has
    a Dataset node wired -- since it's inert for an agent that never calls a
    workspace-scoped tool, and lets one reach for workspace tools ambiently
    through a plain Tool connector without an explicit Dataset connector."""
    if experiment_id is None:
        return None
    return f"{experiment_id}/{_effective_cell_label(cell_label, protocol_run_id)}"


def _materialize_script(workspace_id: str | None, node_id: str, code: str) -> str:
    """Write a wired Script node's code next to the run's workspace; return its path.

    ``""`` when there's nowhere to put it -- no workspace id (an unlinked
    protocol run) or the write failed. The caller falls back to inlining the
    code in the prompt, which is what this replaces.

    The file lives under the run's own workspace directory because that
    directory is already the shared surface between this process and the MCP
    subprocesses: no new mount, no new configuration, and it is cleaned up with
    the workspace it belongs to. Rewritten on every run rather than reused --
    the graph is the source of truth, and an edited Script node must not leave
    a stale copy behind for a rerun to execute.
    """
    if not workspace_id:
        return ""
    safe_node = _UNSAFE_WORKSPACE_LABEL_CHAR.sub("_", node_id)
    try:
        root = Path(WORKSPACE_ROOT).resolve()
        directory = (root / workspace_id / "scripts").resolve()
        if root not in directory.parents:  # workspace_id is already sanitized; belt and braces
            return ""
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{safe_node}.py"
        # Atomic: a tool reading this concurrently must never see a half-write.
        tmp = directory / f"{safe_node}.py.tmp"
        tmp.write_text(code)
        tmp.replace(path)
    except OSError:
        logger.warning("script_materialize_failed", extra={"workspace_id": workspace_id, "node_id": node_id})
        return ""
    return str(path)


def _ambient_meta_for(graph: dict[str, Any], node_id: str, workspace_id: str | None = None) -> dict[str, Any]:
    """The node's Reference-route values, for Motoro's caller-ambient ``_meta``.

    Motoro lifts ``run_metadata["ambient_meta"]`` onto every MCP tool call as
    ``motoro.ambient.<key>`` (see its ``mcp/adapters``), which is the product's
    own half of the channel ``workspace_id`` already uses -- the model never
    sees these and so can never mistype one.

    Two keys today, both Reference-route (see the three routes at the top of
    this module, and Motoro's ``engine/sense.py``):

    * ``dataset_names`` -- the wired Dataset connectors' names, which is what
      lets ``open_workspace`` be called with no arguments at all.
    * ``script_path`` -- where the wired Script node's code was written. The
      code used to be pasted into the prompt for the model to copy back out
      into a tool argument; a script-running tool reads the file instead, so
      what executes is byte-for-byte what the user wrote. ``run_model_script``
      hashes its ``code`` for exactly this reason -- a hash detects a mangled
      transcription after the fact, while a path removes the transcription.

    Add to this rather than to the prompt whenever a new connector contributes
    an id or a path pointing at something held elsewhere.

    ``{}`` when nothing is wired -- Motoro skips an absent/empty dict, so the
    wire call is unchanged for a node with no references.
    """
    meta: dict[str, Any] = {}
    dataset_names = [str(c["dataset_name"]) for c in _resolve_dataset_configs(graph, node_id) if c.get("dataset_name")]
    if dataset_names:
        meta["dataset_names"] = dataset_names
    code = (_resolve_script_config(graph, node_id) or {}).get("code")
    if code:
        script_path = _materialize_script(workspace_id, node_id, str(code))
        if script_path:
            meta["script_path"] = script_path
    return meta


async def _preseed_dataset_workspace(
    graph: dict[str, Any], node_id: str, workspace_id: str | None, owner_id: uuid.UUID
) -> str:
    """Seed this cell's workspace from the wired dataset before the agent runs.

    Returns the dataset name that is now open at HEAD, or ``""`` if nothing was
    seeded. That return value is what turns ``_build_user_input``'s Dataset
    block from an instruction ("call open_workspace() first") into a statement
    of fact ("your data is already open") -- the point of doing this here is
    that opening a workspace is not a decision an agent should be making. It's
    a consequence of the user having wired a Dataset node, and ASAREE knows
    that at run start.

    Idempotent and safe to call on every turn: ``Workspace.open`` resumes a
    cell that already has accepted stages rather than resetting it.

    Deliberately seeds only when EXACTLY ONE dataset is wired, mirroring
    ``asaree_workspace_core.resolve_dataset_name``'s own rule. A cell workspace
    is keyed by ``experiment_id/cell_label`` alone, so several wired datasets
    are a real choice with no defensible default -- picking the first would be
    a guess dressed up as automation. Those runs keep the old flow: the prompt
    lists the candidates and the agent calls ``open_workspace(name=...)``,
    which reports the collision if it opens a second one.

    A failure here is logged and swallowed, never raised: a run whose dataset
    registration is broken should still start and let the agent surface the
    real error from its own ``open_workspace`` call, rather than dying before
    its first turn with a message no one is watching for.
    """
    if not workspace_id:
        return ""
    names = [str(c["dataset_name"]) for c in _resolve_dataset_configs(graph, node_id) if c.get("dataset_name")]
    if len(names) != 1:
        return ""
    try:
        seeded = await seed_cell_workspace(workspace_id=workspace_id, dataset_name=names[0], owner_id=owner_id)
    except WorkspaceSeedError as e:
        logger.warning(
            "workspace_preseed_failed",
            extra={"workspace_id": workspace_id, "node_id": node_id, "dataset": names[0], "error": str(e)},
        )
        return ""
    return seeded.dataset_name


async def _node_run_context(
    graph: dict[str, Any], node_id: str, workspace_id: str | None, owner_id: uuid.UUID
) -> tuple[dict[str, Any], str]:
    """``(ambient_meta, seeded_dataset_name)`` for one node -- everything the
    node's References contribute, resolved together so the three call sites
    (gated worker, single-node play, main loop) can't drift apart on which
    half they remembered to do."""
    ambient_meta = _ambient_meta_for(graph, node_id, workspace_id)
    seeded_dataset = await _preseed_dataset_workspace(graph, node_id, workspace_id, owner_id)
    return ambient_meta, seeded_dataset


def _resolve_llm_config(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    """The node's connected ``llm`` node's own config -- agent/critic_gate
    nodes no longer carry ``model_config_data`` themselves, it's resolved
    from the required LLM connector instead (``topological_order`` already
    validated it exists exactly once)."""
    nodes, _downstream, _upstream = _adjacency(graph)
    edges = _edges_with_handle(graph, node_id, "ai", direction="incoming")
    if not edges:
        return {}
    source = nodes.get(edges[0]["source"])
    if source is None:
        return {}
    return (source.get("data") or {}).get("config") or {}


def _resolve_dataset_configs(graph: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    """Every Dataset node wired into this agent, as a list of
    ``{"dataset_id": ..., "dataset_name": ...}`` configs in canvas wiring
    order -- ``[]`` when none is connected.

    A list rather than a single config for compatibility, not because several
    is the intended shape: the Dataset connector was briefly uncapped and
    graphs saved in that window can still carry two or more. It is capped at
    one again (see ``AgentNode.tsx``), because a cell's workspace is keyed by
    ``experiment_id/cell_label`` alone and therefore holds exactly one dataset
    -- ``seed_cell_workspace`` rejects a second. Comparing datasets is a
    ``dataset_config`` FACTOR instead: ``apply_factor_bindings`` replaces this
    node's whole ``data.config`` per cell (it runs before anything here), so
    each cell resolves to a one-element list naming its own dataset.

    Scans the Dataset handle plus both spellings it has been saved under
    before -- the short-lived ``resource`` one and the Tool handle it
    originally shared with mcp_tool/Script (see ``_LEGACY_DATASET_HANDLES``)
    -- matching on the source node's own ``type`` rather than trusting the
    handle alone. Nodes with ``enabled: False`` or no ``dataset_name`` are
    skipped and duplicates de-duped by ``dataset_id``, matching
    ``_resolve_skill_config``: two nodes naming one dataset would otherwise
    tell the agent to open the same workspace twice.

    Read by ``_build_user_input`` to fold a "Dataset context" block into the
    wired agent's own instruction; never resolved into any Motoro config,
    since a dataset isn't something Motoro's own ModelConfig/PatternConfig/
    ToolConfig has a slot for -- it's purely prompt context an agent uses to
    call ``open_workspace`` itself."""
    nodes, _downstream, _upstream = _adjacency(graph)
    configs: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Edges first, handles second: an agent's Dataset nodes should come out in
    # the order they were wired, not grouped by which handle spelling they
    # happen to be saved under.
    for edge in graph.get("edges") or []:
        if edge.get("target") != node_id or edge.get("targetHandle") not in _LEGACY_DATASET_HANDLES:
            continue
        source = nodes.get(edge.get("source"))
        if source is None or source.get("type") not in _DATASET_NODE_TYPES:
            continue
        config = (source.get("data") or {}).get("config") or {}
        if not config.get("enabled", True) or not config.get("dataset_name"):
            continue
        key = str(config.get("dataset_id") or config["dataset_name"])
        if key in seen:
            continue
        seen.add(key)
        configs.append(config)
    return configs


def _resolve_script_config(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    """``{"name": ..., "language": ..., "code": ...}`` from the node's
    connected Script node, or ``{}`` if none is connected -- optional, like
    Dataset, and sharing the Tool connector with mcp_tool rather than
    getting a dedicated handle (see ``_NODE_TYPE_TO_HANDLE``). Read by
    ``_build_user_input`` to fold the script's own code verbatim into the
    wired agent's instruction, for it to pass as some tool's own code-shaped
    argument (e.g. run_model_script's ``code``) -- ASAREE itself never
    executes this, the same "pure config source, no execution turn" status
    as every other connector."""
    nodes, _downstream, _upstream = _adjacency(graph)
    for edge in _edges_with_handle(graph, node_id, "tool", direction="incoming"):
        source = nodes.get(edge["source"])
        if source is not None and source.get("type") in _SCRIPT_NODE_TYPES:
            return (source.get("data") or {}).get("config") or {}
    return {}


def _resolve_skill_config(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    """``{"skill_ids": [...]}`` -- every Skill node wired into this agent's
    Skill connector, in a stable order -- or ``{}`` when none is connected.

    Ids, not bodies: the skill document itself lives in core
    (:mod:`motoro.services.skill_service`), and the node only names it, so
    editing a registered skill takes effect on the next run without touching
    a single graph. Unlike Dataset/Script this is a real Motoro config slot
    (``Agent.skill_config``), so ASAREE hands the ids over and lets the engine
    do progressive disclosure -- it never folds a skill body into the prompt
    itself.

    Order is the canvas wiring order (which is the order the agent's skill
    index lists them in), de-duplicated: two nodes naming the same registered
    skill would otherwise index it twice. A node with ``enabled: False`` or no
    resolved ``skill_id`` is skipped, matching ``_resolve_tool_config``."""
    nodes, _downstream, _upstream = _adjacency(graph)
    skill_ids: list[str] = []
    for edge in _edges_with_handle(graph, node_id, "skill", direction="incoming"):
        source = nodes.get(edge["source"])
        if source is None or source.get("type") not in _SKILL_NODE_TYPES:
            continue
        skill_node_config = (source.get("data") or {}).get("config") or {}
        if not skill_node_config.get("enabled", True):
            continue
        skill_id = skill_node_config.get("skill_id")
        if skill_id and str(skill_id) not in skill_ids:
            skill_ids.append(str(skill_id))
    return {"skill_ids": skill_ids} if skill_ids else {}


def _resolve_pattern_config(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    """``{"execution_pattern": slug, "pattern_params": {slug: {...}}}`` from
    the node's connected execution-pattern node, or ``{}`` if none is
    connected -- unlike LLM, this connector is optional (``topological_order``
    caps it at one, but doesn't require it): an agent with nothing connected
    gets ``execution_pattern=None`` passed to ``PatternConfig``, and
    Motoro's own ``PatternOrchestrator`` already defaults that to
    "reason_act" (``DEFAULT_EXECUTION_PATTERN``). ASAREE deliberately doesn't
    duplicate that default here -- see AgentNode's own auto-created "Reason +
    Act" node on the frontend for how the default stays visible instead of
    silently applying.

    A factor bound to this agent's own ``data.pattern_override`` (a synthetic
    field the frontend writes but never reads directly) wins over the wired
    connector node entirely -- this is how a Pattern factor varies the
    *node type* itself across cells, which no ordinary field-level binding
    can do. Shaped identically to this function's own return value,
    slug-keyed with the raw Motoro slug."""
    nodes, _downstream, _upstream = _adjacency(graph)
    node = nodes.get(node_id)
    override = (node.get("data") or {}).get("pattern_override") if node else None
    if override:
        return dict(override)
    edges = _edges_with_handle(graph, node_id, "architectural_pattern", direction="incoming")
    if not edges:
        return {}
    source = nodes.get(edges[0]["source"])
    if source is None:
        return {}
    slug = _EXECUTION_PATTERN_SLUGS.get(source.get("type", ""))
    if slug is None:
        return {}
    return {"execution_pattern": slug, "pattern_params": {slug: (source.get("data") or {}).get("config") or {}}}


def _resolve_tool_config(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    """``{"server_names": [...], "tool_names": [...]}`` -- built from every
    ``mcp_tool`` node wired into this agent's Tool connector -- replaces the
    agent's own (now-removed) ``tool_config`` field. Each ``mcp_tool`` node
    represents one MCP server connection and can allow-list *several* of
    that server's tools (``config.tool_names``, plural) -- a per-server node
    with a tools filter, deliberately not a node per tool. The Tool
    connector also accepts Script source nodes (plus, on a graph saved
    before the Resource connector existed, Dataset ones -- see
    ``_LEGACY_DATASET_HANDLES``) -- those are skipped here entirely, since
    they're read by ``_resolve_script_config``/``_resolve_dataset_configs``
    instead, not folded into this allow-list.

    ``tool_names`` MUST be namespaced as ``"{server_name}.{tool_name}"`` --
    that's the shape ``run_tools.gather_tools`` matches against
    ``motoro``'s own tool registry (``MCPServerRegistry.get_all_tools``
    namespaces every entry's ``name`` the same way). A node's own
    ``config.tool_names`` is stored bare (just the tool, not its server --
    see ``McpToolNodeInspector``'s ``toggleTool``), so it has to be prefixed
    here; passing it through bare silently starves every agent down to zero
    tools (``gather_tools`` finds no match and returns ``[]``), which the LLM
    then sees as having only ``final_answer`` available -- no error, just an
    agent that can't do anything and falls back to reporting the blocker in
    its final answer. A node without a resolved ``server_name`` can't be
    namespaced at all, so its tools are skipped rather than smuggled in bare.

    A node's ``config.tool_names`` is read here as-is, so it needs nothing
    extra to be an experimental factor: the frontend's ``tool_names`` level
    type binds exactly that path, and ``apply_factor_bindings`` has already
    substituted this cell's allow-list by the time this runs (the node's
    ``server_id``/``server_name`` are untouched -- a level varies which of ONE
    server's tools are offered, never which server). An empty allow-list is a
    meaningful level: the server still connects (its ``server_name`` is still
    reported) but contributes no tools to that cell."""
    nodes, _downstream, _upstream = _adjacency(graph)
    server_names: list[str] = []
    tool_names: list[str] = []
    for edge in _edges_with_handle(graph, node_id, "tool", direction="incoming"):
        source = nodes.get(edge["source"])
        if source is None or source.get("type") not in _MCP_TOOL_NODE_TYPES:
            continue
        tool_node_config = (source.get("data") or {}).get("config") or {}
        if not tool_node_config.get("enabled", True):
            continue
        server_name = tool_node_config.get("server_name")
        node_tool_names = tool_node_config.get("tool_names") or []
        if server_name:
            server_names.append(server_name)
            tool_names.extend(f"{server_name}.{name}" for name in node_tool_names)
    return {"server_names": server_names, "tool_names": tool_names}


def _resolve_knowledge_config(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    """The Knowledge connector's contribution to the agent's tool allow-list,
    shaped exactly like ``_resolve_tool_config``'s so the two can be merged.

    Every knowledge source -- a folder-picked OKF bundle
    (:mod:`asaree.services.okf_bundles`) or an uploaded single-concept OKF
    document (:mod:`asaree.services.okf_documents`) -- is served by a real MCP
    server, one process per directory, so at run time "knowledge" is just more
    tools: the split between this connector and Tool is about what the user is
    declaring, not about how the engine consumes it. Hence the identical shape
    rather than a separate Motoro config slot -- unlike Skill, core has no
    ``knowledge_config`` to hand this to. The bundle/document distinction
    doesn't survive to here at all: both node types carry a ``server_name``
    and a cached ``tool_names``, and this reads them the same way.

    Namespaced ``"{server_name}.{tool}"`` for the same non-negotiable reason
    as ``_resolve_tool_config``: bare names match nothing in
    ``run_tools.gather_tools`` and would silently leave the agent with no
    tools at all. A node with no ``tool_names`` cached (a bundle whose server
    failed to spawn, so nothing was ever discovered) contributes its server
    name but no tools -- the run then behaves as if the bundle weren't wired,
    which is the honest outcome for a bundle that isn't actually reachable.

    De-duplicated by server: two nodes pointing at the same bundle are one
    server, and listing it twice would just double every tool name."""
    nodes, _downstream, _upstream = _adjacency(graph)
    server_names: list[str] = []
    tool_names: list[str] = []
    for edge in _edges_with_handle(graph, node_id, "knowledge", direction="incoming"):
        source = nodes.get(edge["source"])
        if source is None or source.get("type") not in _KNOWLEDGE_NODE_TYPES:
            continue
        bundle_config = (source.get("data") or {}).get("config") or {}
        if not bundle_config.get("enabled", True):
            continue
        server_name = bundle_config.get("server_name")
        if not server_name or server_name in server_names:
            continue
        server_names.append(server_name)
        tool_names.extend(f"{server_name}.{name}" for name in bundle_config.get("tool_names") or [])
    return {"server_names": server_names, "tool_names": tool_names}


def _resolve_dataset_tool_config(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    """The Dataset connector's contribution to the tool allow-list: ASAREE's
    own ``asaree-workspace`` server, shaped like ``_resolve_tool_config``'s
    output so it merges with the rest.

    A Dataset node declares "this agent operates on data"; the tools that
    make that possible should follow from the declaration rather than from a
    second, differently-shaped node the user has to know to also wire. Before
    this, an agent with only a Dataset wired was told (by ``_build_user_input``)
    to call ``open_workspace`` and then handed an allow-list that didn't
    contain it -- the run's system prompts in the original spinal use case
    covered the gap, and nothing else did.

    Unlike Tool and Knowledge, the grant is implicit, so the tools are a fixed
    list (``WORKSPACE_AGENT_TOOLS``) rather than whatever a node cached: there
    is no node here whose checkboxes could express a narrower choice.
    """
    if not _resolve_dataset_configs(graph, node_id):
        return {"server_names": [], "tool_names": []}
    return {
        "server_names": [WORKSPACE_SERVER_NAME],
        "tool_names": [f"{WORKSPACE_SERVER_NAME}.{name}" for name in WORKSPACE_AGENT_TOOLS],
    }


def _merge_tool_configs(*configs: dict[str, Any]) -> dict[str, Any]:
    """Union of several ``{"server_names", "tool_names"}`` allow-lists, order
    preserved, duplicates dropped.

    De-duplication matters now that one of them is implicit: a user who *did*
    wire an ``asaree-workspace`` Tool node alongside a Dataset would otherwise
    contribute the same namespaced names twice.
    """
    server_names: list[str] = []
    tool_names: list[str] = []
    for config in configs:
        for name in config.get("server_names") or []:
            if name not in server_names:
                server_names.append(name)
        for name in config.get("tool_names") or []:
            if name not in tool_names:
                tool_names.append(name)
    return {"server_names": server_names, "tool_names": tool_names}


def _is_node_active(node: dict[str, Any]) -> bool:
    """Whether this node's own logic actually runs -- a deactivated node
    passes its upstream input straight through as its own output instead
    (see ``_upstream_output_text``), the standard node-disable semantic.
    Absent ``data.active`` means active, so every graph saved before this
    field existed is unaffected. ``critic_gate`` nodes have no separate
    ``active`` flag of their own: their existing ``config.enabled`` already
    means exactly this for the review step specifically (see
    ``_run_gated_worker``) -- deactivating a WORKER that feeds a gate is a
    separate, deliberately unsupported case (see ``topological_order``'s
    validation of gated pairs)."""
    return bool((node.get("data") or {}).get("active", True))


def _upstream_output_text(graph: dict[str, Any], node_id: str, node_runs: dict[str, Any]) -> str:
    """What a deactivated node's own output becomes: its upstream context,
    verbatim, with no goal/prompt mixed in -- the literal "pass the input
    straight through unchanged" semantic of a disabled node. Empty
    string for a start node (nothing upstream to pass through)."""
    upstream_ids = _upstream_ids(graph, node_id)
    parts = [node_runs[uid]["output_text"] for uid in upstream_ids if node_runs.get(uid, {}).get("output_text")]
    return "\n\n".join(parts)


def _build_user_input(
    node: dict[str, Any],
    graph: dict[str, Any],
    node_runs: dict[str, Any],
    *,
    experiment_id: uuid.UUID | None = None,
    effective_cell_label: str | None = None,
    script_bound: bool = False,
    seeded_dataset: str = "",
) -> str:
    """The node's own prompt (falling back to its goal, then its canvas
    label), plus (flat, unstructured -- a deliberate V1 simplification) each
    already-completed upstream node's output_text as context, plus a Dataset
    cue when this node has a Dataset connector wired, plus a Script cue when
    one is wired. `prompt` is the one field meant to change per run (the
    per-invocation user message); `goal` is a persistent objective, only used
    here as prompt's own fallback when the user hasn't set one. Real structured
    handoff via output_contract.payload is a fast-follow, the same way the
    source notebook's own stage-report-block pattern could graduate to using
    it.

    Both the Dataset and the Script block used to be dictation: the exact
    ``open_workspace(experiment_id=..., cell_label=..., name=...)`` call, and
    the script's entire source pasted in for the model to copy back out into a
    tool argument. Neither is now. Both are References, and a Reference is
    bound into ambient request ``_meta``, not narrated (see the three routes at
    the top of this module, and Motoro's ``engine/sense.py``) -- ``_meta`` is
    out of the model's reach, so there is nothing to mistype. What stays here
    is only what ``_meta`` genuinely cannot supply: the *fact* that a dataset
    or a script is waiting, and the dataset name to disambiguate with when
    more than one is wired.

    *script_bound* says the wired script reached ``_meta`` as a path
    (``_ambient_meta_for``). When it didn't -- an unlinked protocol run has no
    workspace directory to write it to -- the code is inlined here as before,
    because a prompt the model can copy from beats no script at all.

    *seeded_dataset* says ASAREE already opened the cell's workspace on the
    agent's behalf (``_preseed_dataset_workspace``). When it did, the Dataset
    block stops asking for a tool call at all and just says the data is there
    -- opening a workspace was never a decision worth spending an agent turn
    on, and a step the agent can't skip is a step it can't get wrong."""
    data: dict[str, Any] = node.get("data") or {}
    config: dict[str, Any] = data.get("config", {})
    seed: str = config.get("prompt") or config.get("goal") or data.get("label", "")
    parts = [seed]

    upstream_ids = _upstream_ids(graph, node["id"])
    upstream_context = [
        f"[{uid}]: {node_runs[uid]['output_text']}" for uid in upstream_ids if node_runs.get(uid, {}).get("output_text")
    ]
    if upstream_context:
        parts.append("Upstream context:\n" + "\n\n".join(upstream_context))

    dataset_configs = _resolve_dataset_configs(graph, node["id"])
    if dataset_configs and experiment_id is not None and effective_cell_label is not None:
        dataset_names = [str(c["dataset_name"]) for c in dataset_configs]
        if seeded_dataset:
            # Nothing to call: the workspace was seeded before this turn, and
            # every workspace/domain tool resolves it from ambient _meta. Named
            # rather than left implicit so the agent can report what it worked
            # on, and so a wrong wiring is visible in the transcript.
            parts.append(
                "Dataset context:\n"
                f"Your data is already open: the dataset {seeded_dataset!r} is loaded into this "
                "cell's workspace at HEAD. Do NOT call open_workspace -- the workspace tools and "
                "the sklearn tools all resolve it from ambient run context. Start with the "
                "analysis itself. (workspace_status() reports the current state if you need it.)"
            )
        elif len(dataset_names) == 1:
            # Pre-seeding was skipped or failed (an unlinked protocol run has
            # no workspace id; a broken registration logs and falls through) --
            # so fall back to asking for the call. Still no arguments: with one
            # dataset wired, open_workspace resolves them all from ambient _meta.
            parts.append(
                "Dataset context:\n"
                "A dataset is registered for this run. Call open_workspace() before doing any data "
                "work -- it takes no arguments here; which dataset and which workspace both arrive "
                "as ambient run context. Its response names what it opened."
            )
        else:
            # `name` is the one thing _meta can't decide for the agent: the
            # ambient fallback deliberately refuses to guess among several, so
            # the names stay in the prompt. experiment_id/cell_label still do
            # not -- they come from the ambient workspace_id.
            #
            # And that ambient workspace_id is exactly why this says "pick
            # one": a cell's workspace is keyed by experiment_id/cell_label
            # alone, so all of these would resolve to the same directory.
            # open_workspace now refuses the second one rather than silently
            # returning the first one's data (it used to claim, wrongly, that
            # each name got its own workspace).
            #
            # A LEGACY path now: the connector is capped at one again, and
            # the real fix landed as the per-dataset cell -- a
            # ``dataset_config`` factor, whose levels are whole Dataset
            # configs, so each cell gets its own dataset in its own
            # workspace. Only a graph saved while the connector was uncapped
            # still reaches this branch.
            listed = "\n".join(f'- "{n}"' for n in dataset_names)
            parts.append(
                "Dataset context:\n"
                f"{len(dataset_names)} datasets are registered for this run:\n{listed}\n"
                "Call open_workspace(name=...) with the ONE this cell should work on, before doing "
                "any data work. `name` is the only argument to pass; the rest arrives as ambient "
                "run context. This cell has a single workspace, so it holds a single dataset -- "
                "opening a second here is an error, not a second workspace."
            )

    script_config = _resolve_script_config(graph, node["id"])
    script_code = script_config.get("code")
    if script_code and script_bound:
        parts.append(
            "Script context:\n"
            "A script is wired into this step. The script-running tool (e.g. run_model_script) picks "
            "it up from ambient run context -- call it WITHOUT a `code` argument and it executes the "
            "wired script exactly as written. Do not retype or paraphrase it."
        )
    elif script_code:
        # No workspace directory to write it to (see _materialize_script), so
        # fall back to what this did before: paste it and ask for a verbatim
        # copy. Costs prompt tokens on every turn and is only as faithful as
        # the model's transcription -- which is the whole reason the path
        # above exists.
        parts.append(
            "Script to pass verbatim as the relevant tool's own code argument (e.g. run_model_script's "
            f"`code`):\n```python\n{script_code}\n```"
        )

    return "\n\n".join(parts)


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


async def _poll_cancel_flag(protocol_run_id: uuid.UUID, cancel_event: asyncio.Event, interval: float = 1.5) -> None:
    """Runs alongside one in-flight execute_run call, watching for a Stop
    click (POST .../cancel) that a completely different request -- possibly
    a different worker process entirely, since protocol runs execute in
    arq's worker, not the API process -- raised on this run's own row.
    Sets cancel_event the moment cancel_requested_at is seen populated;
    Motoro's own runtime checks that event before every Sense/Reason/
    Plan/Act phase (motoro.engine.runtime.AgentRuntime._check_interrupt),
    which is what actually lets a single agent's run wind down mid-loop
    instead of only ever being caught at run_protocol's own between-nodes
    check (which can't interrupt a node already in flight)."""
    while True:
        await asyncio.sleep(interval)
        async with get_session() as db:
            requested_at = await get_cancel_requested_at(db, protocol_run_id)
        if requested_at is not None:
            cancel_event.set()
            return


async def _execute_run_cancellable(
    *, run_id: uuid.UUID, protocol_run_id: uuid.UUID, available_tools: list[dict[str, Any]], timeout: float
) -> None:
    """Wraps Motoro's execute_run with the poller above, scoped to
    exactly this one run's lifetime -- shared by _run_agent_node and
    _run_critic rather than duplicating the poller's start/stop lifecycle in
    both. Deliberately per-call, not per-protocol-run: once a Stop is
    detected, run_protocol's own between-nodes check means no later node
    ever starts, so nothing else would benefit from a longer-lived poller,
    and tearing this one down between nodes avoids running it during gaps
    where no agent is actually executing."""
    cancel_event = asyncio.Event()
    poller = asyncio.create_task(_poll_cancel_flag(protocol_run_id, cancel_event))
    try:
        await asyncio.wait_for(
            execute_run(
                run_id=run_id, registry=get_registry(), available_tools=available_tools, cancel_event=cancel_event
            ),
            timeout=timeout,
        )
    finally:
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller


async def _run_agent_node(
    node: dict[str, Any],
    *,
    protocol_id: uuid.UUID,
    protocol_run_id: uuid.UUID,
    owner_id: uuid.UUID,
    user_input: str,
    graph: dict[str, Any],
    workspace_id: str | None = None,
    ambient_meta: dict[str, Any] | None = None,
) -> tuple[str | None, str | None, uuid.UUID | None]:
    """Create-or-sync the real agent and run it to completion. Returns
    ``(output_text, error, run_id)`` -- exactly one of output_text/error is
    ``None``. ``run_id`` is the underlying Motoro AgentRun id -- always
    populated once ``create_run`` succeeds (even on a later timeout/error),
    since that's what the canvas's Output tab uses to fetch this node's own
    step trace (``GET /runs/{run_id}/steps``); only ``None`` if agent
    creation/sync itself failed before a run could even be created."""
    config = node["data"]["config"]
    # Deterministic, not config["name"]: Agent.name is unique per OWNER, not
    # per protocol, so trusting the freeform (often identically-defaulted)
    # config.name directly risks two unrelated nodes silently overwriting
    # each other's agent definition on every run. config.name is folded
    # into the description instead, purely as a human label.
    agent_name = f"protocol-{protocol_id}-{node['id']}"
    # Model/tool/execution-pattern are no longer fields on the agent's own
    # config -- resolved from its required LLM connector, its (optional,
    # repeatable) Tool connectors, and its optional Architectural Pattern
    # connector instead (topological_order already validated their shape).
    model_config_data = {k: v for k, v in _resolve_llm_config(graph, node["id"]).items() if v is not None}
    model_config = ModelConfig(**model_config_data)
    # Three connectors feed one allow-list. The Knowledge connector's OKF
    # bundles and documents are MCP servers like any other, so they land here
    # rather than in a slot of their own (see _resolve_knowledge_config), and
    # the Dataset connector implies ASAREE's own workspace tools (see
    # _resolve_dataset_tool_config) -- the split between the three is about
    # what the user is declaring, not about how the engine consumes it.
    tool_config = _merge_tool_configs(
        _resolve_tool_config(graph, node["id"]),
        _resolve_knowledge_config(graph, node["id"]),
        _resolve_dataset_tool_config(graph, node["id"]),
    )
    pattern_config_data = _resolve_pattern_config(graph, node["id"])
    pattern_config = PatternConfig(
        execution_pattern=pattern_config_data.get("execution_pattern"),
        pattern_params=pattern_config_data.get("pattern_params") or {},
    ).model_dump()
    # Always a dict, never None, even with nothing wired: Motoro's update_agent
    # reads None as "leave unchanged", so an agent that had skills and then had
    # them unwired on the canvas would silently keep running with them. An
    # explicit empty list is how you detach.
    skill_config = _resolve_skill_config(graph, node["id"]) or {"skill_ids": []}
    description = config.get("description") or ""
    label = node.get("data", {}).get("label")
    if label:
        description = f"{description} (canvas label: {label})".strip()
    # Explicit, ASAREE-owned default -- Motoro's own fallback
    # ("You are {name}. {description}") would use `agent_name` here, an
    # internal "protocol-{protocol_id}-{node_id}" bookkeeping id no user
    # ever sees, not this agent's actual canvas identity.
    system_prompt = config.get("system_prompt") or _default_system_prompt(label, "Agent")

    existing = await get_agent_by_name(agent_name, owner_id=owner_id)
    if existing is not None:
        agent = await update_agent(
            existing.id,
            goal=config.get("goal") or "",
            description=description,
            system_prompt=system_prompt,
            model_config=model_config,
            pattern_config=pattern_config,
            tool_config=tool_config,
            skill_config=skill_config,
            output_contract=config.get("output_contract"),
            budget_limit_usd=config.get("budget_limit_usd"),
            max_run_duration_seconds=config.get("max_run_duration_seconds"),
        )
    else:
        agent = await create_agent(
            name=agent_name,
            goal=config.get("goal") or "",
            description=description,
            system_prompt=system_prompt,
            model_config=model_config,
            pattern_config=pattern_config,
            tool_config=tool_config,
            skill_config=skill_config,
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
        metadata={
            "protocol_id": str(protocol_id),
            "protocol_run_id": str(protocol_run_id),
            "node_id": node["id"],
            **({"workspace_id": workspace_id} if workspace_id else {}),
            # Precomputed by the caller when it also needed to know whether the
            # script got bound (_build_user_input's script_bound); recomputed
            # here only for a caller that didn't care.
            **(
                {"ambient_meta": resolved_ambient}
                if (
                    resolved_ambient := (
                        ambient_meta
                        if ambient_meta is not None
                        else _ambient_meta_for(graph, node["id"], workspace_id)
                    )
                )
                else {}
            ),
        },
    )
    timeout = agent.max_run_duration_seconds or get_settings().worker_job_timeout_seconds
    try:
        await _execute_run_cancellable(
            run_id=run.id, protocol_run_id=protocol_run_id, available_tools=gather_tools(agent), timeout=timeout
        )
    except TimeoutError:
        return None, f"run exceeded its {timeout}s execution budget", run.id
    except Exception as e:  # noqa: BLE001 -- same boundary reasoning as execute_run_task
        return None, f"{type(e).__name__}: {e}", run.id

    finished = await get_run(run.id)
    if finished is None:
        return None, "run vanished after execution", run.id
    if finished.status == RunStatus.CANCELLED:
        # finished.error is None on a clean cancellation (Motoro's own
        # runtime never sets error_msg on that path) -- without this check
        # a mid-run Stop would silently fall through and look like a normal
        # completion with an empty output.
        return None, _AGENT_CANCELLED, run.id
    if finished.error:
        return None, finished.error, run.id
    envelope = parse_envelope(finished.output)
    output_text = envelope.result if envelope is not None else (finished.output or "")
    return output_text, None, run.id


async def _run_critic(
    gate: dict[str, Any],
    *,
    protocol_id: uuid.UUID,
    protocol_run_id: uuid.UUID,
    owner_id: uuid.UUID,
    worker_output: str,
    graph: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Create-or-sync the gate's own critic agent and run it once. Returns
    ``(verdict, error, critic_run_id)`` -- exactly one of verdict/error is
    ``None``. ``critic_run_id`` is populated as soon as the critic's own Run
    is created, even on failure, so the caller can still surface it for
    debugging (e.g. a timed-out or malformed-verdict critic run). The critic
    never gets tools and always runs single-pass (matches the notebook's own
    ``CRITIC_TOOLS = []`` / ``SINGLE_PASS_PATTERN``), and its
    ``output_contract`` is always :data:`CRITIC_OUTPUT_CONTRACT` -- not
    whatever (if anything) is in the node's own config. Model is resolved
    from its required LLM connector, same as an agent node."""
    config = gate["data"]["config"]
    agent_name = f"protocol-{protocol_id}-{gate['id']}"
    model_config_data = {k: v for k, v in _resolve_llm_config(graph, gate["id"]).items() if v is not None}
    model_config = ModelConfig(**model_config_data)
    pattern_config = PatternConfig(execution_pattern="single_agent_baseline").model_dump()
    goal = config.get("goal") or "Review the given output and return an approval verdict with feedback."
    description = config.get("description") or ""
    label = gate.get("data", {}).get("label")
    if label:
        description = f"{description} (canvas label: {label})".strip()
    system_prompt = config.get("system_prompt") or _default_system_prompt(label, "Critic Gate")
    tool_config: dict[str, list[str]] = {"server_names": [], "tool_names": []}

    existing = await get_agent_by_name(agent_name, owner_id=owner_id)
    if existing is not None:
        agent = await update_agent(
            existing.id,
            goal=goal,
            description=description,
            system_prompt=system_prompt,
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
            system_prompt=system_prompt,
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
    critic_run_id = str(run.id)
    timeout = agent.max_run_duration_seconds or get_settings().worker_job_timeout_seconds
    try:
        await _execute_run_cancellable(
            run_id=run.id, protocol_run_id=protocol_run_id, available_tools=gather_tools(agent), timeout=timeout
        )
    except TimeoutError:
        return None, f"critic run exceeded its {timeout}s execution budget", critic_run_id
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}", critic_run_id

    finished = await get_run(run.id)
    if finished is None:
        return None, "critic run vanished after execution", critic_run_id
    if finished.status == RunStatus.CANCELLED:
        return None, _AGENT_CANCELLED, critic_run_id
    if finished.error:
        return None, finished.error, critic_run_id
    envelope = parse_envelope(finished.output)
    if envelope is None or envelope.payload is None:
        return None, "critic did not return a structured verdict", critic_run_id
    return envelope.payload, None, critic_run_id


async def _run_gated_worker(
    worker: dict[str, Any],
    gate: dict[str, Any],
    *,
    protocol_id: uuid.UUID,
    protocol_run_id: uuid.UUID,
    owner_id: uuid.UUID,
    graph: dict[str, Any],
    node_runs: dict[str, Any],
    workspace_id: str | None = None,
    experiment_id: uuid.UUID | None = None,
    effective_cell_label: str | None = None,
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
    # Computed once for the whole revision loop: every attempt reruns the same
    # worker against the same references, so re-materializing the script per
    # attempt would only rewrite an identical file.
    worker_ambient, worker_seeded_dataset = await _node_run_context(graph, worker["id"], workspace_id, owner_id)
    base_instruction = _build_user_input(
        worker,
        graph,
        node_runs,
        experiment_id=experiment_id,
        effective_cell_label=effective_cell_label,
        script_bound="script_path" in worker_ambient,
        seeded_dataset=worker_seeded_dataset,
    )
    instruction = base_instruction
    # Tracks the most recent critic verdict/run across attempts so the
    # forced-accept branch (which never calls the critic for its own final
    # attempt) can still surface *why* a revision was needed last time,
    # instead of silently discarding that context once it stops being used
    # to build the next instruction.
    last_verdict: dict[str, Any] | None = None
    last_critic_run_id: str | None = None

    for attempt in range(max_revisions + 1):
        output_text, error, run_id = await _run_agent_node(
            worker,
            protocol_id=protocol_id,
            protocol_run_id=protocol_run_id,
            owner_id=owner_id,
            user_input=instruction,
            graph=graph,
            workspace_id=workspace_id,
            ambient_meta=worker_ambient,
        )
        run_id_str = str(run_id) if run_id else None
        if error == _AGENT_CANCELLED:
            return (
                {
                    "status": "cancelled",
                    "output_text": None,
                    "error": None,
                    "attempts": attempt + 1,
                    "run_id": run_id_str,
                },
                {"status": "skipped"},
            )
        if error:
            return (
                {
                    "status": "failed",
                    "output_text": None,
                    "error": error,
                    "attempts": attempt + 1,
                    "run_id": run_id_str,
                },
                {"status": "skipped"},
            )
        assert output_text is not None, "_run_agent_node guarantees output_text when error is falsy"

        if not enabled:
            return (
                {
                    "status": "completed",
                    "output_text": output_text,
                    "error": None,
                    "attempts": attempt + 1,
                    "run_id": run_id_str,
                },
                {"status": "completed", "output_text": output_text, "approved": None, "revisions_used": 0},
            )

        if attempt == max_revisions:
            return (
                {
                    "status": "completed",
                    "output_text": output_text,
                    "error": None,
                    "attempts": attempt + 1,
                    "run_id": run_id_str,
                },
                {
                    "status": "completed",
                    "output_text": output_text,
                    "approved": None,
                    "revisions_used": attempt,
                    "forced": True,
                    # Last verdict is the rejection that forced this final attempt --
                    # None only when max_revisions is 0 (no critic ever ran).
                    "feedback": last_verdict.get("feedback") if last_verdict else None,
                    "rejection_scope": last_verdict.get("rejection_scope") if last_verdict else None,
                    "run_id": last_critic_run_id,
                },
            )

        verdict, verdict_error, critic_run_id = await _run_critic(
            gate,
            protocol_id=protocol_id,
            protocol_run_id=protocol_run_id,
            owner_id=owner_id,
            worker_output=output_text,
            graph=graph,
        )
        if verdict_error == _AGENT_CANCELLED:
            # The worker's own output is real and already complete -- only
            # the critic's review was interrupted, so the worker still
            # reports "completed" with its real output_text; just the gate
            # itself is "cancelled".
            return (
                {
                    "status": "completed",
                    "output_text": output_text,
                    "error": None,
                    "attempts": attempt + 1,
                    "run_id": run_id_str,
                },
                {"status": "cancelled", "output_text": None, "error": None, "run_id": critic_run_id},
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
                    "run_id": run_id_str,
                },
                {"status": "failed", "output_text": None, "error": verdict_error, "run_id": critic_run_id},
            )
        assert verdict is not None, "_run_critic guarantees verdict when verdict_error is falsy"

        if verdict.get("approved"):
            return (
                {
                    "status": "completed",
                    "output_text": output_text,
                    "error": None,
                    "attempts": attempt + 1,
                    "run_id": run_id_str,
                },
                {
                    "status": "completed",
                    "output_text": output_text,
                    "approved": True,
                    "revisions_used": attempt,
                    "feedback": verdict.get("feedback"),
                    "rejection_scope": None,
                    "run_id": critic_run_id,
                },
            )

        last_verdict = verdict
        last_critic_run_id = critic_run_id
        instruction = _build_revision_instruction(base_instruction, verdict, output_text)

    raise AssertionError("_run_gated_worker fell through its attempt loop")  # unreachable


async def plan_cell_runs(
    db: AsyncSession,
    *,
    protocol_id: uuid.UUID,
    experiment_id: uuid.UUID | None,
    owner_id: uuid.UUID,
    graph: dict[str, Any],
) -> tuple[list[ProtocolRun], int]:
    """ "Run all cells": creates one pending :class:`ProtocolRun` per
    not-yet-scored :class:`FactorialCellResult` under *experiment_id*, each
    carrying that cell's own ``factor_values`` for ``run_protocol`` to
    substitute at execution time via ``apply_factor_bindings``. Returns
    ``(created_runs, skipped_count)`` -- a cell already carrying
    ``metric_values`` is skipped (resume semantics: a repeat click doesn't
    re-run, and re-bill, an already-scored cell). Raises
    :class:`ProtocolValidationError` (same type the plain-run endpoint
    already 422s on) if there's no linked experiment, the graph itself is
    invalid, the graph doesn't have exactly one sink node -- a cell's result
    has to come from somewhere unambiguous, mirroring the notebook's own
    single-pipeline (DC->FTE->FS->MLM) shape -- or the experiment's declared
    coordination strategy rejects this graph (see
    ``validate_coordination_strategy``). Does NOT enqueue the created runs --
    that's the caller's job, same create-then-enqueue split
    ``create_protocol_run_endpoint`` already uses for a plain run."""
    if experiment_id is None:
        raise ProtocolValidationError("This protocol has no linked experiment to run cells for.")
    topological_order(graph)  # raises ProtocolValidationError on a cycle/empty graph
    sinks = sink_node_ids(graph)
    if len(sinks) != 1:
        raise ProtocolValidationError(
            f"This protocol must have exactly one final node to run per experimental cell (found {len(sinks)})."
        )
    experiment = await get_experiment(db, experiment_id)
    design_spec = experiment.design_spec if experiment is not None else None
    validate_coordination_strategy(design_spec, has_gated_pair=bool(find_gated_pairs(graph)))

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


async def plan_single_cell_run(
    db: AsyncSession,
    *,
    protocol_id: uuid.UUID,
    experiment_id: uuid.UUID | None,
    owner_id: uuid.UUID,
    graph: dict[str, Any],
    cell_label: str,
) -> ProtocolRun:
    """Run one already-generated cell for real, by name -- the single-cell
    counterpart to plan_cell_runs's own "every not-yet-scored cell" batch.
    The canvas's own Run button offers this alongside its existing ad-hoc
    (no substitution) run once the linked experiment has generated cells,
    for testing one specific factor combination without either running
    everything or falling back to an un-substituted smoke test. Same
    validation as plan_cell_runs (linked experiment, valid graph, exactly
    one sink, coordination strategy) but does NOT skip an already-scored
    cell -- picking one specific cell by name is a deliberate re-run, not a
    batch resume, so there's nothing to protect it from."""
    if experiment_id is None:
        raise ProtocolValidationError("This protocol has no linked experiment to run a cell for.")
    topological_order(graph)  # raises ProtocolValidationError on a cycle/empty graph
    sinks = sink_node_ids(graph)
    if len(sinks) != 1:
        raise ProtocolValidationError(
            f"This protocol must have exactly one final node to run per experimental cell (found {len(sinks)})."
        )
    experiment = await get_experiment(db, experiment_id)
    design_spec = experiment.design_spec if experiment is not None else None
    validate_coordination_strategy(design_spec, has_gated_pair=bool(find_gated_pairs(graph)))

    cell = await get_cell(db, experiment_id=experiment_id, cell_label=cell_label)
    if cell is None:
        raise ProtocolValidationError(f"No such cell: {cell_label!r}")
    return await create_protocol_run(
        db,
        protocol_id=protocol_id,
        owner_id=owner_id,
        cell_label=cell.cell_label,
        factor_values=cell.factor_values or {},
    )


def validate_single_node_runnable(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    """Validates a node can run in isolation (the canvas's per-node Play
    icon) -- a deliberately narrower check than topological_order's
    full-graph validation, since a single-node run must not fail because of
    some OTHER, unrelated node's own incomplete config elsewhere in the same
    graph. Returns the node dict on success."""
    nodes: dict[str, dict[str, Any]] = {n["id"]: n for n in graph.get("nodes") or []}
    node = nodes.get(node_id)
    if node is None:
        raise ProtocolValidationError(f"No such node: {node_id!r}")
    if node.get("type") != "agent":
        raise ProtocolValidationError("Only Agent nodes can be run on their own.")
    if _upstream_ids(graph, node_id):
        raise ProtocolValidationError(
            "This agent has upstream input from another node -- running it alone isn't supported yet. "
            "Use the canvas's main Run button to run the whole pipeline."
        )
    llm_edges = _edges_with_handle(graph, node_id, "ai", direction="incoming")
    if len(llm_edges) != 1:
        raise ProtocolValidationError(
            f"Node {_node_display_name(node)!r} must have exactly one AI connection (found {len(llm_edges)})."
        )
    llm_source = nodes.get(llm_edges[0]["source"])
    if llm_source is None or llm_source.get("type") not in _LLM_NODE_TYPES:
        raise ProtocolValidationError(f"Node {_node_display_name(node)!r}'s AI connection must come from an AI node.")
    return node


async def _run_single_node(
    protocol_run_id: uuid.UUID,
    *,
    protocol_id: uuid.UUID,
    owner_id: uuid.UUID,
    graph: dict[str, Any],
    node_id: str,
    experiment_id: uuid.UUID | None = None,
) -> None:
    """The canvas's per-node Play run: one Agent node, no upstream, no gated
    pair, no factor substitution, no coordination-strategy check -- none of
    those concepts apply to a single node run in isolation. A deliberately
    separate path from the main topological walk below, not a special case
    bolted onto it."""
    try:
        node = validate_single_node_runnable(graph, node_id)
    except ProtocolValidationError as e:
        async with get_session() as db:
            await set_status(db, protocol_run_id, status="failed", error=str(e))
        return

    async with get_session() as db:
        await set_status(db, protocol_run_id, status="running")
        await update_node_run(db, protocol_run_id, node_id, {"status": "running"})

    # Never a real factorial cell -- a single-node Play click always gets a
    # synthetic per-run label (see _effective_cell_label).
    effective_cell_label = _effective_cell_label(None, protocol_run_id)
    workspace_id = _compute_workspace_id(experiment_id, None, protocol_run_id)
    ambient_meta, seeded_dataset = await _node_run_context(graph, node["id"], workspace_id, owner_id)
    user_input = _build_user_input(
        node,
        graph,
        {},
        experiment_id=experiment_id,
        effective_cell_label=effective_cell_label,
        script_bound="script_path" in ambient_meta,
        seeded_dataset=seeded_dataset,
    )
    output_text, error, run_id = await _run_agent_node(
        node,
        protocol_id=protocol_id,
        protocol_run_id=protocol_run_id,
        owner_id=owner_id,
        user_input=user_input,
        graph=graph,
        workspace_id=workspace_id,
        ambient_meta=ambient_meta,
    )
    node_run = {
        "status": "failed" if error else "completed",
        "output_text": output_text,
        "error": error,
        "run_id": str(run_id) if run_id else None,
    }
    async with get_session() as db:
        await update_node_run(db, protocol_run_id, node_id, node_run)
        await set_status(db, protocol_run_id, status="failed" if error else "completed", error=error)


async def run_protocol(protocol_run_id: uuid.UUID) -> None:
    # The worker hydrates its MCP registry once, at startup (worker/settings.py),
    # so any server registered SINCE then -- an OKF bundle the user added
    # mid-session being the case this exists for -- isn't live in this process
    # and its tools would silently be missing from gather_tools' allow-list
    # match. Re-hydrating here picks those up; it's a cheap no-op for servers
    # already in the registry, which is every one of them on the common path.
    # Best-effort: a hydration failure costs the run whatever servers weren't
    # already live (surfacing as a normal "tool not available" at agent level),
    # which is a far better outcome than refusing to start the run at all.
    try:
        await hydrate_registry()
    except Exception:
        logger.warning("MCP registry hydration failed; continuing with the registry as-is", exc_info=True)
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
        target_node_id = run.target_node_id
        experiment = await get_experiment(db, experiment_id) if experiment_id else None
        design_spec = experiment.design_spec if experiment is not None else None

    if target_node_id:
        await _run_single_node(
            protocol_run_id,
            protocol_id=protocol_id,
            owner_id=owner_id,
            graph=graph,
            node_id=target_node_id,
            experiment_id=experiment_id,
        )
        return

    # Both None for a plain graph run. Set together only for a run created by
    # "run all cells" (plan_cell_runs) -- substitute this cell's factor
    # values into whichever fields the canvas bound to a matching factor
    # name before doing anything else, so every node below (including
    # topological_order's own validation) sees the already-patched graph.
    if factor_values:
        graph = apply_factor_bindings(graph, factor_values)

    try:
        order = topological_order(graph)
        gated_by = find_gated_pairs(graph)
        validate_coordination_strategy(design_spec, has_gated_pair=bool(gated_by))
    except ProtocolValidationError as e:
        async with get_session() as db:
            await set_status(db, protocol_run_id, status="failed", error=str(e))
        return

    effective_cell_label = _effective_cell_label(cell_label, protocol_run_id)
    workspace_id = _compute_workspace_id(experiment_id, cell_label, protocol_run_id)

    async with get_session() as db:
        await set_status(db, protocol_run_id, status="running")
        if cell_label and experiment_id:
            # Pre-write, before any node executes: a crash/timeout mid-run
            # still leaves this cell's provenance recorded (mirrors the
            # notebook's own pre-scoring upsert_cell call). workspace_id is
            # already computed above -- FactorialCellResult.workspace_id
            # existed for this before anything ever populated it.
            await upsert_cell(
                db,
                experiment_id=experiment_id,
                cell_label=cell_label,
                fields={"run_id": protocol_run_id, "factor_values": factor_values or {}, "workspace_id": workspace_id},
            )

    node_runs: dict[str, Any] = {}
    failed = False
    cancelled = False
    for node in order:
        node_id = node["id"]
        if node_id in node_runs:
            continue  # already resolved -- a critic_gate node handled via its worker's turn below
        if not failed and not cancelled:
            # Polled fresh from the DB, not a locally-cached flag -- a Stop
            # click (cancel endpoint -> request_protocol_run_cancellation)
            # is a different request, possibly handled by a different
            # worker process entirely, so this loop only ever learns about
            # it by re-reading the row between nodes. Checked once per node
            # boundary, not mid-node: whatever's currently in flight (a
            # single agent, or a gated pair's whole revision loop) always
            # finishes -- see run_protocol's own module comment for why that
            # granularity was chosen over interrupting Motoro's own
            # per-phase cancel_event mid-agent.
            async with get_session() as db:
                current = await get_protocol_run(db, protocol_run_id)
            cancelled = current is not None and current.cancel_requested_at is not None
        if failed or cancelled:
            node_runs[node_id] = {"status": "skipped"}
            async with get_session() as db:
                await update_node_run(db, protocol_run_id, node_id, {"status": "skipped"})
            continue

        if node.get("type") in _PURE_CONFIG_SOURCE_TYPES:
            # Pure config sources -- never get their own execution turn (see
            # _resolve_llm_config/_resolve_tool_config). Memory and
            # architectural-pattern nodes are visual scaffolding only this
            # phase: connecting one declares intent for a future phase, but
            # has no runtime effect yet.
            node_runs[node_id] = {"status": "completed", "output_text": None, "error": None}
            async with get_session() as db:
                await update_node_run(db, protocol_run_id, node_id, node_runs[node_id])
            continue

        async with get_session() as db:
            await update_node_run(db, protocol_run_id, node_id, {"status": "running"})

        if node_id in gated_by:
            gate = gated_by[node_id]
            worker_run, gate_run = await _run_gated_worker(
                node,
                gate,
                protocol_id=protocol_id,
                protocol_run_id=protocol_run_id,
                owner_id=owner_id,
                graph=graph,
                node_runs=node_runs,
                workspace_id=workspace_id,
                experiment_id=experiment_id,
                effective_cell_label=effective_cell_label,
            )
            node_runs[node_id] = worker_run
            node_runs[gate["id"]] = gate_run
            async with get_session() as db:
                await update_node_run(db, protocol_run_id, node_id, worker_run)
                await update_node_run(db, protocol_run_id, gate["id"], gate_run)
            if worker_run["status"] == "cancelled" or gate_run["status"] == "cancelled":
                cancelled = True
            elif worker_run["status"] == "failed" or gate_run["status"] == "failed":
                failed = True
            continue

        output_text: str | None
        error: str | None
        run_id: uuid.UUID | None
        if not _is_node_active(node):
            # Deactivated: skip this node's own logic entirely -- its
            # upstream input passes straight through as its output
            # unchanged (the standard node-disable semantic). Gated
            # workers can't reach here (topological_order already rejects
            # that combination), and pure config sources (including
            # mcp_tool) never reach this point at all, so this only ever
            # applies to a plain agent node.
            output_text, error, run_id = _upstream_output_text(graph, node_id, node_runs), None, None
        else:
            ambient_meta, seeded_dataset = await _node_run_context(graph, node_id, workspace_id, owner_id)
            user_input = _build_user_input(
                node,
                graph,
                node_runs,
                experiment_id=experiment_id,
                effective_cell_label=effective_cell_label,
                script_bound="script_path" in ambient_meta,
                seeded_dataset=seeded_dataset,
            )
            output_text, error, run_id = await _run_agent_node(
                node,
                protocol_id=protocol_id,
                protocol_run_id=protocol_run_id,
                owner_id=owner_id,
                user_input=user_input,
                graph=graph,
                workspace_id=workspace_id,
                ambient_meta=ambient_meta,
            )

        if error == _AGENT_CANCELLED:
            node_runs[node_id] = {
                "status": "cancelled",
                "output_text": None,
                "error": None,
                "run_id": str(run_id) if run_id else None,
            }
            cancelled = True
        else:
            node_runs[node_id] = {
                "status": "failed" if error else "completed",
                "output_text": output_text,
                "error": error,
                "run_id": str(run_id) if run_id else None,
            }
            if error:
                failed = True
        async with get_session() as db:
            await update_node_run(db, protocol_run_id, node_id, node_runs[node_id])

    async with get_session() as db:
        if cancelled:
            await set_status(db, protocol_run_id, status="cancelled")
        elif failed:
            await set_status(db, protocol_run_id, status="failed", error="one or more nodes failed")
        else:
            await set_status(db, protocol_run_id, status="completed")
            if cell_label and experiment_id:
                # Post-write, success only: fold the graph's single designated
                # output (the sink node's raw output_text) into this cell's
                # artifacts. There's still no generic notion of "which
                # output_contract field is the metric" for an arbitrary graph
                # -- that's what the best-effort promote_cell_score_metrics
                # call below is for for the one recognizable pipeline shape
                # (a Score agent wired to a single run_model_script call)
                # this doesn't cover, a user still promotes artifacts into
                # metric_values manually via PUT /experiments/{id}/cells/
                # {cell_label}, the same manual step the notebook's own
                # score_payload is today.
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
                # Best-effort: matches the Score/run_model_script shape ->
                # writes metric_values; doesn't match (or anything else goes
                # wrong reading Motoro's own run_steps) -> logs and
                # moves on. Never lets a promotion failure fail an otherwise-
                # successful run.
                try:
                    await promote_cell_score_metrics(
                        db, experiment_id=experiment_id, cell_label=cell_label, protocol_run_id=protocol_run_id
                    )
                except Exception:
                    logger.exception(
                        "score_metric_promotion_failed",
                        extra={"experiment_id": str(experiment_id), "cell_label": cell_label},
                    )
