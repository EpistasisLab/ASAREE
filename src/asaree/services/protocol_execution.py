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
import json
import logging
import re
import uuid
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from asaree_workspace_core import (
    TABULAR_ML,
    WORKSPACE_ROOT,
    StagePlanError,
    dataset_slot,
    resolve_stage_plan,
)
from motoro.mcp.registry import get_registry
from motoro.models.run import RunStatus
from motoro.runner import create_agent, create_run, execute_run, get_agent_by_name, get_run, update_agent
from motoro.schemas.agent import ModelConfig
from motoro.schemas.output import OutputEnvelope, parse_envelope
from motoro.schemas.pattern import PatternConfig
from motoro.security.prompt_injection import (
    fence_upstream,
    neutralize_delimiters,
)
from motoro.services.mcp_service import hydrate_registry
from motoro.services.skill_service import resolve_skills
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.config import get_settings
from asaree.models.database import get_session
from asaree.models.protocol_run import ProtocolRun
from asaree.services import prompt_references
from asaree.services.agent_cards import AgentCard, build_agent_card
from asaree.services.coordination import coordination_strategy_slug
from asaree.services.dataset_workspaces import (
    WorkspaceSeedError,
    fetch_owned_registration,
    head_data_locator,
    seed_cell_workspace,
    slot_data_locators,
)
from asaree.services.deadline import Deadline, active_deadline
from asaree.services.design_generation import get_design_impact
from asaree.services.design_revisions import get_revision as get_design_revision
from asaree.services.experiment_measurements import (
    blocking_measurement_plan_issues,
    validate_experiment_measurement_plan,
)
from asaree.services.experiments import get_experiment
from asaree.services.factor_bindings import validate_factor_bindings
from asaree.services.factorial_cells import get_replicate, list_replicates, upsert_replicate
from asaree.services.protocol_revisions import get_revision
from asaree.services.protocol_runs import (
    TERMINAL_PROTOCOL_RUN_STATUSES,
    create_protocol_run,
    get_cancel_requested_at,
    get_protocol_run,
    is_current_replicate_attempt,
    node_run_truncation,
    set_status,
    touch_protocol_run_heartbeat,
    update_node_run,
)
from asaree.services.protocols import get_protocol
from asaree.services.run_tools import gather_tools
from asaree.services.runtime_metrics import finalize_attempt_measurement
from asaree.services.system_mcp_servers import (
    DATASET_DICTIONARY_AGENT_TOOLS,
    EDA_SERVER_NAME,
    SCIKIT_LEARN_SERVER_NAME,
    SCRIPT_AGENT_TOOLS,
    SCRIPT_SERVER_NAME,
    STAGE_WRITING_SERVERS,
    UNSPLIT_DATASET_AGENT_TOOLS,
    WORKSPACE_AGENT_TOOLS,
    WORKSPACE_SERVER_NAME,
)

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
#    agent's stored config (``_resolve_model_config``, ``_resolve_tool_config``,
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
# Script works the same way, for the same reason: a REFERENCE (its code written
# to disk and bound ambiently, so what runs never passes through the model) plus
# the CAPABILITY that reference is useless without -- ``asaree-script``'s
# ``run_wired_script``, granted by ``_resolve_script_tool_config``. Wiring a
# script is what declares that the agent should run one.
#
# The connector-typed slots on an agent/critic_gate node. model/tool/memory are
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
# "ai", "llm", and "resource" are pre-rename spellings of "model" and
# "dataset" (see _LEGACY_MODEL_HANDLES/_LEGACY_DATASET_HANDLES): an un-migrated
# edge must still be recognised as a connector, or it would be misread as a
# main pipeline edge and turn a perfectly good graph into a cycle/ordering
# error.
_CONNECTOR_HANDLES = frozenset(
    {
        "model",
        "ai",
        "llm",
        "tool",
        "memory",
        "architectural_pattern",
        "dataset",
        "resource",
        "skill",
        "knowledge",
        "output_parser",
    }
)

# Each connector slot accepts a FAMILY of node types, not one exact type --
# mirrors how "tool" already accepts any mcp_tool node. LLM and Architectural
# Pattern are each split one-node-per-provider/pattern -- a dedicated node per
# capability rather than one generic node with an internal picker -- instead of
# a single generic node with a Provider/kind field -- config shape is identical
# across LLM providers (provider is baked into the node type instead of a
# user-editable field), but genuinely differs per architectural pattern (see
# each pattern's own NodeConfig on the frontend), so the Model family shares
# one inspector while each pattern gets its own.
_MODEL_NODE_TYPES = frozenset(
    {"model_anthropic", "model_openai", "model_azure_foundry", "model_openrouter", "model_local"}
)
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
# An Output Parser node carries the field spec Motoro's output_contract
# machinery extracts a typed payload with (motoro.services.output_contract's
# extract_payload). It used to be a field on the agent itself
# (``config.output_contract``) and became a node for the same reason model,
# tools and pattern did -- see _run_agent_node's own comment -- but with one
# extra argument the others don't have: extraction is a *second LLM call* per
# run, made after the agent has already finished writing, so its cost has to
# be visible on the canvas rather than buried in one node's settings tab.
#
# Being a node also fixes what the field could not: a parser contributes its
# field list to the producer's prompt (_output_shape_block), so the extractor
# reads text that was actually asked to contain the fields it wants. The field
# never did that -- the agent was never told the contract existed.
#
# Capped at one, like Memory and unlike Tool/Skill/Knowledge: two field specs
# for one output is an ambiguity, not a richer declaration.
_OUTPUT_PARSER_NODE_TYPES = frozenset({"output_parser"})

# Every node type that's a pure config source -- never gets its own execution
# turn, never a pipeline "final output" (see sink_node_ids/run_protocol's
# main loop), and may only ever emit its own connector-typed edge (see the
# "outgoing wrong handle" check in topological_order below).
_PURE_CONFIG_SOURCE_TYPES = (
    _MODEL_NODE_TYPES
    | _EXECUTION_PATTERN_NODE_TYPES
    | _MEMORY_NODE_TYPES
    | _MCP_TOOL_NODE_TYPES
    | _DATASET_NODE_TYPES
    | _SCRIPT_NODE_TYPES
    | _SKILL_NODE_TYPES
    | _KNOWLEDGE_NODE_TYPES
    | _OUTPUT_PARSER_NODE_TYPES
)

# Which connector handle each pure-config-source node type may exclusively
# emit into, and the human-facing label for that handle -- both keyed off
# the same family grouping so a new provider/pattern node type only needs
# adding to _MODEL_NODE_TYPES/_EXECUTION_PATTERN_NODE_TYPES above, not a
# second lookup.
_NODE_TYPE_TO_HANDLE: dict[str, str] = {
    **{t: "model" for t in _MODEL_NODE_TYPES},
    **{t: "architectural_pattern" for t in _EXECUTION_PATTERN_NODE_TYPES},
    **{t: "memory" for t in _MEMORY_NODE_TYPES},
    # Script still shares the Tool connector rather than getting its own slot
    # -- one connector accepting a FAMILY of node types (see this dict's own
    # docstring above _MODEL_NODE_TYPES). Both are pure config sources an
    # agent's Tool "+" panel can add (AddNodePanel filters its catalog by
    # CONNECTOR_PANEL_INFO.tool's allowedTypes on the frontend); which one a
    # given wired node actually IS is recovered by checking the source node's
    # own `type`, not by which handle it's on (see _resolve_tool_config/
    # _resolve_script_configs, and the per-agent validation block below).
    #
    # Dataset used to be in that same shared bucket and no longer is: it has
    # its own slot, named after the node type itself since `dataset` is the
    # only member of the family.
    **{t: "tool" for t in _MCP_TOOL_NODE_TYPES},
    **{t: "dataset" for t in _DATASET_NODE_TYPES},
    **{t: "tool" for t in _SCRIPT_NODE_TYPES},
    **{t: "skill" for t in _SKILL_NODE_TYPES},
    **{t: "knowledge" for t in _KNOWLEDGE_NODE_TYPES},
    **{t: "output_parser" for t in _OUTPUT_PARSER_NODE_TYPES},
}
# The user-facing name of each connector slot -- mirrors
# CONNECTOR_SLOT_LABELS on the frontend, so a validation error always names
# the connector by the caption printed next to it on the canvas.
_HANDLE_LABELS: dict[str, str] = {
    "model": "Model",
    "ai": "Model",  # pre-rename spellings, same slot -- see _LEGACY_MODEL_HANDLES
    "llm": "Model",
    "memory": "Memory",
    "architectural_pattern": "Architectural Pattern",
    "tool": "Tool",
    "dataset": "Dataset",
    "resource": "Dataset",  # pre-rename spelling, same slot -- see _LEGACY_DATASET_HANDLES
    "skill": "Skill",
    "knowledge": "Knowledge",
    "output_parser": "Output Parser",
}

# Connector slots have been renamed twice since graphs started being saved,
# and a stored graph is an opaque JSONB blob, so every spelling has to keep
# resolving:
#
#   "llm" -> "ai" -> "model"  the Model connector (migrations
#                              3f1a7c9b2e04 and the Model-schema migration)
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
_LEGACY_MODEL_HANDLES = frozenset({"model", "ai", "llm"})
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
    "model": _LEGACY_MODEL_HANDLES,
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
# reality. "peer_collaboration" is the third, and the only one that changes how
# a cell run executes at all: the graph runs as a conversation (see
# ``services.agent_messenger``) instead of as a one-pass DAG walk, so connected
# agents can consult each other while working, and each declared cell/replicate
# still records exactly one result the same way.
#
# "supervisor_architecture" is the fourth, and the second that changes how a
# cell run executes: one supervisor dispatches to N workers (in parallel by
# default), an optional reviewer reports on their output, and the supervisor
# synthesizes. Every one of those turns is forced by ASAREE rather than chosen
# by a model -- see ``resolve_supervisor_roles`` and
# ``execute_supervisor_architecture``.
#
# Five further slugs mirroring ARES's own coordination categories (swarm/
# task-bidding/supervision-tree/event-driven/multi-agent-planning) used to
# be offered as named placeholders. They were removed from the picker rather
# than left selectable-but-rejected -- an option that always errors is worse
# than an option that isn't there. The frozenset stays so an experiment whose
# design_spec still names one gets that explanation instead of a bare "unknown".
# ``supervisor_architecture`` was in it until it was actually built.
_RETIRED_COORDINATION_STRATEGIES = frozenset(
    {
        "swarm_architecture",
        "task_bidding",
        "supervision_tree_with_guarded_capabilities",
        "event_driven_reactivity",
        "multi_agent_planning",
    }
)

#: Strategies whose cells are orchestrated turn by turn instead of walked as a
#: DAG -- see :func:`is_conversation_strategy` for what that suspends.
_CONVERSATION_STRATEGIES = frozenset({"peer_collaboration", "supervisor_architecture"})


def is_conversation_strategy(design_spec: dict[str, Any] | None) -> bool:
    """Whether this experiment's cells execute as a conversation rather than a
    pipeline, which decides how much of the pipeline's structural validation
    still applies to the canvas.

    Two of those requirements -- that the graph is acyclic, and that it has
    exactly one sink whose output is the deliverable -- describe walking a graph
    in dependency order, not being a valid graph. ``run_protocol``'s
    ``peer_collaboration`` branch discards the topological sort outright and
    takes the result from the conversation lead, so neither requirement is a
    fact about a valid conversation, and both reject the topology this strategy
    exists for: agents wired to each other in a loop, which has no unfed node to
    start from and no sink at all. Every other check (a non-empty graph,
    critic-gate shape, factor bindings) is about the canvas itself and still
    applies.

    ``supervisor_architecture`` is here for the same reason and not by analogy:
    its target topology -- workers reporting to a reviewer that reports to the
    supervisor -- is a cycle through the supervisor, and its result comes from
    the supervisor rather than from a sink. It is orchestrated turn by turn
    (:func:`execute_supervisor_architecture`), so it never walks the sort
    either.
    """
    return coordination_strategy_slug(design_spec) in _CONVERSATION_STRATEGIES


def _derived_stage(stage_id: str, position: int) -> dict[str, Any]:
    """One derived stage descriptor, keeping the preset's meaning where it has one.

    A stage id the preset knows (``dc``/``fte``/``fs``) comes back with the
    preset's label, gate and ``fixed_input`` intact -- those are the domain
    invariants that make the stage worth gating at all, and the server writing
    the stage is the same server either way. Only ``version_id`` is renumbered,
    to this stage's actual position, so a canvas that wires DC and FS without
    FTE produces ``v1_dc -> v2_fs`` rather than a gap.
    """
    known = next((stage for stage in TABULAR_ML.stages if stage.id == stage_id), None)
    raw = known.as_dict() if known is not None else {"id": stage_id, "label": stage_id}
    return {**raw, "version_id": f"v{position}_{stage_id}"}


def derive_stage_plan(graph: dict[str, Any]) -> Any:
    """The stage plan this canvas already describes, or ``None`` for the default.

    A stage plan is not something a user should have to write down: the canvas
    has already said which staged steps this experiment has, by which
    stage-writing MCP servers it wires into its agents
    (``system_mcp_servers.STAGE_WRITING_SERVERS``). Reading it back off the
    wiring keeps the graph the single source of truth, the same way the
    coordination strategies read their topology off it rather than off a second
    declaration that can disagree.

    Order is the agents' own pipeline order (:func:`_kahn_order`), because a
    stage reads the accepted output of the stage before it and that ordering is
    exactly what the canvas draws. The unvalidated walk, deliberately: this runs
    against a half-wired draft too, and a canvas that isn't runnable yet should
    say so through validation, not by making stage derivation raise.

    Returns ``None`` -- meaning the default preset, byte for byte -- in the two
    cases where deriving must change nothing:

    * the canvas wires no stage server at all, which is every generic agent team
      (they never stage, so the plan is irrelevant to them), and
    * the derived stages are ``tabular_ml``'s exactly, which is the published
      spinal canvas. Returning ``None`` rather than an equal-looking inline copy
      is deliberate: it is the same value that canvas resolved before deriving
      existed, so nothing is recorded in ``state.json`` and the on-disk format
      cannot drift. See ``tests/test_spinal_compat.py``.

    The case that is *not* a no-op is a partial pipeline. A canvas wiring DC and
    FS but no FTE used to get the full triple regardless, so FS read a version
    FTE never accepted and the run stalled on a lineage error the user had no
    way to connect to the wiring. Now that canvas simply has two stages.
    """
    nodes, ordered, _ = _kahn_order(graph)
    stage_ids: list[str] = []
    for nid in ordered:
        node = nodes[nid]
        if node.get("type") != "agent":
            continue
        for edge in _edges_with_handle(graph, nid, "tool", direction="incoming"):
            source = nodes.get(str(edge.get("source")))
            if source is None or source.get("type") not in _MCP_TOOL_NODE_TYPES:
                continue
            config = (source.get("data") or {}).get("config") or {}
            if not config.get("enabled", True):
                continue
            stage_id = STAGE_WRITING_SERVERS.get(str(config.get("server_name") or ""))
            if stage_id is not None and stage_id not in stage_ids:
                stage_ids.append(stage_id)
    if not stage_ids or stage_ids == TABULAR_ML.ids:
        return None
    return {
        "name": "canvas",
        "stages": [_derived_stage(stage_id, i + 1) for i, stage_id in enumerate(stage_ids)],
    }


def stage_plan_spec(design_spec: dict[str, Any] | None, *, graph: dict[str, Any] | None = None) -> Any:
    """The workspace stage plan for this run, or ``None`` for the default.

    Returned unresolved, on purpose: ``asaree_workspace_core.stages`` owns what a
    plan means (and rejects a malformed one), while this only knows where the
    declaration comes from. ``None`` is passed straight through to the workspace,
    where it means "adopt whatever this cell already stages through" rather than
    "the default preset" -- so an experiment that never declared a plan behaves
    exactly as it did before plans existed.

    Two sources, in this order. An explicit ``design_spec["stage_plan"]`` wins:
    it is the SDK/notebook escape hatch for a pipeline the canvas cannot express,
    and the only way to name a preset outright. Otherwise the plan is derived
    from the canvas (:func:`derive_stage_plan`) -- which is how the GUI gets one,
    since there is deliberately no stage-plan field in the Design tab for a user
    to fill in.
    """
    declared = (design_spec or {}).get("stage_plan") or None
    if declared is not None:
        return declared
    return derive_stage_plan(graph) if graph is not None else None


def validate_stage_plan(design_spec: dict[str, Any] | None) -> None:
    """Reject a malformed declared stage plan before anything runs.

    Checked alongside :func:`validate_coordination_strategy` rather than left to
    the first seeding call, which happens inside a run whose failure is logged
    and swallowed: a typo'd gate rule would otherwise silently give the whole
    experiment the default pipeline and look like it worked.

    Only the *declared* plan is checked -- deliberately no graph argument. A
    derived plan is built by :func:`derive_stage_plan` out of a fixed registry,
    so it cannot carry a typo; the thing that can is the SDK escape hatch, and
    that is what this guards.
    """
    spec = (design_spec or {}).get("stage_plan") or None
    if spec is None:
        return
    try:
        resolve_stage_plan(spec)
    except StagePlanError as e:
        raise ProtocolValidationError(f"This experiment's stage plan is not usable: {e}") from e


def validate_coordination_strategy(design_spec: dict[str, Any] | None, *, graph: dict[str, Any]) -> None:
    """Checks the experiment's declared strategy against the protocol it will
    run. Takes the whole graph rather than a pre-computed fact about it because
    each strategy asks a different question of the canvas."""
    slug = coordination_strategy_slug(design_spec)
    if slug == "sequential":
        validate_sequential_chain(graph)
        return
    if slug == "critic_gate":
        if not find_gated_pairs(graph):
            raise ProtocolValidationError(
                "This experiment's coordination strategy is 'Critic Gate' but this protocol has no Critic Gate "
                "node wired in -- add one, or change the coordination strategy on the Design tab."
            )
        return
    if slug == "peer_collaboration":
        # Resolving the entry agent *is* the validation: it fails unless the
        # canvas has connected agents and says unambiguously which one leads.
        validate_conversation_entry(graph, resolve_conversation_entry_id(graph))
        return
    if slug == "supervisor_architecture":
        # Reading the roles *is* the validation, the same way it is for peer:
        # it fails unless the canvas says unambiguously who supervises, who
        # works and who reviews.
        resolve_supervisor_roles(graph)
        return
    if slug in _RETIRED_COORDINATION_STRATEGIES:
        raise ProtocolValidationError(
            f"Coordination strategy {slug!r} was never implemented and is no longer offered -- "
            "pick another one on the Design tab."
        )
    raise ProtocolValidationError(f"Unknown coordination strategy: {slug!r}")


def _sequential_agent_links(graph: dict[str, Any]) -> tuple[list[str], dict[str, list[str]], dict[str, list[str]]]:
    """The agent-to-agent handoff graph of *graph*: ``(agents, successors,
    predecessors)``, each mapping keyed by every agent id.

    A handoff is an agent-to-agent **path** over main edges, not necessarily a
    single edge, because non-agent nodes on the main flow are passthrough
    plumbing rather than links in the chain: the spinal pipeline's own shape is
    ``agent -> critic_gate -> agent``, and a Script node between two agents is
    just as legitimate. Counting raw edges would read both as two disjoint
    one-agent chains.

    Connector edges (LLM, Dataset, Pattern, Tool, ...) are excluded outright.
    They are configuration, and their fan-in must stay unrestricted -- one LLM
    node feeding every agent in a chain is the normal shape, not a fork.
    """
    nodes = {str(n.get("id")): n for n in graph.get("nodes") or [] if n.get("id")}
    agents = [nid for nid, node in nodes.items() if node.get("type") == "agent"]
    downstream: dict[str, list[str]] = {nid: [] for nid in nodes}
    for edge in graph.get("edges") or []:
        if edge.get("targetHandle") in _CONNECTOR_HANDLES:
            continue
        source, target = str(edge.get("source")), str(edge.get("target"))
        if source in nodes and target in nodes:
            downstream[source].append(target)

    successors: dict[str, list[str]] = {nid: [] for nid in agents}
    predecessors: dict[str, list[str]] = {nid: [] for nid in agents}
    for agent_id in agents:
        # Breadth-first through the non-agent plumbing, stopping at the first
        # agent on each branch. `seen` also makes a cycle in that plumbing
        # terminate rather than spin.
        frontier = list(downstream[agent_id])
        seen = {agent_id}
        while frontier:
            current = frontier.pop(0)
            if current in seen:
                continue
            seen.add(current)
            if nodes[current].get("type") == "agent":
                if current not in successors[agent_id]:
                    successors[agent_id].append(current)
                    predecessors[current].append(agent_id)
                continue
            frontier.extend(downstream[current])
    return agents, successors, predecessors


def sequential_chain_order(graph: dict[str, Any]) -> list[str]:
    """The agents of a ``sequential`` protocol, in the order they hand off.

    Returns ``[]`` for a graph with no agents. Assumes the chain rule already
    holds -- :func:`validate_sequential_chain` is what establishes that, and it
    calls this to do the walk.
    """
    agents, successors, predecessors = _sequential_agent_links(graph)
    ordered: list[str] = []
    seen: set[str] = set()
    for head in [nid for nid in agents if not predecessors[nid]]:
        cursor: str | None = head
        while cursor is not None and cursor not in seen:
            ordered.append(cursor)
            seen.add(cursor)
            following = successors[cursor]
            cursor = following[0] if following else None
    # A cycle has no head at all, so its members are unreachable from one --
    # appended in declaration order so the caller can still name them.
    ordered.extend(nid for nid in agents if nid not in seen)
    return ordered


def validate_sequential_chain(graph: dict[str, Any]) -> None:
    """``sequential`` means a chain, not any DAG.

    This branch used to be a bare ``return``, so the strategy imposed no shape
    at all: an agent could fan out to three others, or three could fan into one,
    and the run would still be called "sequential" because ``run_protocol``
    walks whatever ``topological_order`` hands it. The walk itself has always
    been mandatory and exhaustive -- there is no handoff tool and no way for an
    agent to skip its successor -- so what was missing was never the execution
    guarantee, only the guarantee that the *topology* is what the word says.

    Checked over the agent handoff graph (see :func:`_sequential_agent_links`),
    so a critic gate or a Script node between two agents keeps the chain a
    chain, and connector fan-in is not a fork.

    Zero agents, and one agent with nothing wired to it, are both valid -- that
    is the single-agent case, and every ``sequential`` experiment that existed
    when this check landed. The rule is enforced hard rather than grandfathered
    behind a flag because none of them had a single agent-to-agent handoff to
    break.
    """
    nodes = {str(n.get("id")): n for n in graph.get("nodes") or [] if n.get("id")}
    agents, successors, predecessors = _sequential_agent_links(graph)
    if len(agents) < 2:
        return

    def _name(node_id: str) -> str:
        return _node_display_name(nodes[node_id])

    for nid in agents:
        if len(successors[nid]) > 1:
            names = ", ".join(sorted(_name(t) for t in successors[nid]))
            raise ProtocolValidationError(
                f"{_name(nid)!r} hands off to more than one agent ({names}), but this experiment's coordination "
                "strategy is 'Sequential', where each agent has exactly one successor. Remove the extra "
                "connections, or switch the strategy to 'Peer Collaboration' on the Design tab."
            )
        if len(predecessors[nid]) > 1:
            names = ", ".join(sorted(_name(s) for s in predecessors[nid]))
            raise ProtocolValidationError(
                f"More than one agent hands off to {_name(nid)!r} ({names}), but this experiment's coordination "
                "strategy is 'Sequential', where each agent has exactly one predecessor. Remove the extra "
                "connections, or switch the strategy to 'Peer Collaboration' on the Design tab."
            )

    heads = [nid for nid in agents if not predecessors[nid]]
    if not heads:
        raise ProtocolValidationError(
            "Every agent in this protocol is fed by another agent, so a 'Sequential' run has nowhere to start -- "
            "which is what happens when the agents are wired in a loop. Break the loop so one agent leads, or "
            "switch the strategy to 'Peer Collaboration' on the Design tab."
        )

    # More than one head, or an agent no head can reach, both mean the same
    # thing: the canvas holds several independent runs rather than one. Reported
    # as separate messages because the fix differs -- join them, or delete the
    # stranded one.
    ordered = sequential_chain_order(graph)
    reachable: set[str] = set()
    cursor: str | None = heads[0]
    while cursor is not None and cursor not in reachable:
        reachable.add(cursor)
        following = successors[cursor]
        cursor = following[0] if following else None
    if len(heads) > 1:
        names = ", ".join(sorted(_name(nid) for nid in heads))
        raise ProtocolValidationError(
            f"This protocol has {len(heads)} separate agent chains, starting at {names}. A 'Sequential' experiment "
            "runs one chain -- connect them end to end, or switch the strategy to 'Peer Collaboration' on the "
            "Design tab."
        )
    stranded = [nid for nid in ordered if nid not in reachable]
    if stranded:
        names = ", ".join(sorted(_name(nid) for nid in stranded))
        raise ProtocolValidationError(
            f"{names} cannot be reached from the start of the chain, so a 'Sequential' run would never get to "
            "them. Wire them into the chain, or switch the strategy to 'Peer Collaboration' on the Design tab."
        )


#: Agent node ``data`` flag marking that agent as the one a conversation starts
#: at. An explicit override of the wiring rule in
#: :func:`resolve_conversation_entry_id`, deliberately not a second mechanism
#: alongside it -- see that function for when each applies.
_CONVERSATION_LEAD_FIELD = "conversation_lead"


def _is_marked_lead(node: dict[str, Any]) -> bool:
    data = node.get("data")
    return isinstance(data, dict) and data.get(_CONVERSATION_LEAD_FIELD) is True


def resolve_conversation_entry_id(graph: dict[str, Any]) -> str:
    """Which agent the user's question goes to when this graph runs as a
    conversation.

    Two sources, in this order: an agent explicitly marked as the lead on the
    canvas wins, and failing that it's derived from the wiring -- the
    peer-connected agent that nothing feeds, i.e. exactly the node a pipeline
    run would have started at. A peer edge is undirected for *consultation*, but
    the user still drew it in a direction, and absent a marker that direction is
    the only statement of intent available.

    Derivation alone is not enough, because it quietly assumes a DAG. The
    topology this strategy most invites -- every agent wired to every other --
    is a cycle, and in a cycle *every* agent is fed, so the derivation finds no
    candidate at all and could only tell the user to unwire something. The
    marker is how you say "this one leads" without breaking the shape you meant
    to draw. Derivation stays the default so a plain chain still needs no
    configuration, and so nothing saved before the marker existed changes
    behavior.

    One agent has to lead either way: its answer is the run's result, so if two
    are equally plausible there's no honest way to pick, and guessing would
    silently drop half the canvas out of the run.
    """
    nodes = {str(n.get("id")): n for n in graph.get("nodes") or [] if n.get("id")}
    peer_agents = [nid for nid in nodes if nodes[nid].get("type") == "agent" and _connected_agent_ids(graph, nid)]
    if not peer_agents:
        raise ProtocolValidationError(
            "This experiment's coordination strategy is 'Peer Collaboration' but no two Agent nodes on this "
            "protocol are connected, so nobody has anyone to talk to -- draw an edge between two agents, or "
            "change the coordination strategy on the Design tab."
        )

    marked = [nid for nid in nodes if nodes[nid].get("type") == "agent" and _is_marked_lead(nodes[nid])]
    if len(marked) > 1:
        names = ", ".join(sorted(_node_display_name(nodes[nid]) for nid in marked))
        raise ProtocolValidationError(
            f"More than one agent is marked as the conversation lead ({names}). Only one agent can lead a "
            "conversation -- unmark the others."
        )
    if marked:
        # Checked against the peer cluster rather than just the graph: honoring
        # a marker on an unconnected agent would run a "conversation" with one
        # participant, which is the one case where the marker must not win.
        if marked[0] not in peer_agents:
            raise ProtocolValidationError(
                f"{_node_display_name(nodes[marked[0]])!r} is marked as the conversation lead but isn't connected "
                "to another agent, so it has nobody to talk to. Connect it to a peer, or mark a different agent."
            )
        return marked[0]

    # Connector-typed edges are configuration, not upstream work, so an agent
    # with only an LLM/Dataset/Tool wired into it is still a starting point.
    fed = {
        str(edge.get("target"))
        for edge in graph.get("edges") or []
        if edge.get("targetHandle") not in _CONNECTOR_HANDLES
    }
    entries = [nid for nid in peer_agents if nid not in fed]
    if len(entries) == 1:
        return entries[0]
    if not entries:
        raise ProtocolValidationError(
            "Every connected agent in this protocol has something feeding into it, so there's no obvious agent "
            "to start the conversation -- which is what happens whenever the agents are wired in a loop. Mark one "
            "of them as the conversation lead in its node settings, or leave one agent's main input unwired."
        )
    names = ", ".join(sorted(_node_display_name(nodes[nid]) for nid in entries))
    raise ProtocolValidationError(
        f"This protocol has more than one agent that could start the conversation ({names}). Mark one of them as "
        "the conversation lead in its node settings, or wire them so a single agent leads."
    )


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
    "output_parser": "Output Parser",
    "pattern_reason_act": "Reason + Act",
    "pattern_single_agent_baseline": "Single-Agent Baseline",
    "model_anthropic": "Anthropic",
    "model_openai": "OpenAI",
    "model_azure_foundry": "Azure AI Foundry",
    "model_openrouter": "OpenRouter",
    "model_local": "Local",
}


def _node_display_name(node: dict[str, Any]) -> str:
    """A validation-error-friendly name for a node -- its canvas label if the
    user has set one (matching what they'd actually see in the inspector
    header/on the card), else the same placeholder text the frontend shows
    for an unnamed node of that type (EditableNodeTitle's own `placeholder`
    prop, or the provider label for the three Model node types). Never the
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


def _kahn_order(graph: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str], bool]:
    """The dependency-order walk on its own, with no opinion about validity.

    Split out of :func:`topological_order` for callers that only want to know
    what order the canvas draws -- :func:`derive_stage_plan` reads a half-built
    graph while the user is still wiring it, and a missing Model connection there
    is a thing to report on the canvas, not a reason for stage derivation to
    raise. Returns the node map, the walk, and whether every node was reached
    (``False`` is the cycle signature); unreached nodes are appended in
    declaration order so the walk always covers the graph.
    """
    nodes, downstream, upstream = _adjacency(graph)
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
    complete = len(ordered) == len(nodes)
    if not complete:
        reached = set(ordered)
        ordered.extend(nid for nid in nodes if nid not in reached)
    return nodes, ordered, complete


def topological_order(graph: dict[str, Any], *, require_acyclic: bool = True) -> list[dict[str, Any]]:
    """Kahn's algorithm. Raises :class:`ProtocolValidationError` on an empty
    graph, a cycle (any node Kahn's algorithm can't reach stays with a
    nonzero in-degree, which is exactly the cycle signature), or a malformed
    critic-gate topology: a ``critic_gate`` node must have exactly one
    incoming edge, from an ``agent`` node, and that agent node's *only*
    outgoing edge must be to this gate -- no fan-out around a gate, since
    anything wanting the reviewed output must consume it after the gate.

    *require_acyclic* exists because the acyclic requirement is the only one of
    those three that is a fact about *walking* a graph in dependency order
    rather than a fact about a valid graph. A ``peer_collaboration`` run never
    walks one -- see ``is_conversation_strategy`` -- so it passes ``False`` to
    keep the empty-graph and critic-gate checks while dropping the check that
    would reject the topology that strategy exists for. Nodes a cycle leaves
    unreachable are appended in declaration order, since with the sort's
    premise gone there is no order left to claim.
    """
    _, downstream, _ = _adjacency(graph)
    nodes, ordered, complete = _kahn_order(graph)
    if not nodes:
        raise ProtocolValidationError("This protocol has no nodes.")
    if not complete and require_acyclic:
        raise ProtocolValidationError("This protocol's graph has a cycle -- it can't be run in dependency order.")

    for nid, node in nodes.items():
        if node.get("type") != "critic_gate":
            continue
        # Main-pipeline incoming edges only -- a gate's own Model connector
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
            model_edges = _edges_with_handle(graph, nid, "model", direction="incoming")
            if len(model_edges) != 1:
                raise ProtocolValidationError(
                    f"Node {name!r} must have exactly one Model connection (found {len(model_edges)})."
                )
            model_source = nodes.get(model_edges[0]["source"])
            if model_source is None or model_source.get("type") not in _MODEL_NODE_TYPES:
                raise ProtocolValidationError(f"Node {name!r}'s Model connection must come from a Model node.")

        tool_edges = _edges_with_handle(graph, nid, "tool", direction="incoming")
        memory_edges = _edges_with_handle(graph, nid, "memory", direction="incoming")
        pattern_edges = _edges_with_handle(graph, nid, "architectural_pattern", direction="incoming")
        dataset_slot_edges = _edges_with_handle(graph, nid, "dataset", direction="incoming")
        skill_edges = _edges_with_handle(graph, nid, "skill", direction="incoming")
        knowledge_edges = _edges_with_handle(graph, nid, "knowledge", direction="incoming")
        parser_edges = _edges_with_handle(graph, nid, "output_parser", direction="incoming")
        if node_type == "agent":
            # The Tool connector accepts a family of source types -- an
            # mcp_tool node contributes a callable capability, while a
            # Script node contributes declarative config/context (see
            # _resolve_tool_config/_resolve_script_configs) -- so which
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
            # Script is uncapped too: each Script node is materialized and
            # exposed by name/id, so several scripts are no longer ambiguous.
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
            # Capped at one, like Memory -- see _OUTPUT_PARSER_NODE_TYPES.
            if len(parser_edges) > 1:
                raise ProtocolValidationError(
                    f"Node {name!r} can have at most one Output Parser connection (found {len(parser_edges)})."
                )
            for edge in parser_edges:
                parser_source = nodes.get(edge["source"])
                if parser_source is None or parser_source.get("type") not in _OUTPUT_PARSER_NODE_TYPES:
                    raise ProtocolValidationError(
                        f"Node {name!r}'s Output Parser connection must come from an Output Parser node."
                    )
            # A wired parser AND the agent's own legacy `config.output_contract`
            # is genuinely ambiguous -- both are complete field specs for the
            # same output, and picking one by precedence would silently ignore
            # the other. Refused here rather than resolved, because the fix is
            # a two-second decision the user is better placed to make than a
            # rule is. The legacy field on its own keeps working forever
            # (_resolve_output_contract): every published revision that has one
            # is immutable, and the SDK can still set it.
            if parser_edges and ((node.get("data") or {}).get("config") or {}).get("output_contract"):
                raise ProtocolValidationError(
                    f"Node {name!r} has both an Output Parser connection and its own stored output contract. "
                    "Convert the stored one to a node, or remove it, so there is one output shape."
                )
        elif (
            tool_edges
            or memory_edges
            or pattern_edges
            or dataset_slot_edges
            or skill_edges
            or knowledge_edges
            or parser_edges
        ):
            raise ProtocolValidationError(
                f"Only Agent nodes can have a Tool, Memory, Architectural Pattern, Skill, Dataset, "
                f"Knowledge, or Output Parser connection (node {name!r})."
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


def referenceable_node_ids(graph: dict[str, Any], node_id: str) -> list[str]:
    """Which nodes *node_id*'s prompt may reference, in canvas declaration order.

    Exactly the nodes that provably run before this one: its main-edge
    ancestors, transitively. This single function answers the question for both
    sides of the feature, and that is the point --

    * the inspector's picker offers this set, so a user cannot insert a
      reference that will be refused later, and
    * :func:`validate_prompt_references` rejects anything outside it.

    Two rules fall out of following **main** edges only (:func:`_upstream_ids`),
    rather than every edge:

    * A connector node (llm/memory/pattern/mcp_tool/dataset) is never
      referenceable. Its "output" is an inert placeholder, so a reference to one
      would resolve to noise -- the same reason it is excluded from upstream
      context.
    * A node on a parallel branch is never referenceable, even though the
      topological walk happens to put it earlier. There is no ordering guarantee
      between branches, so such a reference cannot be relied on to resolve. Only
      ancestry, not walk position, establishes "runs before".

    Transitive, not just direct predecessors: on ``A -> B -> C``, C may
    reference A. That is the whole reach-back capability, and it costs nothing
    here because ``node_runs`` already retains every executed node for the whole
    run.
    """
    upstream: dict[str, list[str]] = {}
    for edge in graph.get("edges") or []:
        if edge.get("targetHandle") in _CONNECTOR_HANDLES:
            continue
        source, target = edge.get("source"), edge.get("target")
        if source and target:
            upstream.setdefault(target, []).append(source)

    ancestors: set[str] = set()
    # Iterative, and guarded by `ancestors` itself: this runs during validation,
    # which is exactly when the graph may still contain the cycle that
    # `topological_order` is about to reject.
    frontier = list(upstream.get(node_id) or [])
    while frontier:
        current = frontier.pop()
        if current in ancestors or current == node_id:
            continue
        ancestors.add(current)
        frontier.extend(upstream.get(current) or [])
    return [nid for n in graph.get("nodes") or [] if (nid := n.get("id")) in ancestors]


# design_spec factor names (e.g. "Azure Foundry:Model", "Critic enabled") are
# free text, joined into a real cell_label like "Azure Foundry:Effort:medium__
# Azure Foundry:Model:sonnet__Critic enabled:off" -- a string
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

    ``""`` when no materialization id was supplied or the write failed.

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


def _script_workspace_id(workspace_id: str | None, protocol_run_id: uuid.UUID | None, agent_node_id: str) -> str | None:
    """Choose real workspace storage or an isolated standalone-run directory."""
    safe_agent = _UNSAFE_WORKSPACE_LABEL_CHAR.sub("_", agent_node_id)
    return workspace_id or (f"_protocol_runs/{protocol_run_id}/{safe_agent}" if protocol_run_id else None)


def _cleanup_adhoc_scripts(ambient_meta: dict[str, Any] | None) -> None:
    """Remove standalone-run script files after their consuming agent stops."""
    adhoc_root = (Path(WORKSPACE_ROOT).resolve() / "_protocol_runs").resolve()
    for item in (ambient_meta or {}).get("script_paths") or []:
        path = Path(str(item.get("path") or "")).resolve()
        if adhoc_root not in path.parents:
            continue
        with contextlib.suppress(OSError):
            path.unlink()
        for directory in (path.parent, path.parent.parent, path.parent.parent.parent):
            with contextlib.suppress(OSError):
                directory.rmdir()


def _ambient_meta_for(
    graph: dict[str, Any],
    node_id: str,
    workspace_id: str | None = None,
    *,
    script_workspace_id: str | None = None,
    slots: tuple[str, ...] = (),
) -> dict[str, Any]:
    """The node's Reference-route values, for Motoro's caller-ambient ``_meta``.

    Motoro lifts ``run_metadata["ambient_meta"]`` onto every MCP tool call as
    ``motoro.ambient.<key>`` (see its ``mcp/adapters``), which is the product's
    own half of the channel ``workspace_id`` already uses -- the model never
    sees these and so can never mistype one.

    Four keys today, all Reference-route (see the three routes at the top of
    this module, and Motoro's ``engine/sense.py``):

    * ``dataset_names`` -- the wired Dataset connectors' names, which is what
      lets ``open_workspace`` be called with no arguments at all.
    * ``data_path`` / ``target_column`` -- the cell workspace's HEAD train
      parquet and its outcome column (``head_data_locator``), for the tools
      that take a dataset as a path rather than reading the workspace
      themselves (``scikit-learn-mcp``). Keyed off ``workspace_id`` alone, NOT
      off this node having a Dataset connector: the workspace belongs to the
      cell, and the nodes most likely to want a path are the ones with no
      Dataset node wired. In the spinal graph the connector is on DC/FTE/FS
      while the model-fitting steps (MLM, Score) ride on the ambient
      workspace_id -- gating this on a wired dataset left exactly those two
      with no path, which is the whole reason a path is bound at all.
      ``head_data_locator`` is total, so a run whose workspace was never
      seeded contributes nothing here -- and ``_node_run_context`` then falls
      back to the wired dataset's own raw file, which is how an unsplit
      dataset (one that has no workspace at all) reaches those tools.
      A workspace holding SEVERAL slots binds no ``data_path`` at all: there
      is no single HEAD, and picking one dataset out of several would be a
      guess. ``data_slots`` carries the per-slot view instead.
    * ``data_slots`` -- present only when the workspace holds more than one
      slot: ``{slot: {name, head, data_path, target_column}}``, so a tool that
      takes a path can be pointed at a specific lineage and ``workspace_status``
      can report all of them. Left absent in the ordinary one-dataset case so
      nothing has to read it to find "the" dataset.
    * ``script_paths`` -- every wired Script node as
      ``[{id, name, path}, ...]`` in canvas wiring order. A script-running
      tool selects one by name or node id and reads the file, so what executes
      is byte-for-byte what the user wrote. With exactly one script the legacy
      singular ``script_path`` is also published, preserving no-argument tool
      calls and compatibility with script-aware MCP servers that predate the
      repeatable connector.

    Add to this rather than to the prompt whenever a new connector contributes
    an id or a path pointing at something held elsewhere.

    *slots* narrows the workspace view to the slots this node actually owns,
    which is how a parallel worker under ``supervisor_architecture`` gets its
    own lineage's HEAD as ``data_path`` rather than the whole cell's
    ``data_slots``. Empty (the default) means the whole workspace, which is
    every other caller and the behavior that predates slots.

    ``{}`` when nothing is wired -- Motoro skips an absent/empty dict, so the
    wire call is unchanged for a node with no references.
    """
    meta: dict[str, Any] = {}
    dataset_names = [str(c["dataset_name"]) for c in _resolve_dataset_configs(graph, node_id) if c.get("dataset_name")]
    if dataset_names:
        meta["dataset_names"] = dataset_names
    if workspace_id:
        locators = slot_data_locators(workspace_id)
        if slots:
            locators = {key: value for key, value in locators.items() if key in slots}
        if len(locators) > 1:
            meta["data_slots"] = locators
        elif slots and len(locators) == 1:
            # Narrowed to exactly one, so it has a HEAD to name -- and it must
            # be read from that slot rather than from the workspace, which by
            # now holds every other agent's slot too.
            only = next(iter(locators.values()))
            data_path, target_column = str(only.get("data_path") or ""), str(only.get("target_column") or "")
            if data_path:
                meta["data_path"] = data_path
            if target_column:
                meta["target_column"] = target_column
        else:
            data_path, target_column = head_data_locator(workspace_id)
            if data_path:
                meta["data_path"] = data_path
            if target_column:
                meta["target_column"] = target_column
    script_paths: list[dict[str, str]] = []
    for index, config in enumerate(_resolve_script_configs(graph, node_id), start=1):
        code = config.get("code")
        if not code:
            continue
        script_node_id = str(config.get("node_id") or f"script-{index}")
        script_path = _materialize_script(script_workspace_id or workspace_id, script_node_id, str(code))
        if script_path:
            script_paths.append(
                {
                    "id": script_node_id,
                    "name": str(config.get("name") or f"script-{index}"),
                    "path": script_path,
                }
            )
    if script_paths:
        meta["script_paths"] = script_paths
        if len(script_paths) == 1:
            meta["script_path"] = script_paths[0]["path"]
    return meta


@dataclass(frozen=True)
class NodeDataset:
    """How this node's wired Dataset reached the agent, if one did at all.

    Three outcomes, and the prompt says something different for each (see
    ``_build_user_input``):

    * *seeded* -- the registration has a frozen train/test split, so this
      cell's workspace was opened at HEAD before the turn started and there is
      nothing for the agent to call. One entry per wired dataset, each in its
      own workspace slot, in canvas wiring order.
    * *unsplit* -- the registration is a raw file with no split. There is no
      workspace, and ``data_path``/``target_column`` name that file so the
      agent can split it itself with ``scikit-learn-mcp``. Splitting in ASAREE
      is optional by design, so this is an ordinary state, not a broken one.
    * neither -- no Dataset wired, or every seeding failed; the prompt falls
      back to asking for an ``open_workspace`` call.
    """

    # (dataset name, workspace slot key) per seeded dataset. The slot is
    # carried rather than recomputed because it is not always
    # ``dataset:<name>``: a workspace already in the pre-slot on-disk format
    # keeps its single unnamed slot, and the prompt has to name the slot the
    # agent's tool calls will actually accept.
    seeded: tuple[tuple[str, str], ...] = ()
    unsplit_name: str = ""
    data_path: str = ""
    target_column: str = ""

    @property
    def seeded_names(self) -> tuple[str, ...]:
        return tuple(name for name, _slot in self.seeded)


async def _resolve_node_dataset(
    graph: dict[str, Any],
    node_id: str,
    workspace_id: str | None,
    owner_id: uuid.UUID,
    *,
    slot_prefix: str | None = None,
    stage_plan: Any = None,
) -> NodeDataset:
    """Seed this cell's workspace from the wired dataset before the agent runs.

    Returns which dataset is now open at HEAD, or an empty
    :class:`NodeDataset` if nothing was seeded. That return value is what turns
    ``_build_user_input``'s Dataset block from an instruction ("call
    open_workspace() first") into a statement of fact ("your data is already
    open") -- the point of doing this here is that opening a workspace is not a
    decision an agent should be making. It's a consequence of the user having
    wired a Dataset node, and ASAREE knows that at run start.

    Idempotent and safe to call on every turn: ``Workspace.open`` resumes a
    cell that already has accepted stages rather than resetting it.

    Seeds EVERY wired dataset, each into its own workspace slot. This used to
    seed only when exactly one was wired, because a cell's workspace held one
    implicit lineage and several datasets were a choice with no defensible
    default. Slots remove the choice: two datasets are two lineages in the same
    cell, so both get opened and the agent is told which slot each one is in.
    A single dataset stays slot-less (``slot=None``), which is what keeps its
    workspace in the pre-slot on-disk format -- see ``seed_cell_workspace``.

    *slot_prefix* overrides that namespace entirely, giving this node a private
    lineage nobody else stages into: ``supervisor_architecture`` passes
    ``agent:<node_id>`` so its workers can run in parallel without one accepting
    a stage out from under another. A single dataset lands in the prefix itself;
    several become ``<prefix>:<dataset name>``. Naming rather than deriving it
    here keeps this function free of any opinion about which strategy is
    running.

    *stage_plan* is the experiment's declared pipeline
    (``design_spec.stage_plan``, resolved by ``asaree_workspace_core.stages``).
    It is recorded on the cell's workspace the first time one is created and
    fixed thereafter, so it is passed on every seeding call rather than only the
    first: which node happens to create the workspace depends on which agent has
    a Dataset wired, and that is not something to have to reason about.

    A failure here is logged and swallowed, never raised: a run whose dataset
    registration is broken should still start and let the agent surface the
    real error from its own ``open_workspace`` call, rather than dying before
    its first turn with a message no one is watching for.
    A dataset registered without a split takes the other route entirely and is
    reported as *unsplit* rather than seeded -- see :class:`NodeDataset`.
    """
    names = [str(c["dataset_name"]) for c in _resolve_dataset_configs(graph, node_id) if c.get("dataset_name")]
    if not names:
        return NodeDataset()
    solo = len(names) == 1

    seeded: list[tuple[str, str]] = []
    for name in names:
        reg = await fetch_owned_registration(name, owner_id)
        if reg is None:
            logger.warning(
                "workspace_preseed_failed", extra={"node_id": node_id, "dataset": name, "error": "not found"}
            )
            continue
        # Enrich this run's in-memory graph with current catalog metadata. The
        # published graph still owns identity/order; the registry owns mutable
        # descriptive facts, so old nodes gain the same discovery context as
        # newly-created ones without rewriting a revision.
        for config in _resolve_dataset_configs(graph, node_id):
            if str(config.get("dataset_name") or "") == name:
                config.update(
                    description=reg.get("description"),
                    target_column=reg.get("target_column"),
                    split_state="split" if reg.get("train_path") and reg.get("test_path") else "unsplit",
                    dictionary_available=bool(reg.get("dictionary_json")),
                )
        if not (reg.get("train_path") and reg.get("test_path")):
            # Unsplit: no workspace to seed (``seed_cell_workspace`` says why),
            # so the raw file itself becomes the run's dataset and the agent
            # makes its own split with the sklearn tools. Not a failure and not
            # logged as one -- registration stores only a raw file, and
            # splitting is a separate optional action a researcher may skip.
            #
            # Only bindable when it is the ONLY wired dataset: the ambient
            # data_path is a single value, so an unsplit dataset alongside
            # others has nowhere to be bound and is left for the agent to open
            # (it has no workspace slot either -- a slot is a staged lineage
            # over a frozen split).
            if solo:
                return NodeDataset(
                    unsplit_name=name,
                    data_path=str(reg.get("raw_path") or ""),
                    target_column=str(reg.get("target_column") or ""),
                )
            logger.warning(
                "workspace_preseed_skipped",
                extra={"node_id": node_id, "dataset": name, "error": "unsplit dataset among several"},
            )
            continue
        if not workspace_id:
            continue
        slot: str | None
        if slot_prefix:
            slot = slot_prefix if solo else f"{slot_prefix}:{name}"
        else:
            slot = None if solo else dataset_slot(name)
        try:
            opened = await seed_cell_workspace(
                workspace_id=workspace_id,
                dataset_name=name,
                owner_id=owner_id,
                slot=slot,
                stage_plan=stage_plan,
            )
        except WorkspaceSeedError as e:
            logger.warning(
                "workspace_preseed_failed",
                extra={"workspace_id": workspace_id, "node_id": node_id, "dataset": name, "error": str(e)},
            )
            continue
        seeded.append((opened.dataset_name, opened.slot))
    return NodeDataset(seeded=tuple(seeded))


async def _node_run_context(
    graph: dict[str, Any],
    node_id: str,
    workspace_id: str | None,
    owner_id: uuid.UUID,
    *,
    protocol_run_id: uuid.UUID | None = None,
    slot_prefix: str | None = None,
    stage_plan: Any = None,
) -> tuple[dict[str, Any], NodeDataset]:
    """``(ambient_meta, dataset)`` for one node -- everything the node's
    References contribute, resolved together so the three call sites (gated
    worker, single-node play, main loop) can't drift apart on which half they
    remembered to do.

    Seeding runs FIRST: the ambient ``data_path`` names the workspace's HEAD
    version, which does not exist until the workspace does. For a node with no
    Dataset connector the seeding is a no-op and the path comes from whatever
    an earlier node in the run already seeded.

    An unsplit dataset supplies that path itself, and only as a fallback: a
    workspace HEAD always wins, because a cell that has one has already moved
    past the raw file (and a later Score step must fit on the engineered
    matrix, not on the upload).

    *slot_prefix* gives this node a private workspace lineage (see
    :func:`_resolve_node_dataset`). When it is set the ambient view is narrowed
    to the slots that were just seeded for it, so a worker sharing a cell
    workspace with several sibling workers still sees exactly one HEAD -- its
    own -- rather than everybody's."""
    dataset = await _resolve_node_dataset(
        graph, node_id, workspace_id, owner_id, slot_prefix=slot_prefix, stage_plan=stage_plan
    )
    ambient_meta = _ambient_meta_for(
        graph,
        node_id,
        workspace_id,
        script_workspace_id=_script_workspace_id(workspace_id, protocol_run_id, node_id),
        slots=tuple(slot for _name, slot in dataset.seeded) if slot_prefix else (),
    )
    if dataset.data_path and "data_path" not in ambient_meta:
        ambient_meta["data_path"] = dataset.data_path
        if dataset.target_column:
            ambient_meta["target_column"] = dataset.target_column
    return ambient_meta, dataset


def _resolve_model_config(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    """The node's connected ``llm`` node's own config -- agent/critic_gate
    nodes no longer carry ``model_config_data`` themselves, it's resolved
    from the required Model connector instead (``topological_order`` already
    validated it exists exactly once)."""
    nodes, _downstream, _upstream = _adjacency(graph)
    edges = _edges_with_handle(graph, node_id, "model", direction="incoming")
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

    Several IS the intended shape now, and the connector is uncapped to match
    (see ``AgentNode.tsx``). It was capped at one for a while because a cell's
    workspace held one implicit lineage keyed by ``experiment_id/cell_label``
    alone, so a second dataset had nowhere to go -- ``seed_cell_workspace``
    rejected it outright. Workspace *slots* give each one its own lineage,
    target column and HEAD inside the same cell, which is what a pipeline
    joining two cohorts needs.

    Note the two questions this does not answer: *comparing* datasets is a
    ``dataset_config`` FACTOR, not several connectors -- ``apply_factor_bindings``
    replaces this node's whole ``data.config`` per cell (it runs before anything
    here), so each cell of that design resolves to a one-element list naming its
    own dataset. Several connectors means "this step works with all of these at
    once", which is a different experiment.

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


def _resolve_script_configs(graph: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    """Every connected Script node's config, in canvas wiring order.

    Script shares the Tool connector with mcp_tool rather than getting a
    dedicated handle (see ``_NODE_TYPE_TO_HANDLE``). Like Tool and Dataset,
    it is repeatable: each connected node contributes one independently
    selectable script to the wired agent.
    """
    nodes, _downstream, _upstream = _adjacency(graph)
    configs: list[dict[str, Any]] = []
    for edge in _edges_with_handle(graph, node_id, "tool", direction="incoming"):
        source = nodes.get(edge["source"])
        if source is not None and source.get("type") in _SCRIPT_NODE_TYPES:
            config = (source.get("data") or {}).get("config") or {}
            configs.append({**config, "node_id": str(source.get("id") or "")})
    return configs


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


def _is_peer_edge(edge: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> bool:
    """A main (untyped) edge joining two Agent nodes.

    This is the *same* edge a normal pipeline run walks to pass one agent's
    output to the next -- it is both things, and which one it means is decided
    by the run mode, not by the edge. A conversation reads it as an undirected
    "these two may consult each other"; ``run_protocol`` keeps reading it as a
    directed data-flow. Nothing about the edge is rewritten to say so, which is
    why a canvas built for a pipeline run needs no migration to support
    conversation.

    Connector-typed edges (``_CONNECTOR_HANDLES``) are configuration rather than
    topology, and an Agent<->Critic Gate edge keeps its pipeline meaning, so
    neither makes a peer.
    """
    if edge.get("targetHandle") in _CONNECTOR_HANDLES:
        return False
    source = nodes.get(str(edge.get("source")))
    target = nodes.get(str(edge.get("target")))
    return source is not None and target is not None and source.get("type") == target.get("type") == "agent"


@dataclass(frozen=True)
class SupervisorRoles:
    """Who plays what in a ``supervisor_architecture`` run.

    Derived from the wiring rather than declared per node, so there is no
    second place to keep in sync with the canvas -- the only explicit marker is
    the existing ``conversation_lead``, reused to name the supervisor when the
    wiring alone is ambiguous.
    """

    supervisor: str
    workers: tuple[str, ...]
    reviewer: str | None = None

    @property
    def execution_budget(self) -> int:
        """How many agent turns one run of this topology takes.

        Topology-derived rather than a flat cap, because every turn here is
        forced by ASAREE: the supervisor dispatches, each worker runs once, the
        reviewer runs once, the supervisor synthesizes. There is no model
        deciding to consult, so there is nothing to bound -- this number is a
        description of the run, and the cost estimate the user is owed.
        """
        return 2 + len(self.workers) + (1 if self.reviewer else 0)


def _supervisor_workers_run_in_parallel(design_spec: dict[str, Any] | None) -> bool:
    """Whether this experiment's workers are dispatched at once.

    Parallel by default -- the fan-out is the point of the topology, and each
    worker stages into its own workspace slot, so there is nothing for them to
    race over. ``coordination_strategy.params.parallel_workers = false`` opts
    one experiment out, for the case where workers are meant to build on each
    other's staged data, or where a run needs to be reproduced turn by turn.

    Anything other than an explicit ``false`` means parallel, so a params dict
    carrying an unrelated key, or a value some other tool wrote, can never
    quietly halve a run's throughput.
    """
    params = ((design_spec or {}).get("coordination_strategy") or {}).get("params") or {}
    return params.get("parallel_workers") is not False


def _supervisor_candidates(
    agents: list[str],
    successors: dict[str, list[str]],
    predecessors: dict[str, list[str]],
) -> list[str]:
    """Which agents the *wiring* alone says could be the supervisor.

    Two rules, tried in order, because the target topology defeats the obvious
    one. "The agent nothing feeds into" reads a supervisor off a fan-out
    cleanly -- but the moment a reviewer reports its findings back, the
    supervisor is fed too and no agent is unfed at all, so that rule finds
    nobody on the very shape this strategy was built for.

    So when there is no unfed agent, fall back to the widest fan-out: in a loop
    the supervisor is still the agent dispatching to the most others (three
    workers against the reviewer's one report). A tie is left ambiguous rather
    than broken arbitrarily -- a three-agent ring genuinely is symmetric, and
    the marker is how the user says which way round it goes.

    Order matters: the unfed rule wins whenever it applies, so a canvas whose
    head fans out more narrowly than some agent downstream of it still resolves
    to the head the user drew.
    """
    unfed = [nid for nid in agents if successors[nid] and not predecessors[nid]]
    if unfed:
        return unfed
    widest = max((len(successors[nid]) for nid in agents), default=0)
    if not widest:
        return []
    return [nid for nid in agents if len(successors[nid]) == widest]


def resolve_supervisor_roles(graph: dict[str, Any]) -> SupervisorRoles:
    """Read a supervisor topology off the canvas, or say why it isn't one.

    The target shape is one supervisor fanning out to N workers, with an
    optional reviewer that sees the workers' output and reports back. Roles come
    from the agent handoff graph (:func:`_sequential_agent_links`), so a Critic
    Gate or a Script between two agents is plumbing, not a role:

    * **supervisor** -- the agent marked ``conversation_lead`` if one is (the
      same marker Peer Collaboration uses; a second marker field would be two
      ways to say one thing), else whichever agent the wiring points to (see
      :func:`_supervisor_candidates`).
    * **workers** -- every agent the supervisor hands off to.
    * **reviewer** -- the one remaining agent, which must be connected to at
      least two others. It is deliberately the agent the supervisor does *not*
      point at: direction is how the canvas distinguishes "I am dispatching work
      to you" from "you report on the work". Draw the reviewer's edge toward the
      supervisor and the workers' edges toward the reviewer.

    Raises :class:`ProtocolValidationError` naming the offending agents for
    anything else -- notably two agents the supervisor dispatches to that are
    also wired to each other, which is a peer mesh rather than a supervisor
    tree and belongs under Peer Collaboration.
    """
    nodes = {str(n.get("id")): n for n in graph.get("nodes") or [] if n.get("id")}
    agents, successors, predecessors = _sequential_agent_links(graph)

    def _name(node_id: str) -> str:
        return _node_display_name(nodes[node_id])

    def _names(ids: Iterable[str]) -> str:
        return ", ".join(sorted(_name(nid) for nid in ids))

    if len(agents) < 2:
        raise ProtocolValidationError(
            "This experiment's coordination strategy is 'Supervisor' but this protocol has fewer than two Agent "
            "nodes, so there is nobody to supervise -- add worker agents, or change the coordination strategy on "
            "the Design tab."
        )

    marked = [nid for nid in agents if _is_marked_lead(nodes[nid])]
    if len(marked) > 1:
        raise ProtocolValidationError(
            f"More than one agent is marked as the supervisor ({_names(marked)}). A supervisor run has exactly "
            "one -- unmark the others."
        )
    if marked:
        supervisor = marked[0]
    else:
        candidates = _supervisor_candidates(agents, successors, predecessors)
        if len(candidates) != 1:
            detail = (
                f"more than one agent could be ({_names(candidates)})"
                if candidates
                else "no agent hands off to another, so none of them leads"
            )
            raise ProtocolValidationError(
                f"This protocol doesn't say which agent supervises the others -- {detail}. Mark the supervisor in "
                "its node settings, or wire it so exactly one agent hands off to the others without being fed by "
                "one."
            )
        supervisor = candidates[0]

    workers = tuple(successors[supervisor])
    if not workers:
        raise ProtocolValidationError(
            f"{_name(supervisor)!r} supervises this run but hands off to no other agent, so there are no workers "
            "to dispatch to. Connect it to the agents it should delegate to."
        )

    rest = [nid for nid in agents if nid != supervisor and nid not in workers]
    if len(rest) > 1:
        raise ProtocolValidationError(
            f"{_names(rest)} are neither the supervisor nor agents it hands off to. A supervisor run has one "
            "supervisor, its workers, and at most one reviewer -- connect them to the supervisor to make them "
            "workers, or delete them."
        )
    reviewer = rest[0] if rest else None
    if reviewer is not None and len(_connected_agent_ids(graph, reviewer)) < 2:
        raise ProtocolValidationError(
            f"{_name(reviewer)!r} reviews this run but is connected to only one other agent, so it has almost "
            "nothing to review. Connect it to the workers whose output it should see, or connect it to the "
            "supervisor to make it a worker instead."
        )

    # Worker-to-worker edges make this a mesh, not a tree. Rejected rather than
    # tolerated because the whole guarantee of this strategy is that ASAREE
    # dispatches every worker itself: an edge between two workers says they talk
    # to each other, which nothing here would ever honor.
    worker_set = set(workers)
    for nid in workers:
        peers = sorted(worker_set.intersection(_connected_agent_ids(graph, nid)))
        if peers:
            raise ProtocolValidationError(
                f"{_name(nid)!r} is wired to another worker ({_names(peers)}), but under 'Supervisor' the workers "
                "report to the supervisor, not to each other. Remove that connection, or switch the strategy to "
                "'Peer Collaboration' on the Design tab."
            )
    return SupervisorRoles(supervisor=supervisor, workers=workers, reviewer=reviewer)


def _connected_agent_ids(graph: dict[str, Any], node_id: str) -> list[str]:
    """Agent node ids reachable from *node_id* over a peer edge, either way.

    Undirected on purpose: an edge's stored source/target records how the user
    happened to draw it, not who is allowed to speak. Order is canvas wiring
    order, and each peer appears once however many edges join the pair.
    """
    nodes = {str(n.get("id")): n for n in graph.get("nodes") or [] if n.get("id")}
    peers: list[str] = []
    for edge in graph.get("edges") or []:
        if not _is_peer_edge(edge, nodes):
            continue
        source, target = str(edge.get("source")), str(edge.get("target"))
        if source == node_id:
            other = target
        elif target == node_id:
            other = source
        else:
            continue
        if other != node_id and other not in peers:
            peers.append(other)
    return peers


def _can_deliver_communication(graph: dict[str, Any], from_agent_id: str, to_agent_id: str) -> bool:
    """Live authorization check, re-run for every consultation.

    Capability is snapshotted, reachability is live: the cards an agent carries
    come from the run's published revision, but whether it may still *reach* a
    peer is answered against the draft ``Protocol.graph`` at call time. Pulling
    the edge on the canvas stops the next consultation mid-run, which is the
    behaviour a user unplugging a wire expects.
    """
    if from_agent_id == to_agent_id:
        return False
    return to_agent_id in _connected_agent_ids(graph, from_agent_id)


async def resolve_agent_card(
    graph: dict[str, Any],
    node_id: str,
    *,
    owner_id: uuid.UUID,
    metadata: dict[str, Any] | None = None,
) -> AgentCard | None:
    """Project one Agent node into the card its peers see.

    ``None`` for a node that is missing or is not an Agent -- invariant 4: a
    non-agent node is never addressable.

    Reads the same graph the run executes from, so a card can never describe an
    agent differently from how that agent is about to be configured.
    """
    nodes = {str(n.get("id")): n for n in graph.get("nodes") or [] if n.get("id")}
    node = nodes.get(node_id)
    if node is None or node.get("type") != "agent":
        return None
    config = (node.get("data") or {}).get("config") or {}
    # The registered skill documents, not the ids: a peer reads names and
    # descriptions to decide whether this agent is worth asking. Bodies are
    # never projected -- progressive disclosure is the owning agent's business.
    skills = await resolve_skills(_resolve_skill_config(graph, node_id), owner_id=owner_id)
    return build_agent_card(
        node_id=node_id,
        label=(node.get("data") or {}).get("label"),
        description=config.get("description") or "",
        goal=config.get("goal") or "",
        skills=[dict(s) for s in skills],
        model=_resolve_model_config(graph, node_id).get("model"),
        metadata=metadata,
    )


async def resolve_available_agents(
    graph: dict[str, Any],
    node_id: str,
    *,
    owner_id: uuid.UUID,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Serialized cards for every peer connected to *node_id*, in wiring order.

    ``[]`` when nothing is connected -- which is every existing single-agent and
    pipeline run, so they carry no new field content, get no prompt section and
    are bound no new function schemas (invariant 11).
    """
    cards: list[dict[str, Any]] = []
    for peer_id in _connected_agent_ids(graph, node_id):
        card = await resolve_agent_card(graph, peer_id, owner_id=owner_id, metadata=metadata)
        if card is not None:
            cards.append(card.to_dict())
    return cards


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
    they're read by ``_resolve_script_configs``/``_resolve_dataset_configs``
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
    tool_descriptions: dict[str, str] = {}
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
        label = str(
            bundle_config.get("document_title")
            or bundle_config.get("bundle_label")
            or (source.get("data") or {}).get("label")
            or server_name
        )
        summary = str(bundle_config.get("document_description") or bundle_config.get("bundle_description") or "")
        for name in bundle_config.get("tool_names") or []:
            full_name = f"{server_name}.{name}"
            tool_names.append(full_name)
            tool_descriptions[full_name] = f"Knowledge source: {label}. {summary}".strip()
    resolved: dict[str, Any] = {"server_names": server_names, "tool_names": tool_names}
    if tool_descriptions:
        resolved["tool_descriptions"] = tool_descriptions
    return resolved


def _declares_a_field(contract: Any) -> bool:
    """Whether *contract* names at least one field. The name is the whole test
    -- a row with a type but no name is a half-filled editor row, and every
    consumer here keys off the name."""
    if not isinstance(contract, dict):
        return False
    return any(
        isinstance(field, dict) and str(field.get("name") or "").strip() for field in contract.get("fields") or []
    )


def _resolve_output_contract(graph: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    """The Motoro ``output_contract`` field spec for this agent -- from its
    wired Output Parser node, or, failing that, from the agent's own legacy
    ``config.output_contract``. ``None`` when it has neither, which is the
    common case and means no payload extraction happens at all.

    **The legacy fallback is permanent, not a deprecation ramp.** Two reasons,
    both structural rather than a matter of how long anyone waits:

    * Every ``ProtocolRevision`` is an immutable snapshot that ``run_protocol``
      loads, and revisions carrying the field already exist and are already
      pointed at by finished runs. A data migration that rewrote them would be
      editing published artifacts; one that didn't would break them.
    * ``POST /agents`` still accepts ``output_contract`` (see
      :mod:`asaree.api.agents`), so a *new* graph can arrive with the field set,
      from the SDK or a notebook, at any time. There is no cutover date after
      which nothing produces one.

    So this is unlike ``_LEGACY_MODEL_HANDLES``, which covers a rename whose
    stored data really was migrated: nothing here ever becomes dead code.

    The two sources are never merged and never race -- ``topological_order``
    refuses a graph that has both on one node, so by the time this runs at most
    one is set. The parser is checked first anyway, so that ordering is not
    load-bearing.

    A disabled parser node (``config.enabled is False``) contributes nothing,
    the same way a disabled Tool or Knowledge node does: that is the canvas's
    way of taking the shape out of one run -- prose instead of named values --
    without deleting the field spec.

    Note the fallback is reached only when **no parser node is wired at all**,
    not whenever the parser yields nothing. A wired-but-disabled or
    wired-but-empty parser resolves to ``None``, because "off for this run" has
    to mean off -- reaching past it to a stored field would run a contract the
    user had just switched away from.

    "Empty" means *no field anyone could name*, not just a missing ``fields``
    list: a new parser node arrives with one blank row (see
    ``defaultOutputParserNodeData``), so a contract can be present and still
    declare nothing. Such a contract is treated as absent rather than passed on,
    because every consumer would otherwise do work for a shape with no keys in
    it -- most expensively the runtime, which would spend a model call
    extracting a payload that cannot have any fields."""
    nodes, _downstream, _upstream = _adjacency(graph)
    wired = False
    for edge in _edges_with_handle(graph, node_id, "output_parser", direction="incoming"):
        source = nodes.get(edge["source"])
        if source is None or source.get("type") not in _OUTPUT_PARSER_NODE_TYPES:
            continue
        wired = True
        parser_config = (source.get("data") or {}).get("config") or {}
        if not parser_config.get("enabled", True):
            continue
        contract = parser_config.get("output_contract")
        if isinstance(contract, dict) and _declares_a_field(contract):
            return dict(contract)
    if wired:
        return None
    node = nodes.get(node_id) or {}
    legacy = ((node.get("data") or {}).get("config") or {}).get("output_contract")
    return dict(legacy) if isinstance(legacy, dict) and _declares_a_field(legacy) else None


def _output_shape_block(contract: dict[str, Any] | None) -> str:
    """The producer-side prompt block naming the fields its Output Parser
    declares, and asking for them back as JSON. ``""`` when there is no
    contract.

    This is the half of ``output_contract`` that never existed. Motoro's
    ``extract_payload`` is a *post-hoc* extractor: a second LLM call that
    coerces text the agent has already finished, and the agent was never told
    the contract exists -- so the extractor was being asked to pull ``n_rows``
    out of prose that had no reason to contain ``n_rows``.

    Naming the fields fixed the accuracy half of that. The JSON block fixes the
    cost half: with the values restated in a machine-readable form, reading
    them is a ``json.loads`` in ``parse_payload_inline`` rather than a second
    pass over the whole answer. The model call is still there as a fallback for
    a reply that ignores the instruction, so a parser never *stops* working --
    it just stops being the normal case, which is what makes declaring a shape
    cheap enough to be the default way to ask for one.

    Two things this deliberately does not do. It does not send a JSON Schema:
    the fields already read as a list, and a schema is longer, harder for a
    model to follow, and no more precise for a flat object. And it does not ask
    for JSON *instead* of the answer -- the prose is what the next agent reads,
    so the block is an appendix to it. ``parse_payload_inline`` strips the block
    back off before anything downstream sees it."""
    fields = (contract or {}).get("fields") or []
    lines = []
    keys = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        keys.append(name)
        type_ = str(field.get("type") or "").strip()
        description = str(field.get("description") or "").strip()
        suffix = f" -- {description}" if description else ""
        lines.append(f"- {name} ({type_}){suffix}" if type_ else f"- {name}{suffix}")
    if not lines:
        return ""
    # `null` for every key, so the template is itself valid JSON and "I could
    # not establish this" needs no separate notation -- it is what the model
    # gets by leaving the line alone.
    template = "{" + ", ".join(f'"{key}": null' for key in keys) + "}"
    return (
        "Your answer will be read for these specific values, so state each one explicitly:\n"
        + "\n".join(lines)
        + "\n\nWrite your answer as you normally would. Then, as the very last thing in your reply, "
        "repeat those values in a fenced JSON block:\n"
        f"```json\n{template}\n```\n"
        "Replace each null with the value your answer establishes, leaving it null where your answer "
        "establishes none. Write nothing after the block."
    )


def _resolve_dataset_tool_config(graph: dict[str, Any], node_id: str, *, unsplit_dataset: str = "") -> dict[str, Any]:
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

    *unsplit_dataset* names a wired registration that has no train/test split
    (``_resolve_node_dataset``'s other outcome). There is no workspace for one,
    so the workspace grant above is inapplicable -- and ``_build_user_input``'s
    Dataset block says exactly that, then points the agent at
    ``describe_dataset``/``describe_split``/``train_test_split``. Those come
    from ``scikit-learn-mcp``, so they are granted with it: naming a tool in the
    prompt and leaving it out of the allow-list is the precise defect this
    function and ``_resolve_script_tool_config`` were written to fix, and the
    unsplit case was the one dataset shape neither covered. The workspace tools
    stay granted alongside them -- ``workspace_status`` reporting "no workspace
    here" is a better answer than a missing tool.
    """
    if not _resolve_dataset_configs(graph, node_id):
        return {"server_names": [], "tool_names": []}
    server_names = [WORKSPACE_SERVER_NAME]
    tool_names = [f"{WORKSPACE_SERVER_NAME}.{name}" for name in WORKSPACE_AGENT_TOOLS]
    if unsplit_dataset:
        server_names.append(SCIKIT_LEARN_SERVER_NAME)
        tool_names.extend(f"{SCIKIT_LEARN_SERVER_NAME}.{name}" for name in UNSPLIT_DATASET_AGENT_TOOLS)
    if any(config.get("dictionary_available") for config in _resolve_dataset_configs(graph, node_id)):
        server_names.append(EDA_SERVER_NAME)
        tool_names.extend(f"{EDA_SERVER_NAME}.{name}" for name in DATASET_DICTIONARY_AGENT_TOOLS)
    return {"server_names": server_names, "tool_names": tool_names}


def _resolve_script_tool_config(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    """The Script connector's contribution to the tool allow-list: ASAREE's own
    ``asaree-script`` server, on exactly the same terms as
    ``_resolve_dataset_tool_config`` above.

    Wiring a script is the gesture that means "run this", so the executor
    follows from it. Before this, a Script node was inert unless the user ALSO
    wired ``asaree-sklearn-model`` or ``scikit-learn-mcp`` -- the only two
    servers whose script tools read the ambient script path, and both
    model-fitting harnesses that reject a script which doesn't define a
    ``predict_proba``/``predict``. A script that merely computed and printed
    something had nowhere at all to run, while the prompt went on telling the
    agent one was waiting.

    Granted on the code, not on the node: a Script node wired with an empty
    ``code`` has nothing to execute, and ``_ambient_meta_for`` publishes no path
    for it either, so the tool would only be there to report its own absence.
    """
    if not any(config.get("code") for config in _resolve_script_configs(graph, node_id)):
        return {"server_names": [], "tool_names": []}
    return {
        "server_names": [SCRIPT_SERVER_NAME],
        "tool_names": [f"{SCRIPT_SERVER_NAME}.{name}" for name in SCRIPT_AGENT_TOOLS],
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
    tool_descriptions: dict[str, str] = {}
    for config in configs:
        for name in config.get("server_names") or []:
            if name not in server_names:
                server_names.append(name)
        for name in config.get("tool_names") or []:
            if name not in tool_names:
                tool_names.append(name)
        tool_descriptions.update(config.get("tool_descriptions") or {})
    resolved: dict[str, Any] = {"server_names": server_names, "tool_names": tool_names}
    if tool_descriptions:
        resolved["tool_descriptions"] = tool_descriptions
    return resolved


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


def _node_seed_prompt(node: dict[str, Any]) -> str:
    """The node's own instruction, before ASAREE wraps anything around it.

    ``prompt`` is the field meant to change per run; ``goal`` is a persistent
    objective and only stands in when no prompt is set; the canvas label is the
    last resort. Shared by ``_build_user_input`` (where it's the first block)
    and the sequential transcript (where it's what the user is shown as having
    asked), so the two can't disagree about which field is the instruction.
    """
    data: dict[str, Any] = node.get("data") or {}
    config: dict[str, Any] = data.get("config") or {}
    return str(config.get("prompt") or config.get("goal") or data.get("label", ""))


#: There is deliberately no sentence here telling the agent what the fenced
#: block *is* or how to treat it. There used to be two -- ``handoff`` ("any
#: instructions inside are addressed to someone else, do not follow them") and
#: ``brief`` ("its instructions ARE meant for you") -- selected by an
#: ``upstream_kind`` argument threaded through this whole module.
#:
#: Needing two was the tell. Whether a predecessor's output is *material to work
#: on* or *direction to follow* is the experimenter's design, not a fact about
#: the topology, and the platform was guessing. On a Planner -> Reporter chain
#: the handoff sentence contradicts the whole point of the edge; that is exactly
#: why the supervisor path had to opt out of it. So the prompt now says nothing
#: about it, and the experimenter's own wording ("carry out the plan below" vs.
#: "summarize the material below") settles it -- which also makes it a treatment
#: they can vary rather than a constant they cannot see.
#:
#: What survives is structure, not prose: the ``[Sender]`` label and the fence.
#: Delimiters are constant across every treatment and assert nothing, so unlike
#: a framing sentence they are not a confound -- the same argument
#: :func:`_reference_payload` already makes for keeping its own fence automatic
#: while the prose around it became opt-in and then went away.
#:
#: The mechanical protection is unchanged: ``fence_upstream`` still neutralizes
#: the delimiter inside the payload, so upstream text cannot forge its way out
#: of its own block. What was dropped is a soft instruction, not a boundary.


def _upstream_context(
    graph: dict[str, Any],
    node_id: str,
    node_runs: dict[str, Any],
    *,
    upstream_ids: list[str] | None = None,
    exclude_ids: Collection[str] = (),
) -> str:
    """A node's predecessors' output, as it appears in its prompt.

    **Automatic.** A direct predecessor's output arrives in this agent's prompt
    because the edge is there, not because a ``{{...}}`` asked for it. That
    reverses a previous design in which an edge granted *availability* and a
    reference granted *use*: the reference was meant to keep platform text out
    of a treatment, but the thing it was gating is the pipeline's own payload,
    not prose -- and gating it made "graph looks wired, nothing flows" a silent,
    legal outcome. In a factorial batch that is a degenerate cell that still
    looks clean in the results table, which is worse than any confound it
    avoided. ``upstream_ids is None`` now means "derive it from the graph"
    rather than "emit nothing".

    Direct predecessors only (:func:`_upstream_ids` follows main edges). An
    ancestor further back never arrives on its own; reaching one is what an
    explicit ``{{node:X}}`` is for, and staying explicit is the point of it.

    **Structure, no prose.** A ``[Sender]`` label and a fence, and nothing else
    -- no heading, and no sentence telling the agent what the block is or how to
    treat it (see the note above this function for why that sentence was removed
    rather than reworded). Names come from the canvas
    label, since a model reads ``[Feature Engineer]`` as an author and
    ``[dndnode_3]`` as noise, and the label is what the user sees on the canvas
    and in the transcript, so one upstream step is called one thing everywhere.
    Unlabelled nodes fall back to ``_node_display_name``'s type placeholder, and
    the node id is appended only when two senders resolve to the same name --
    "which of the two" is the one question the id actually answers.

    The fence stays because Motoro wraps the whole assembled prompt in a single
    ``<<<USER_DATA>>>`` (``engine/reason.py``, ``plan.py``, ``act.py``) at a
    granularity that cannot separate this agent's instructions from its
    predecessor's output. The inner fence draws that line, and ``fence_upstream``
    neutralizes the delimiter inside the payload so an agent cannot write outside
    its own block.

    *exclude_ids* are senders the prompt already referenced by hand. Their block
    is dropped rather than repeated: the experimenter placed that output
    somewhere deliberate, and appending a second copy would be the platform
    overruling the placement. Per-sender, not all-or-nothing -- referencing one
    predecessor of a fan-in must not silently drop the other. Because the block
    built here is byte-identical to what :func:`_render_reference` builds, the
    two can be swapped without the prompt changing shape.

    This block is **not** frozen: the golden in ``tests/test_spinal_compat.py``
    is a change-detector that puts the diff in front of a reviewer, not a
    promise the text will not move.
    """
    ids = _upstream_ids(graph, node_id) if upstream_ids is None else upstream_ids
    excluded = set(exclude_ids)
    ids = [uid for uid in ids if uid not in excluded]
    nodes = {str(n.get("id")): n for n in graph.get("nodes") or []}
    names = {uid: _node_display_name(nodes.get(uid) or {"id": uid}) for uid in ids}
    ambiguous = {name for name in names.values() if list(names.values()).count(name) > 1}
    blocks = []
    for uid in ids:
        run = node_runs.get(uid) or {}
        text = run.get("output_text")
        if not text:
            continue
        label = f"{names[uid]} ({uid})" if names[uid] in ambiguous else names[uid]
        blocks.append(f"[{label}]\n{_sender_block(str(text), run.get('payload') or {})}")
    return "\n\n".join(blocks)


def _reference_payload(text: str, *, raw: bool) -> str:
    """One referenced output, ready to sit in a prompt.

    Fenced by default. Delimiters are constant across every treatment and assert
    nothing, so unlike a framing sentence they are not a confound -- which is
    why the fence stayed automatic when the prose became opt-in.

    ``|raw`` drops the fence for an experimenter who wants their sentence to
    read as one sentence, but **not** the neutralization: a payload that can
    forge a delimiter can make the text after it look like quoted material, or
    close Motoro's outer ``<<<USER_DATA>>>`` fence. That part is never a choice.
    """
    return neutralize_delimiters(text) if raw else fence_upstream(text)


def _sender_block(text: str, payload: dict[str, Any], *, raw: bool = False) -> str:
    """One sender's whole contribution: its fenced answer, plus the fields its
    Output Parser extracted.

    Shared by the automatic upstream block (:func:`_upstream_context`) and an
    explicit ``{{node:X}}`` (:func:`_render_reference`) so the two cannot drift.
    That they are byte-identical is what makes suppression safe: an
    experimenter who references a predecessor by hand gets exactly what would
    have arrived anyway, only where they put it.
    """
    body = _reference_payload(text, raw=raw)
    fields = _payload_fields_line(payload)
    return f"{body}\n{fields}" if fields else body


def _format_payload_value(value: Any) -> str:
    """One extracted field as it substitutes into a sentence.

    Bare: ``{{node:x.n_rows}}`` becomes ``4300``, not ``"4300"`` and not a JSON
    fragment. The whole point of a field reference is that the sentence around
    it reads as a sentence, so the experimenter writes the quotes if they want
    them. Lists and dicts have no bare spelling, so those fall back to JSON --
    a field reference to one is unusual but not illegal.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return json.dumps(value)
    return json.dumps(value, default=str)


def _payload_fields_line(payload: dict[str, Any]) -> str:
    """The extracted fields appended to a whole-node reference.

    Readable ``key=value``, not raw JSON: this sits in a prompt an agent reads,
    and a JSON blob invites it to reply in JSON. Strings are quoted here (unlike
    the bare single-field form) because without quotes a multi-word value runs
    into the next pair. No fence and no sender name -- the fenced output_text it
    follows already carries both, and this is a continuation of that block, not
    a second one.
    """
    if not payload:
        return ""
    pairs = [
        f"{key}={json.dumps(value, default=str) if isinstance(value, str) else _format_payload_value(value)}"
        for key, value in payload.items()
    ]
    return "Structured fields: " + ", ".join(pairs)


def _render_reference(
    ref: prompt_references.PromptReference,
    *,
    graph: dict[str, Any],
    node_id: str,
    node_runs: dict[str, Any],
    unresolved: list[str],
) -> str:
    """What one ``{{...}}`` becomes. Appends to *unresolved* as a side effect.

    An empty resolution is recorded rather than raised: an agent that correctly
    produced nothing is a legitimate result, and failing the replicate would
    discard valid experimental data. Recording it is what keeps the other
    outcome -- a cell that ran with an empty variable and looks clean in the
    results table -- from being silent.

    Every surviving form resolves to *data*. The two that resolved to
    platform-composed prose -- ``{{audience}}`` and ``{{upstream_instructions}}``
    -- are gone; see :mod:`asaree.services.prompt_references`.
    """
    if ref.field:
        # A field reference resolves against the payload alone: it names a
        # typed value, and falling back to the prose when extraction failed
        # would substitute a whole answer where a number was expected. Best
        # effort by design -- the parser is post-hoc and may legitimately have
        # produced nothing -- so this is an empty resolution, recorded like any
        # other, not a failed run.
        payload = (node_runs.get(ref.node_id) or {}).get("payload") or {}
        rendered = _format_payload_value(payload.get(ref.field)) if ref.field in payload else ""
        if not rendered:
            unresolved.append(f"{ref.node_id}.{ref.field}")
            return ""
        # Neutralized but never fenced: a fence around a bare value would put
        # a delimiter block in the middle of a sentence.
        return neutralize_delimiters(rendered)

    ids = _upstream_ids(graph, node_id) if ref.kind == prompt_references.PREVIOUS else [ref.node_id]
    nodes = {str(n.get("id")): n for n in graph.get("nodes") or []}
    blocks = []
    for uid in ids:
        run = node_runs.get(uid) or {}
        text = run.get("output_text")
        if not text:
            unresolved.append(uid)
            continue
        block = _sender_block(str(text), run.get("payload") or {}, raw=ref.raw)
        # Labelled only where the reference itself cannot say which sender is
        # which: a `{{previous}}` that expanded to several predecessors. A
        # single-node reference needs no label, because the experimenter named
        # the node -- adding one there would be platform text in a prompt that
        # asked for a payload.
        if len(ids) > 1:
            block = f"[{_node_display_name(nodes.get(uid) or {'id': uid})}]\n{block}"
        blocks.append(block)
    return "\n\n".join(blocks)


def _resolve_prompt_references(
    text: str,
    graph: dict[str, Any],
    node_id: str,
    node_runs: dict[str, Any],
) -> tuple[str, list[str]]:
    """Substitute every reference in *text*; returns it plus the ids that
    resolved to nothing.

    The caller decides what to do with the second value -- see
    :func:`_render_reference` on why it is reported rather than raised.
    """
    unresolved: list[str] = []
    rendered = prompt_references.substitute(
        text,
        lambda ref: _render_reference(
            ref,
            graph=graph,
            node_id=node_id,
            node_runs=node_runs,
            unresolved=unresolved,
        ),
    )
    return rendered, unresolved


def _hand_placed_sender_ids(text: str, graph: dict[str, Any], node_id: str) -> set[str]:
    """Which of *node_id*'s direct predecessors this prompt has already placed
    itself, and whose automatic block :func:`_upstream_context` should therefore
    drop.

    Only a **whole-node** reference counts. ``{{Profiler.n_rows}}`` names one
    extracted value, not the answer it came from -- suppressing the block for
    that would silently take away the prose the agent was wired to receive,
    which is the opposite of what asking for one number requested.

    ``{{previous}}`` counts for every direct predecessor at once, since that is
    exactly what it expands to.
    """
    placed = {ref.node_id for ref in prompt_references.iter_references(text) if ref.kind == "node" and not ref.field}
    if prompt_references.uses(text, prompt_references.PREVIOUS):
        placed |= set(_upstream_ids(graph, node_id))
    return placed


def validate_prompt_references(*, graph: dict[str, Any]) -> None:
    """Refuse a graph whose prompts point at something that can never resolve.

    Design time, not run time: a reference that cannot resolve is a wiring
    mistake, and the user can only act on it while looking at the canvas. Raised
    from the same place :func:`validate_coordination_strategy` and
    :func:`validate_stage_plan` are called, so it lands at publish/plan/run
    rather than halfway through a batch.

    Checked against :func:`referenceable_node_ids`, the same set the picker
    offers, so the two cannot disagree about what is legal.

    A node with predecessors and *no* reference is not an error, and is no
    longer even unusual: its predecessors' output arrives on the edge, so the
    prompt has nothing left to say. Writing a reference is how an author asks
    for something the edge does not already give them -- a different position, a
    node further back, one extracted field.
    """
    for node in graph.get("nodes") or []:
        node_id = node.get("id")
        if not node_id:
            continue
        # Both reference-bearing fields, checked identically: the picker is
        # offered in the System prompt box too, and it tells the user there
        # that publishing will be refused for an out-of-scope reference. The
        # field name goes into the message so a rejected publish says which
        # box to open.
        fields = [("prompt", _node_seed_prompt(node))]
        system_prompt = (node.get("data", {}).get("config", {}) or {}).get("system_prompt")
        if system_prompt:
            fields.append(("system prompt", str(system_prompt)))
        for field, prompt in fields:
            if not prompt_references.has_references(prompt):
                continue
            name = _node_display_name(node)
            allowed = set(referenceable_node_ids(graph, node_id))
            known = {str(n.get("id")) for n in graph.get("nodes") or []}

            for referenced in prompt_references.referenced_node_ids(prompt):
                if referenced not in known:
                    raise ProtocolValidationError(
                        f"{name}'s {field} references a node that no longer exists ({referenced}). "
                        "Remove the reference or reconnect the node that replaced it."
                    )
                if referenced not in allowed:
                    other = _node_display_name(next(n for n in graph["nodes"] if str(n.get("id")) == referenced))
                    raise ProtocolValidationError(
                        f"{name}'s {field} references {other!r}, which does not run before it. "
                        "A reference only resolves if the referenced node is upstream on the "
                        "same path -- connect them, or reference a node that is."
                    )

            # Field references are checked against the producer's DECLARED
            # contract, not against anything a run produced: this is design
            # time, nothing has run, and a misspelled field would otherwise
            # surface as an empty substitution in the middle of a batch. The
            # whole-node form has no equivalent check because prose always
            # exists; a named field either was declared or was a typo.
            for referenced, named_fields in prompt_references.referenced_node_fields(prompt).items():
                if referenced not in known or referenced not in allowed:
                    continue  # already reported above, with the better message
                other = _node_display_name(next(n for n in graph["nodes"] if str(n.get("id")) == referenced))
                contract = _resolve_output_contract(graph, referenced)
                declared = [
                    str(spec.get("name") or "").strip()
                    for spec in (contract or {}).get("fields") or []
                    if isinstance(spec, dict) and str(spec.get("name") or "").strip()
                ]
                for field_name in named_fields:
                    if field_name in declared:
                        continue
                    if not declared:
                        raise ProtocolValidationError(
                            f"{name}'s {field} references {other!r}.{field_name}, but {other!r} has no "
                            "Output Parser declaring any fields. Connect an Output Parser to it, or "
                            "reference the whole node instead."
                        )
                    raise ProtocolValidationError(
                        f"{name}'s {field} references {other!r}.{field_name}, which {other!r}'s Output "
                        f"Parser does not declare. It declares: {', '.join(declared)}."
                    )

            if prompt_references.uses(prompt, prompt_references.PREVIOUS) and not _upstream_ids(graph, node_id):
                raise ProtocolValidationError(
                    f"{name}'s {field} uses {{{{previous}}}} but nothing is connected to its input. "
                    "Connect an upstream node, or reference one explicitly."
                )


def _resource_catalog(graph: dict[str, Any], node_id: str) -> str:
    """Compact semantic metadata for references wired into one agent.

    Paths, ids, and source bodies stay out of the prompt. This block gives the
    model only enough meaning to choose among already-authorized resources;
    native tool schemas remain the interface for reading or executing them.
    """
    sections: list[str] = []

    datasets = _resolve_dataset_configs(graph, node_id)
    if datasets:
        lines = []
        for config in datasets:
            name = str(config.get("dataset_name") or "dataset")
            facts = [str(config.get("description") or "").strip()]
            if config.get("target_column"):
                facts.append(f"target={config['target_column']}")
            if config.get("split_state"):
                facts.append(f"state={config['split_state']}")
            if config.get("dictionary_available"):
                facts.append("data dictionary available through an authorized EDA tool")
            detail = "; ".join(fact for fact in facts if fact)
            lines.append(f"- {name}: {detail}" if detail else f"- {name}")
        sections.append("Available datasets:\n" + "\n".join(lines))

    nodes, _downstream, _upstream = _adjacency(graph)
    knowledge_lines: list[str] = []
    for edge in _edges_with_handle(graph, node_id, "knowledge", direction="incoming"):
        source = nodes.get(edge["source"])
        if source is None or source.get("type") not in _KNOWLEDGE_NODE_TYPES:
            continue
        config = (source.get("data") or {}).get("config") or {}
        if not config.get("enabled", True):
            continue
        label = str(
            config.get("document_title")
            or config.get("bundle_label")
            or (source.get("data") or {}).get("label")
            or "knowledge source"
        )
        facts = [str(config.get("document_description") or config.get("bundle_description") or "").strip()]
        if config.get("document_type"):
            facts.append(f"type={config['document_type']}")
        tags = config.get("document_tags") or []
        if tags:
            facts.append("tags=" + ", ".join(str(tag) for tag in tags))
        detail = "; ".join(fact for fact in facts if fact)
        knowledge_lines.append(f"- {label}: {detail}" if detail else f"- {label}")
    if knowledge_lines:
        sections.append(
            "Available knowledge sources (use their list/search/get tools to disclose content on demand):\n"
            + "\n".join(knowledge_lines)
        )

    scripts = [config for config in _resolve_script_configs(graph, node_id) if config.get("code")]
    if scripts:
        lines = []
        for index, config in enumerate(scripts, start=1):
            name = str(config.get("name") or f"script-{index}")
            description = str(config.get("description") or "").strip()
            lines.append(f"- {name}: {description}" if description else f"- {name}")
        sections.append("Available scripts (source remains out of context until execution):\n" + "\n".join(lines))

    return "\n\n".join(sections)


def _build_user_input(
    node: dict[str, Any],
    graph: dict[str, Any],
    node_runs: dict[str, Any],
    *,
    experiment_id: uuid.UUID | None = None,
    effective_cell_label: str | None = None,
    script_bound: bool = False,
    seeded_datasets: tuple[tuple[str, str], ...] = (),
    unsplit_dataset: str = "",
    upstream_ids: list[str] | None = None,
    unresolved_out: list[str] | None = None,
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

    *script_bound* says the wired scripts reached ``_meta`` as paths
    (``_ambient_meta_for``). Production callers provide either the experiment
    workspace or an isolated standalone-run directory, so source stays out of
    the prompt in both cases. The false branch is a defensive diagnostic for a
    materialization failure.

    *seeded_datasets* are the ``(dataset name, workspace slot)`` pairs ASAREE
    already opened on the agent's behalf (``_resolve_node_dataset``). When
    anything was seeded, the Dataset block stops asking for a tool call at all
    and just says the data is there -- opening a workspace was never a decision
    worth spending an agent turn on, and a step the agent can't skip is a step
    it can't get wrong. With several, the slot keys are named, because which
    dataset a call is about is the one part of that no ambient value can decide.

    *unsplit_dataset* is the same resolver's other outcome: a registration with
    no train/test split, bound as a plain file. Mutually exclusive with
    *seeded_datasets* -- a dataset has a split or it doesn't.

    *upstream_ids* overrides which senders the block draws from. Defaults to
    this node's main-edge predecessors, which is right for a pipeline; a
    caller that already knows who spoke to this agent (the messenger, whose
    dispatch does not have to be a direct edge) passes it explicitly rather
    than hoping the topology agrees.

    *unresolved_out*, when given, collects the node ids whose referenced output
    was empty. An out-parameter rather than a second return value because five
    of the six call sites do not care and one of them is an inline argument
    expression -- and because the string this returns is the whole point of
    calling it. Empty is the normal case.

    The prompt's own ``{{...}}`` references are resolved
    (:mod:`asaree.services.prompt_references`), the direct predecessors it did
    *not* place by hand are appended as an upstream block, and the shape the
    node's Output Parser declares is appended last."""
    seed = _node_seed_prompt(node)
    # Computed from the *authored* text, before substitution replaces the
    # tokens with the payloads they name and there is nothing left to detect.
    hand_placed = _hand_placed_sender_ids(seed, graph, node["id"])
    seed, unresolved = _resolve_prompt_references(seed, graph, node["id"], node_runs)
    if unresolved:
        # Recorded, not raised -- see _render_reference. The out-parameter
        # is what the Runs tab reads; the log line is for a call site that
        # did not pass one.
        if unresolved_out is not None:
            unresolved_out.extend(unresolved)
        logger.warning(
            "prompt references resolved empty: node=%s referenced=%s",
            node["id"],
            ",".join(unresolved),
        )
    parts = [seed]

    upstream_context = _upstream_context(
        graph, node["id"], node_runs, upstream_ids=upstream_ids, exclude_ids=hand_placed
    )
    if upstream_context:
        parts.append(upstream_context)

    resource_catalog = _resource_catalog(graph, node["id"])
    if resource_catalog:
        parts.append(resource_catalog)

    dataset_configs = _resolve_dataset_configs(graph, node["id"])
    if dataset_configs and experiment_id is not None and effective_cell_label is not None:
        dataset_names = [str(c["dataset_name"]) for c in dataset_configs]
        if len(seeded_datasets) == 1:
            # Nothing to call: the workspace was seeded before this turn, and
            # every workspace/domain tool resolves it from ambient _meta. Named
            # rather than left implicit so the agent can report what it worked
            # on, and so a wrong wiring is visible in the transcript.
            parts.append(
                "Dataset context:\n"
                f"Your data is already open: the dataset {seeded_datasets[0][0]!r} is loaded into this "
                "cell's workspace at HEAD. Do NOT call open_workspace -- the workspace tools and "
                "the sklearn tools all resolve it from ambient run context, so omit any "
                "data_path/workspace_id/target_column argument and never build one out of an id "
                "another tool reported (a workspace id is not a file path). Start with the "
                "analysis itself. (workspace_status() reports the current state if you need it.)"
            )
        elif seeded_datasets:
            # Several datasets, each already open in its own workspace slot.
            # The one thing ambient context cannot decide is WHICH -- that is a
            # real choice about the analysis -- so the slot keys stay in the
            # prompt, and only the keys: everything else still resolves
            # ambiently. Slots are named, not positional, so a tool call that
            # omits one gets an error listing them rather than a default.
            listed = "\n".join(f'- {name!r} -> slot="{slot}"' for name, slot in seeded_datasets)
            parts.append(
                "Dataset context:\n"
                f"{len(seeded_datasets)} datasets are already open in this cell's workspace, each in "
                f"its own slot at its own HEAD:\n{listed}\n"
                'Do NOT call open_workspace -- they are all loaded. Pass slot="..." to the workspace '
                "and staging tools to say which one a call is about; omit every other argument, since "
                "the workspace itself arrives as ambient run context. Each slot stages independently, "
                "so accepting a stage in one does not touch the others. "
                "(workspace_status() lists the slots and their current state.)"
            )
        elif unsplit_dataset:
            # No workspace and no frozen split -- the raw file is bound as the
            # run's data_path instead. The agent has to make the split, so the
            # prompt says so outright: left to infer it, a model reaches for
            # open_workspace (which will refuse) or invents a test_path.
            parts.append(
                "Dataset context:\n"
                f"The dataset {unsplit_dataset!r} is bound to this step as a file, and it has NOT "
                "been split into train/test. Do NOT call open_workspace -- there is no workspace "
                "for an unsplit dataset, and the staged workspace tools have nothing to work on. "
                "Use the sklearn tools: they resolve the file and its target column from ambient "
                "run context, so omit data_path/target_column, and they hold out their own test "
                "split on every call. Making the split is your job here -- start with "
                "describe_dataset, then describe_split (or train_test_split, which writes the two "
                "halves out) to check it isn't leaking before you fit."
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
            # Several wired and none of them pre-seeded -- an unlinked protocol
            # run (no workspace id at all), or every registration was broken or
            # unsplit. `name` is the one thing _meta can't decide for the agent:
            # the ambient fallback deliberately refuses to guess among several,
            # so the names stay in the prompt. experiment_id/cell_label still do
            # not -- they come from the ambient workspace_id.
            listed = "\n".join(f'- "{n}"' for n in dataset_names)
            parts.append(
                "Dataset context:\n"
                f"{len(dataset_names)} datasets are registered for this run:\n{listed}\n"
                "Call open_workspace(name=...) for each one you need, before doing any data work. "
                "`name` is the only argument to pass; the rest arrives as ambient run context. Each "
                "dataset opens into its own slot of this cell's workspace and stages independently, "
                'so pass slot="..." (the response names it) to say which one a later call is about.'
            )

    script_configs = [config for config in _resolve_script_configs(graph, node["id"]) if config.get("code")]
    if script_configs and script_bound:
        if len(script_configs) == 1:
            parts.append(
                "Script context:\n"
                "A script is wired into this step. Call run_wired_script() -- no arguments -- and it "
                "executes exactly what the user wrote; read its stdout for the result. Do not retype or "
                "paraphrase the script. (A sklearn script tool, e.g. run_model_script, picks the same "
                "script up the same way if this step is about fitting a model.)"
            )
        else:
            listed = "\n".join(
                f"- {str(config.get('name') or f'script-{index}')!r} (id: {config['node_id']!r})"
                for index, config in enumerate(script_configs, start=1)
            )
            parts.append(
                "Script context:\n"
                f"{len(script_configs)} scripts are wired into this step:\n{listed}\n"
                "Call run_wired_script(script=...) with a script name or id to execute exactly what the user "
                "wrote, then read its stdout. Do not retype or paraphrase a script. If names are duplicated, "
                "select by id."
            )
    elif script_configs:
        parts.append(
            "Script context:\n"
            "A script is wired into this unlinked run, but no isolated run workspace exists in which to materialize "
            "it. Its source has deliberately not been inserted into the prompt. Link the protocol to an experiment "
            "to execute wired scripts."
        )

    # The shape block is the only *prose* this function composes. Everything
    # else appended here is either the user's own text or a labelled, fenced
    # payload; the two platform-authored sentences that used to live behind
    # `{{audience}}` and `{{upstream_instructions}}` are gone, because what an
    # agent should be told about its position and about how to treat its
    # predecessor's output is the experimenter's wording, not the platform's.
    #
    # This block survives that because it is not the platform's opinion: it is
    # the user's own declaration about their own node, made by wiring an Output
    # Parser to it. Making them insert a token as well would be ceremony, not
    # consent.
    #
    # It also used to have a prose twin, `config.expected_output`, appended just
    # above this. That field is gone: one node said what shape to produce and a
    # second said which fields to state, so a user had to keep two descriptions
    # of one answer in agreement by hand, and the free-text one was the half
    # nothing could read back. Asking for the shape and extracting it are now
    # the same declaration -- see `_output_shape_block`.
    shape_block = _output_shape_block(_resolve_output_contract(graph, node["id"]))
    if shape_block:
        parts.append(shape_block)

    return "\n\n".join(parts)


def _build_system_prompt(
    node: dict[str, Any],
    graph: dict[str, Any],
    node_runs: dict[str, Any],
    *,
    unresolved_out: list[str] | None = None,
) -> str | None:
    """The user-authored System prompt with its references resolved, or
    ``None`` when the node has none.

    ``None`` rather than an empty string so the caller keeps its own
    ``_default_system_prompt`` fallback -- deciding what an unset system prompt
    becomes is :func:`_run_agent_node`'s job, and duplicating it here would give
    two answers to drift apart.

    The System prompt gets the same references the user prompt does because the
    picker offers them in both boxes; a token that resolved in one field and
    arrived as literal ``{{node:...}}`` text in the other would be the worse
    outcome. Note what that means: a referenced upstream output is *model* text
    landing at the highest-trust position in the request. It is fenced by
    :func:`fence_upstream` exactly as in the user prompt (same
    :func:`_render_reference`), which is what keeps it quotable material rather
    than instructions -- but a user who writes ``{{previous}}`` into a system
    prompt is choosing that placement, so it is theirs to choose deliberately.
    """
    authored = (node.get("data", {}).get("config", {}) or {}).get("system_prompt")
    if not authored:
        return None
    rendered, unresolved = _resolve_prompt_references(str(authored), graph, node["id"], node_runs)
    if unresolved:
        if unresolved_out is not None:
            unresolved_out.extend(unresolved)
        logger.warning(
            "system prompt references resolved empty: node=%s referenced=%s",
            node["id"],
            ",".join(unresolved),
        )
    return rendered


#: The cell label a preview claims to be for. Never written anywhere -- it only
#: has to be non-None, because that is what gates the Dataset block on.
_PREVIEW_CELL_LABEL = "preview"


async def _preview_node_dataset(graph: dict[str, Any], node_id: str, owner_id: uuid.UUID) -> NodeDataset:
    """:func:`_resolve_node_dataset`'s answer, without doing any of the work.

    That function seeds the cell's workspace as a side effect, which a preview
    must not do -- so this repeats only its *classification* (registered and
    split -> seeded; registered without a split and alone -> unsplit; neither ->
    nothing) against the same registration read and the same slot naming.

    The one thing it cannot know is what ``seed_cell_workspace`` would report
    back: a workspace already in the pre-slot on-disk format keeps a single
    unnamed slot regardless of what was asked for. A preview names the slot the
    canvas implies, which is what a workspace created fresh for the next cell
    will actually use.
    """
    names = [str(c["dataset_name"]) for c in _resolve_dataset_configs(graph, node_id) if c.get("dataset_name")]
    if not names:
        return NodeDataset()
    solo = len(names) == 1

    seeded: list[tuple[str, str]] = []
    for name in names:
        reg = await fetch_owned_registration(name, owner_id)
        if reg is None:
            continue
        for config in _resolve_dataset_configs(graph, node_id):
            if str(config.get("dataset_name") or "") == name:
                config.update(
                    description=reg.get("description"),
                    target_column=reg.get("target_column"),
                    split_state="split" if reg.get("train_path") and reg.get("test_path") else "unsplit",
                    dictionary_available=bool(reg.get("dictionary_json")),
                )
        if not (reg.get("train_path") and reg.get("test_path")):
            if solo:
                return NodeDataset(
                    unsplit_name=name,
                    data_path=str(reg.get("raw_path") or ""),
                    target_column=str(reg.get("target_column") or ""),
                )
            continue
        seeded.append((name, "" if solo else dataset_slot(name)))
    return NodeDataset(seeded=tuple(seeded))


def _preview_node_run(graph: dict[str, Any], node: dict[str, Any], node_id: str) -> dict[str, Any]:
    """One upstream node's stand-in run for :func:`preview_node_prompt`.

    The payload stands in field by field, for the same reason ``output_text``
    does: a field reference that previews as nothing renders a sentence with a
    hole in it (``The dataset has  rows.``), which reads as a broken prompt
    rather than as a value that does not exist yet. Only the fields the node's
    Output Parser actually declares get a stand-in, so referencing one it does
    not declare still previews as the gap it will really be -- the preview is
    allowed to be unfinished, never to be wrong.
    """
    name = _node_display_name(node)
    contract = _resolve_output_contract(graph, node_id) or {}
    return {
        "status": "completed",
        "output_text": f'<output of "{name}">',
        "error": None,
        "payload": {
            field["name"]: f'<{field["name"]} of "{name}">'
            for field in contract.get("fields") or []
            if field.get("name")
        },
    }


async def preview_node_prompt(
    graph: dict[str, Any],
    node_id: str,
    *,
    owner_id: uuid.UUID,
    experiment_id: uuid.UUID | None = None,
) -> str:
    """The exact prompt this agent would be given, assembled from the draft canvas.

    Wraps :func:`_build_user_input` rather than re-deriving anything: a preview
    that drifts from the real prompt is worse than no preview, so the only
    difference between this and a run is what it is given to work with.

    Upstream output is the one thing that genuinely does not exist yet, so each
    referenceable ancestor stands in as ``<output of "Name">``. Every ancestor
    gets one, not just the direct predecessors -- ``referenceable_node_ids`` is
    the same set the picker offers, so a reach-back reference previews as
    something rather than as a gap it would not really leave.

    Creates nothing: no ``ProtocolRun``, no agent run, no workspace (see
    :func:`_preview_node_dataset`).

    Raises :class:`ProtocolValidationError` for a node that is not an agent --
    only an agent is given a prompt, and previewing a connector would be
    inventing one.
    """
    nodes = {str(n.get("id")): n for n in graph.get("nodes") or [] if n.get("id")}
    node = nodes.get(node_id)
    if node is None:
        raise ProtocolValidationError(f"No node {node_id!r} on this canvas.")
    if node.get("type") != "agent":
        raise ProtocolValidationError(f"{_node_display_name(node)} is not an agent, so it is never given a prompt.")

    node_runs = {
        upstream_id: _preview_node_run(graph, nodes[upstream_id], upstream_id)
        for upstream_id in referenceable_node_ids(graph, node_id)
        if upstream_id in nodes
    }
    dataset = await _preview_node_dataset(graph, node_id, owner_id)
    return _build_user_input(
        node,
        graph,
        node_runs,
        experiment_id=experiment_id,
        effective_cell_label=_PREVIEW_CELL_LABEL,
        # True exactly when the run would have a workspace to write the script
        # into (_materialize_script) -- which is what an experiment-linked run
        # always has, and an unlinked one never does.
        script_bound=experiment_id is not None,
        seeded_datasets=dataset.seeded,
        unsplit_dataset=dataset.unsplit_name,
    )


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


async def _monitor_protocol_run(protocol_run_id: uuid.UUID, cancel_event: asyncio.Event, interval: float = 1.5) -> None:
    """Monitor cancellation and refresh liveness during an in-flight run.

    Watches for a Stop click (POST .../cancel) that a completely different
    request -- possibly a different worker process entirely, since protocol
    runs execute in arq's worker, not the API process -- raised on this run's
    own row. It also periodically refreshes the run heartbeat so stale-run
    reconciliation does not fail a live attempt.
    Sets cancel_event the moment cancel_requested_at is seen populated;
    Motoro's own runtime checks that event before every Sense/Reason/
    Plan/Act phase (motoro.engine.runtime.AgentRuntime._check_interrupt),
    which is what actually lets a single agent's run wind down mid-loop
    instead of only ever being caught at run_protocol's own between-nodes
    check (which can't interrupt a node already in flight)."""
    loop = asyncio.get_running_loop()
    last_heartbeat = loop.time()
    while True:
        await asyncio.sleep(interval)
        async with get_session() as db:
            requested_at = await get_cancel_requested_at(db, protocol_run_id)
            if loop.time() - last_heartbeat >= 30:
                await touch_protocol_run_heartbeat(db, protocol_run_id)
                last_heartbeat = loop.time()
        if requested_at is not None:
            cancel_event.set()
            return


async def _execute_run_cancellable(
    *,
    run_id: uuid.UUID,
    protocol_run_id: uuid.UUID,
    available_tools: list[dict[str, Any]],
    timeout: float,
    available_agents: list[dict[str, Any]] | None = None,
    agent_messenger: Any = None,
) -> None:
    """Wraps Motoro's execute_run with the poller above, scoped to
    exactly this one run's lifetime -- shared by _run_agent_node and
    _run_critic rather than duplicating the poller's start/stop lifecycle in
    both. Deliberately per-call, not per-protocol-run: once a Stop is
    detected, run_protocol's own between-nodes check means no later node
    ever starts, so nothing else would benefit from a longer-lived poller,
    and tearing this one down between nodes avoids running it during gaps
    where no agent is actually executing.

    ``available_agents``/``agent_messenger`` travel together and are both empty
    for a pipeline run: cards with no messenger would let an agent see peers it
    could not reach, and a messenger with no cards would never be called.

    ``timeout`` is enforced through an extendable :class:`Deadline` rather than
    ``asyncio.wait_for``, so that time this agent spends *blocked on a peer* can
    be handed back to it -- an agent is charged for its own thinking, never for
    waiting. With no peers the two are indistinguishable: nothing extends the
    deadline, and the run is cancelled at exactly ``timeout`` seconds as
    before."""
    cancel_event = asyncio.Event()
    poller = asyncio.create_task(_monitor_protocol_run(protocol_run_id, cancel_event))
    try:
        # Entered before create_task so the runner's copied context already
        # holds this frame, which is how a nested peer run reaches back to
        # extend it (see services.deadline).
        with active_deadline(Deadline(timeout)) as deadline:
            runner = asyncio.create_task(
                execute_run(
                    run_id=run_id,
                    registry=get_registry(),
                    available_tools=available_tools,
                    cancel_event=cancel_event,
                    available_agents=available_agents or [],
                    agent_messenger=agent_messenger,
                )
            )
            try:
                while True:
                    if deadline.expired():
                        raise TimeoutError
                    # Re-read after every wait rather than waiting once for the
                    # full span: a consultation may have stopped the clock in
                    # the meantime, so what looked like the end of the budget
                    # no longer is. A paged deadline reports a poll interval
                    # instead of its frozen remainder, which is what makes this
                    # loop again rather than spin.
                    done, _ = await asyncio.wait({runner}, timeout=deadline.remaining())
                    if done:
                        await runner  # re-raise whatever the run itself raised
                        return
            finally:
                if not runner.done():
                    runner.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await runner
    finally:
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller


async def _sync_durable_agent(*, name: str, owner_id: uuid.UUID, fields: dict[str, Any]) -> Any:
    """Create or update a protocol-owned Agent without a check-then-insert race.

    Multiple protocol runs can reach the same canvas node concurrently. The
    durable Motoro Agent is keyed by owner/name, so a losing create retries as
    an update of the row the concurrent request just inserted.

    **Never put per-run state in ``fields``.** Every entry here must come from
    the node's canvas config or its wired connectors -- i.e. from the protocol
    itself, which is what this row is a projection of. Run-scoped configuration
    belongs on the run: ``create_run`` already takes ``model_config_overrides``
    and ``config_snapshot``. The rule needs stating because this write path
    makes the mistake cheap to make and expensive to notice: ``update_agent``
    reads ``None`` as "leave unchanged", so a field written once during one run
    is never cleared by any later run and quietly contaminates every future one
    (see ``scripts/repair_contaminated_agents.py``, which cleans up an earlier
    design that injected a conversation contract this way).
    """
    existing = await get_agent_by_name(name, owner_id=owner_id)
    if existing is not None:
        return await update_agent(existing.id, **fields)
    try:
        return await create_agent(name=name, owner_id=owner_id, **fields)
    except IntegrityError:
        existing = await get_agent_by_name(name, owner_id=owner_id)
        if existing is None:
            raise
        return await update_agent(existing.id, **fields)


# What an all-null payload means, said once. Motoro's extractor builds its
# model with every contracted field typed ``T | None`` and is forbidden from
# inferring a value the text does not state, so "the parser ran and found
# nothing" and "the parser ran and the answer genuinely had none of these"
# produce the identical object: every key present, every value null. Without
# this the UI renders that as a result -- a tidy list of fields whose answer is
# "null" -- which reads as a finding rather than as a failed read.
_EMPTY_PAYLOAD_CAVEAT = (
    "every field the Output Parser declared came back empty: the text it was given "
    "stated none of them, so this is a failed read rather than a result"
)


def _payload_is_empty(payload: Any) -> bool:
    """True when a payload came back shaped but entirely unfilled.

    Non-empty on purpose: a contract declaring no fields at all would otherwise
    trip this vacuously, and there is nothing to warn about when nothing was
    asked for.
    """
    return isinstance(payload, Mapping) and len(payload) > 0 and all(value is None for value in payload.values())


def _extraction_fields(envelope: OutputEnvelope | None) -> dict[str, Any] | None:
    """What an Output Parser contributed to one node run, or ``None``.

    Written as a fragment the node run merges rather than as two more return
    values: both keys are optional, both come from the same place, and a caller
    that does not care about either can ignore one value instead of two.

    ``caveats`` is kept even when a payload came back -- Motoro's extractor can
    coerce a field and still report that it guessed -- and an empty list is
    dropped, so the key's presence means there is something to read.
    """
    if envelope is None:
        return None
    fields: dict[str, Any] = {}
    caveats = list(envelope.caveats or [])
    if envelope.payload is not None:
        fields["payload"] = envelope.payload
        if _payload_is_empty(envelope.payload):
            caveats.append(_EMPTY_PAYLOAD_CAVEAT)
    if caveats:
        fields["caveats"] = caveats
    return fields or None


def _truncation_fields(run: Any) -> dict[str, Any] | None:
    """Whether the agent's loop was cut off by its iteration ceiling, or ``None``.

    A Reason+Act run that exhausts ``max_iterations`` does NOT fail: Motoro's
    loop falls through its ``for...else``, keeps whatever the last Act produced
    as the run output, and still reports ``completed``
    (``motoro/engine/runtime.py``). Downstream that is indistinguishable from an
    agent that finished -- which is how a run whose report was never written
    ends up handed to an Output Parser that can only find nulls in a tool dump.

    Read from ``agent_runs.pattern_overrides`` rather than rederived from the
    step list: the ReasonAct pattern already records exactly this
    (``reason_act_state``, see its ``_state``/``_persist_state``), and the step
    rows cannot answer it -- the persisted Act step carries the pre-hook
    ``should_continue`` and is ``false`` on every run, truncated or not.

    Deliberately NOT expressed as a node-run *status*: the status vocabulary is
    read in ~20 places (metric collection, result-node gating, the conversation
    and supervisor flows) that all mean "did this node produce usable output",
    and the answer for a truncated run is still yes -- its work up to the
    ceiling is real. This is a flag on a completed run, the way ``caveats`` is.
    """
    state = (getattr(run, "pattern_overrides", None) or {}).get("reason_act_state")
    if not isinstance(state, Mapping) or not state.get("max_iterations_hit"):
        return None
    return {
        "truncation": {
            "reason": str(state.get("terminated_by") or "max_iterations"),
            "iterations": state.get("iterations"),
            "max_iterations": state.get("max_iterations"),
        }
    }


async def _run_agent_node(
    node: dict[str, Any],
    *,
    protocol_id: uuid.UUID,
    protocol_run_id: uuid.UUID,
    owner_id: uuid.UUID,
    user_input: str,
    graph: dict[str, Any],
    system_prompt: str | None = None,
    workspace_id: str | None = None,
    ambient_meta: dict[str, Any] | None = None,
    available_agents: list[dict[str, Any]] | None = None,
    agent_messenger: Any = None,
    unsplit_dataset: str = "",
) -> tuple[str | None, str | None, uuid.UUID | None, dict[str, Any] | None]:
    """Create-or-sync the real agent and run it to completion. Returns
    ``(output_text, error, run_id, extraction)`` -- exactly one of
    output_text/error is ``None``. ``run_id`` is the underlying Motoro AgentRun
    id -- always populated once ``create_run`` succeeds (even on a later
    timeout/error), since that's what the canvas's Output tab uses to fetch
    this node's own step trace (``GET /runs/{run_id}/steps``); only ``None`` if
    agent creation/sync itself failed before a run could even be created.

    ``extraction`` is the annotation fragment, ready to merge into the node run:
    ``payload`` (the envelope's typed object) when the extraction succeeded,
    ``caveats`` when it had something to say about why it did not, and
    ``truncation`` when the agent's loop was cut off by its iteration ceiling
    (:func:`_truncation_fields`) rather than by the agent deciding it was done.
    ``None`` when there was no parser and nothing to report. It is returned
    *alongside* ``output_text``, never instead of it: extraction is post-hoc and
    best-effort (``extract_payload`` returns ``(None, caveats)`` rather than
    raising), so the prose handoff must never depend on it having worked -- and
    a failed extraction has to be visible rather than merely absent, which is
    what the caveats are for.

    ``available_agents`` are the serialized peer cards this node may consult
    (:func:`resolve_available_agents`) and ``agent_messenger`` is how a chosen
    consultation is delivered. Both default to empty, which is what a pipeline
    run passes: peers are a conversation-mode capability, so an ordinary run is
    byte-for-byte what it was before. They are deliberately *not* folded into
    ``_sync_durable_agent``'s fields -- the card is per-run and derived, and
    writing it onto the durable agent row is exactly the contamination this
    design avoids."""
    config = node["data"]["config"]
    # Deterministic, not config["name"]: Agent.name is unique per OWNER, not
    # per protocol, so trusting the freeform (often identically-defaulted)
    # config.name directly risks two unrelated nodes silently overwriting
    # each other's agent definition on every run. config.name is folded
    # into the description instead, purely as a human label.
    agent_name = f"protocol-{protocol_id}-{node['id']}"
    # Model/tool/execution-pattern are no longer fields on the agent's own
    # config -- resolved from its required Model connector, its (optional,
    # repeatable) Tool connectors, and its optional Architectural Pattern
    # connector instead (topological_order already validated their shape).
    # output_contract joined them, with one extra argument the others didn't
    # need: extraction is a second LLM call per run, so its cost belongs on the
    # canvas. Unlike the three above, the node's own field is still read as a
    # fallback and always will be -- see _resolve_output_contract.
    model_config_data = {k: v for k, v in _resolve_model_config(graph, node["id"]).items() if v is not None}
    model_config = ModelConfig(**model_config_data)
    # Four connectors feed one allow-list. The Knowledge connector's OKF
    # bundles and documents are MCP servers like any other, so they land here
    # rather than in a slot of their own (see _resolve_knowledge_config), and
    # the Dataset and Script connectors imply ASAREE's own workspace and
    # script-running tools (see _resolve_dataset_tool_config /
    # _resolve_script_tool_config) -- the split between the four is about
    # what the user is declaring, not about how the engine consumes it.
    tool_config = _merge_tool_configs(
        _resolve_tool_config(graph, node["id"]),
        _resolve_knowledge_config(graph, node["id"]),
        _resolve_dataset_tool_config(graph, node["id"], unsplit_dataset=unsplit_dataset),
        _resolve_script_tool_config(graph, node["id"]),
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
    #
    # The *system_prompt* argument, when given, is that same authored field
    # with its ``{{...}}`` references already resolved (:func:`_build_system_prompt`) --
    # resolution needs `node_runs`, which this function does not have. Falling
    # back to the raw field keeps the call sites that have nothing to resolve
    # against (a single-node run) working unchanged.
    resolved_system_prompt = system_prompt or config.get("system_prompt") or _default_system_prompt(label, "Agent")

    agent = await _sync_durable_agent(
        name=agent_name,
        owner_id=owner_id,
        fields={
            "goal": config.get("goal") or "",
            "description": description,
            "system_prompt": resolved_system_prompt,
            "model_config": model_config,
            "pattern_config": pattern_config,
            "tool_config": tool_config,
            "skill_config": skill_config,
            # From the wired Output Parser node, falling back to the agent's own
            # stored field for graphs that predate it -- see
            # _resolve_output_contract for why that fallback is permanent.
            "output_contract": _resolve_output_contract(graph, node["id"]),
            "budget_limit_usd": config.get("budget_limit_usd"),
            "max_run_duration_seconds": config.get("max_run_duration_seconds"),
        },
    )
    assert agent is not None

    resolved_ambient = (
        ambient_meta
        if ambient_meta is not None
        else _ambient_meta_for(
            graph,
            node["id"],
            workspace_id,
            script_workspace_id=_script_workspace_id(workspace_id, protocol_run_id, str(node["id"])),
        )
    )
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
            **({"ambient_meta": resolved_ambient} if resolved_ambient else {}),
        },
    )
    timeout = agent.max_run_duration_seconds or get_settings().worker_job_timeout_seconds
    try:
        await _execute_run_cancellable(
            run_id=run.id,
            protocol_run_id=protocol_run_id,
            available_tools=gather_tools(agent),
            timeout=timeout,
            available_agents=available_agents,
            agent_messenger=agent_messenger,
        )
    except TimeoutError:
        return None, f"run exceeded its {timeout}s execution budget", run.id, None
    except Exception as e:  # noqa: BLE001 -- same boundary reasoning as execute_run_task
        return None, f"{type(e).__name__}: {e}", run.id, None
    finally:
        _cleanup_adhoc_scripts(resolved_ambient)

    finished = await get_run(run.id)
    if finished is None:
        return None, "run vanished after execution", run.id, None
    if finished.status == RunStatus.CANCELLED:
        # finished.error is None on a clean cancellation (Motoro's own
        # runtime never sets error_msg on that path) -- without this check
        # a mid-run Stop would silently fall through and look like a normal
        # completion with an empty output.
        return None, _AGENT_CANCELLED, run.id, None
    if finished.error:
        return None, finished.error, run.id, None
    envelope = parse_envelope(finished.output)
    output_text = envelope.result if envelope is not None else (finished.output or "")
    # Merged into one fragment because both are node-run annotations on a run
    # that completed, and they are usually seen together: hitting the ceiling
    # is the single most common reason the parser has nothing to read.
    node_fields = {**(_extraction_fields(envelope) or {}), **(_truncation_fields(finished) or {})}
    return output_text, None, run.id, node_fields or None


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
    from its required Model connector, same as an agent node."""
    config = gate["data"]["config"]
    agent_name = f"protocol-{protocol_id}-{gate['id']}"
    model_config_data = {k: v for k, v in _resolve_model_config(graph, gate["id"]).items() if v is not None}
    model_config = ModelConfig(**model_config_data)
    pattern_config = PatternConfig(execution_pattern="single_agent_baseline").model_dump()
    goal = config.get("goal") or "Review the given output and return an approval verdict with feedback."
    description = config.get("description") or ""
    label = gate.get("data", {}).get("label")
    if label:
        description = f"{description} (canvas label: {label})".strip()
    system_prompt = config.get("system_prompt") or _default_system_prompt(label, "Critic Gate")
    tool_config: dict[str, list[str]] = {"server_names": [], "tool_names": []}

    agent = await _sync_durable_agent(
        name=agent_name,
        owner_id=owner_id,
        fields={
            "goal": goal,
            "description": description,
            "system_prompt": system_prompt,
            "model_config": model_config,
            "pattern_config": pattern_config,
            "tool_config": tool_config,
            "output_contract": CRITIC_OUTPUT_CONTRACT,
        },
    )
    assert agent is not None

    instruction = f"Review the following output and return your verdict.\n\nOutput to review:\n\n{worker_output}"
    run = await create_run(
        agent_id=agent.id,
        user_input=instruction,
        owner_id=owner_id,
        metadata={
            "protocol_id": str(protocol_id),
            "protocol_run_id": str(protocol_run_id),
            "node_id": gate["id"],
            "runtime_role": "critic",
        },
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


def _completed_worker_record(
    output_text: str, attempt: int, run_id_str: str | None, extraction: dict[str, Any] | None
) -> dict[str, Any]:
    """The worker half of a gated pair's node run, for the four ways that pair
    can end with the worker's own output intact (gate disabled, forced accept,
    critic cancelled, approved). *extraction* is merged in the same way
    ``run_protocol``'s own node_runs write does.
    """
    return {
        "status": "completed",
        "output_text": output_text,
        "error": None,
        "attempts": attempt + 1,
        "run_id": run_id_str,
        **(extraction or {}),
    }


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
    stage_plan: Any = None,
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
    worker_ambient, worker_dataset = await _node_run_context(
        graph,
        worker["id"],
        workspace_id,
        owner_id,
        protocol_run_id=protocol_run_id,
        stage_plan=stage_plan,
    )
    base_instruction = _build_user_input(
        worker,
        graph,
        node_runs,
        experiment_id=experiment_id,
        effective_cell_label=effective_cell_label,
        script_bound="script_paths" in worker_ambient,
        seeded_datasets=worker_dataset.seeded,
        unsplit_dataset=worker_dataset.unsplit_name,
    )
    # Also computed once: like the instruction, it does not vary by attempt.
    worker_system_prompt = _build_system_prompt(worker, graph, node_runs)
    instruction = base_instruction
    # Tracks the most recent critic verdict/run across attempts so the
    # forced-accept branch (which never calls the critic for its own final
    # attempt) can still surface *why* a revision was needed last time,
    # instead of silently discarding that context once it stops being used
    # to build the next instruction.
    last_verdict: dict[str, Any] | None = None
    last_critic_run_id: str | None = None

    for attempt in range(max_revisions + 1):
        output_text, error, run_id, extraction = await _run_agent_node(
            worker,
            protocol_id=protocol_id,
            protocol_run_id=protocol_run_id,
            owner_id=owner_id,
            user_input=instruction,
            graph=graph,
            system_prompt=worker_system_prompt,
            workspace_id=workspace_id,
            ambient_meta=worker_ambient,
            unsplit_dataset=worker_dataset.unsplit_name,
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
                _completed_worker_record(output_text, attempt, run_id_str, extraction),
                {"status": "completed", "output_text": output_text, "approved": None, "revisions_used": 0},
            )

        if attempt == max_revisions:
            return (
                _completed_worker_record(output_text, attempt, run_id_str, extraction),
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
                _completed_worker_record(output_text, attempt, run_id_str, extraction),
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
                _completed_worker_record(output_text, attempt, run_id_str, extraction),
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
    protocol_revision_id: uuid.UUID | None = None,
    replicate_labels: set[str] | None = None,
    rerun_replicate_labels: set[str] | None = None,
) -> tuple[list[ProtocolRun], int]:
    """ "Run all cells": creates one pending :class:`ProtocolRun` per
    not-yet-completed replicate row under *experiment_id*, each carrying its
    cell's ``factor_values`` for ``run_protocol`` to
    substitute at execution time via ``apply_factor_bindings``. Returns
    ``(created_runs, skipped_count)`` -- a replicate with score metrics or a
    non-obsolete completed prior ProtocolRun is skipped (resume semantics)
    unless its label appears in ``rerun_replicate_labels``. This keeps
    intentionally unscored qualitative runs from being silently re-billed,
    while letting obsolete results run against the current canvas.
    ``replicate_labels`` optionally narrows the batch to one or more
    current-design replicates. Raises
    :class:`ProtocolValidationError` (same type the plain-run endpoint
    already 422s on) if there's no linked experiment, the graph itself is
    invalid, the graph doesn't have exactly one sink node -- a replicate's result
    has to come from somewhere unambiguous, mirroring the notebook's own
    single-pipeline (DC->FTE->FS->MLM) shape -- or the experiment's declared
    coordination strategy rejects this graph (see
    ``validate_coordination_strategy``). Does NOT enqueue the created runs --
    that's the caller's job, same create-then-enqueue split
    ``create_protocol_run_endpoint`` already uses for a plain run."""
    if experiment_id is None:
        raise ProtocolValidationError("This protocol has no linked experiment to run replicates for.")
    # Strategy first, so its own message wins over a pipeline requirement that
    # may not apply to this canvas at all -- see is_conversation_strategy.
    experiment = await get_experiment(db, experiment_id)
    design_spec = experiment.design_spec if experiment is not None else None
    measurement_plan = (
        experiment.locked_measurement_plan
        if experiment is not None and experiment.locked_at is not None
        else experiment.measurement_plan
        if experiment is not None
        else None
    )
    validate_coordination_strategy(design_spec, graph=graph)
    validate_stage_plan(design_spec)
    validate_prompt_references(graph=graph)
    conversation = is_conversation_strategy(design_spec)
    topological_order(graph, require_acyclic=not conversation)  # also raises on an empty graph
    if not conversation:
        sinks = sink_node_ids(graph)
        if len(sinks) != 1:
            raise ProtocolValidationError(
                f"This protocol must have exactly one final node to run per replicate (found {len(sinks)})."
            )
    try:
        validate_factor_bindings(design_spec, graph)
    except ValueError as exc:
        raise ProtocolValidationError(str(exc)) from exc
    measurement_report = await validate_experiment_measurement_plan(
        db,
        document=measurement_plan,
        metrics=(design_spec or {}).get("metrics"),
        graph=graph,
        experiment_id=experiment_id,
        owner_id=owner_id,
    )
    if blocking_issues := blocking_measurement_plan_issues(measurement_report):
        raise ProtocolValidationError("; ".join(issue.message for issue in blocking_issues))
    impact = await get_design_impact(db, experiment_id=experiment_id, design_spec=design_spec)
    if impact.regeneration_required:
        raise ProtocolValidationError(
            "Design changed — review and regenerate before running all cells. "
            f"Current {impact.current_cell_count} cells/{impact.current_replicate_count} replicates, "
            f"proposed {impact.proposed_cell_count} cells/{impact.proposed_replicate_count} replicates."
        )

    # Current design only -- list_replicates scopes to the experiment's current
    # revision, so a superseded design's replicates are neither counted nor run.
    replicates = await list_replicates(db, experiment_id=experiment_id)
    labels = {replicate.replicate_label for replicate in replicates}
    requested_labels = labels if replicate_labels is None else replicate_labels
    unknown_requested = requested_labels - labels
    if unknown_requested:
        raise ProtocolValidationError(f"Unknown replicate label(s): {', '.join(sorted(unknown_requested))}.")
    replicates = [replicate for replicate in replicates if replicate.replicate_label in requested_labels]
    requested_reruns = rerun_replicate_labels or set()
    unknown = requested_reruns - requested_labels
    if unknown:
        raise ProtocolValidationError(f"Unknown replicate label(s): {', '.join(sorted(unknown))}.")
    run_ids = {replicate.run_id for replicate in replicates if replicate.run_id is not None}
    runs_by_id: dict[uuid.UUID, ProtocolRun] = {}
    if run_ids:
        runs_by_id = {
            run.id: run for run in (await db.execute(select(ProtocolRun).where(ProtocolRun.id.in_(run_ids)))).scalars()
        }
    published_revision = await get_revision(db, protocol_revision_id) if protocol_revision_id is not None else None

    # Keep the definition of obsolete in step with list_experiment_trials:
    # a run pinned to another revision is obsolete; an older unpinned legacy
    # run is obsolete once a newer canvas was published. Directly scored rows
    # have no ProtocolRun and therefore cannot be obsolete.
    def is_obsolete(run: ProtocolRun) -> bool:
        if protocol_revision_id is None:
            return False
        if run.protocol_revision_id is not None:
            return run.protocol_revision_id != protocol_revision_id
        return published_revision is not None and run.created_at < published_revision.published_at

    obsolete_run_ids = {run.id for run in runs_by_id.values() if is_obsolete(run)}
    completed_run_ids = {
        run.id for run in runs_by_id.values() if run.status == "completed" and run.id not in obsolete_run_ids
    }
    completed_labels = {
        replicate.replicate_label
        for replicate in replicates
        if (replicate.metric_values and replicate.run_id not in obsolete_run_ids)
        or replicate.run_id in completed_run_ids
    }
    not_completed = requested_reruns - completed_labels
    if not_completed:
        raise ProtocolValidationError(
            f"Only previously completed replicates can be selected to run again: {', '.join(sorted(not_completed))}."
        )
    pending = [
        replicate
        for replicate in replicates
        if replicate.replicate_label not in completed_labels or replicate.replicate_label in requested_reruns
    ]
    runs = [
        await create_protocol_run(
            db,
            protocol_id=protocol_id,
            owner_id=owner_id,
            replicate_label=replicate.replicate_label,
            factor_values=replicate.factor_values or {},
            replicate_result_id=replicate.id,
            design_revision_id=replicate.design_revision_id,
            protocol_revision_id=protocol_revision_id,
        )
        for replicate in pending
    ]
    return runs, len(replicates) - len(pending)


async def plan_single_replicate_run(
    db: AsyncSession,
    *,
    protocol_id: uuid.UUID,
    experiment_id: uuid.UUID | None,
    owner_id: uuid.UUID,
    graph: dict[str, Any],
    replicate_label: str,
    protocol_revision_id: uuid.UUID | None = None,
) -> ProtocolRun:
    """Run one already-generated replicate for real, by name -- the single-run
    counterpart to plan_cell_runs's own "every not-yet-completed replicate" batch.
    The canvas's own Run button offers this alongside its existing ad-hoc
    (no substitution) run once the linked experiment has generated cells,
    for testing one specific factor combination without either running
    everything or falling back to an un-substituted smoke test. Same
    validation as plan_cell_runs (linked experiment, valid graph, exactly
    one sink, coordination strategy) but does NOT skip an already-completed
    replicate -- picking one specific replicate by name is a deliberate re-run, not a
    batch resume, so there's nothing to protect it from."""
    if experiment_id is None:
        raise ProtocolValidationError("This protocol has no linked experiment to run a replicate for.")
    # Same order and same reason as plan_cell_runs above.
    experiment = await get_experiment(db, experiment_id)
    design_spec = experiment.design_spec if experiment is not None else None
    measurement_plan = (
        experiment.locked_measurement_plan
        if experiment is not None and experiment.locked_at is not None
        else experiment.measurement_plan
        if experiment is not None
        else None
    )
    validate_coordination_strategy(design_spec, graph=graph)
    validate_stage_plan(design_spec)
    validate_prompt_references(graph=graph)
    conversation = is_conversation_strategy(design_spec)
    topological_order(graph, require_acyclic=not conversation)  # also raises on an empty graph
    if not conversation:
        sinks = sink_node_ids(graph)
        if len(sinks) != 1:
            raise ProtocolValidationError(
                f"This protocol must have exactly one final node to run per replicate (found {len(sinks)})."
            )
    try:
        validate_factor_bindings(design_spec, graph)
    except ValueError as exc:
        raise ProtocolValidationError(str(exc)) from exc
    measurement_report = await validate_experiment_measurement_plan(
        db,
        document=measurement_plan,
        metrics=(design_spec or {}).get("metrics"),
        graph=graph,
        experiment_id=experiment_id,
        owner_id=owner_id,
    )
    if blocking_issues := blocking_measurement_plan_issues(measurement_report):
        raise ProtocolValidationError("; ".join(issue.message for issue in blocking_issues))
    impact = await get_design_impact(db, experiment_id=experiment_id, design_spec=design_spec)
    if impact.regeneration_required:
        raise ProtocolValidationError("Design changed — review and regenerate before running a replicate.")

    replicate = await get_replicate(db, experiment_id=experiment_id, replicate_label=replicate_label)
    if replicate is None:
        raise ProtocolValidationError(f"No such replicate: {replicate_label!r}")
    return await create_protocol_run(
        db,
        protocol_id=protocol_id,
        owner_id=owner_id,
        replicate_label=replicate.replicate_label,
        factor_values=replicate.factor_values or {},
        replicate_result_id=replicate.id,
        design_revision_id=replicate.design_revision_id,
        protocol_revision_id=protocol_revision_id,
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
    model_edges = _edges_with_handle(graph, node_id, "model", direction="incoming")
    if len(model_edges) != 1:
        raise ProtocolValidationError(
            f"Node {_node_display_name(node)!r} must have exactly one Model connection (found {len(model_edges)})."
        )
    model_source = nodes.get(model_edges[0]["source"])
    if model_source is None or model_source.get("type") not in _MODEL_NODE_TYPES:
        raise ProtocolValidationError(
            f"Node {_node_display_name(node)!r}'s Model connection must come from a Model node."
        )
    return node


def validate_conversation_entry(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    """Validates an agent can host a conversation. Returns the node dict.

    Scoped to the entry agent and its peers, deliberately *not* to the whole
    graph: a conversation is a cluster of connected agents, and a half-configured
    node in some unrelated corner of the same canvas has nothing to do with it.

    Notably absent: any check on how many peer edges the *graph* has. An earlier
    design required exactly one, which made a third agent on the canvas an error
    rather than a third participant.
    """
    nodes: dict[str, dict[str, Any]] = {str(n["id"]): n for n in graph.get("nodes") or [] if n.get("id")}
    node = nodes.get(node_id)
    if node is None:
        raise ProtocolValidationError(f"No such node: {node_id!r}")
    if node.get("type") != "agent":
        raise ProtocolValidationError("Only Agent nodes can start a conversation.")

    peers = _connected_agent_ids(graph, node_id)
    if not peers:
        raise ProtocolValidationError(
            f"{_node_display_name(node)!r} isn't connected to another agent, so it has nobody to talk to. "
            "Draw an edge between two Agent nodes first."
        )
    # The entry agent and every peer it may consult: each needs its own model,
    # or the consultation fails partway through a run the user already paid for.
    for participant_id in [node_id, *peers]:
        participant = nodes[participant_id]
        model_edges = _edges_with_handle(graph, participant_id, "model", direction="incoming")
        if len(model_edges) != 1:
            raise ProtocolValidationError(
                f"Node {_node_display_name(participant)!r} must have exactly one Model connection "
                f"(found {len(model_edges)})."
            )
        model_source = nodes.get(str(model_edges[0]["source"]))
        if model_source is None or model_source.get("type") not in _MODEL_NODE_TYPES:
            raise ProtocolValidationError(
                f"Node {_node_display_name(participant)!r}'s Model connection must come from a Model node."
            )
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
    async with get_session() as db:
        experiment = await get_experiment(db, experiment_id) if experiment_id else None
    single_design_spec = experiment.design_spec if experiment is not None else None
    ambient_meta, node_dataset = await _node_run_context(
        graph,
        node["id"],
        workspace_id,
        owner_id,
        protocol_run_id=protocol_run_id,
        stage_plan=stage_plan_spec(single_design_spec, graph=graph),
    )
    user_input = _build_user_input(
        node,
        graph,
        {},
        experiment_id=experiment_id,
        effective_cell_label=effective_cell_label,
        script_bound="script_paths" in ambient_meta,
        seeded_datasets=node_dataset.seeded,
        unsplit_dataset=node_dataset.unsplit_name,
    )
    output_text, error, run_id, extraction = await _run_agent_node(
        node,
        protocol_id=protocol_id,
        protocol_run_id=protocol_run_id,
        owner_id=owner_id,
        user_input=user_input,
        graph=graph,
        workspace_id=workspace_id,
        ambient_meta=ambient_meta,
        unsplit_dataset=node_dataset.unsplit_name,
    )
    node_run: dict[str, Any] = {
        "status": "failed" if error else "completed",
        "output_text": output_text,
        "error": error,
        "run_id": str(run_id) if run_id else None,
    }
    node_run.update(extraction or {})
    async with get_session() as db:
        await update_node_run(db, protocol_run_id, node_id, node_run)
        if error:
            await set_status(db, protocol_run_id, status="failed", error=error)
        else:
            await set_status(db, protocol_run_id, status="finalizing")
            if experiment_id is not None:
                await finalize_attempt_measurement(db, protocol_run_id)
            await set_status(db, protocol_run_id, status="completed")


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
        if run.protocol_revision_id is not None:
            revision = await get_revision(db, run.protocol_revision_id)
            if revision is None:
                await set_status(
                    db, protocol_run_id, status="failed", error="published protocol revision no longer exists"
                )
                return
            graph = revision.graph
        experiment_id, replicate_label, factor_values = (
            protocol.experiment_id,
            run.replicate_label,
            run.factor_values,
        )
        # Pinned when the run was planned, so a design regenerated while this
        # was in flight can't redirect the write-backs below onto a different
        # generation's cell (or mint a stray one for a combination the current
        # design no longer has). Null for a run planned before this column
        # existed -- upsert_replicate then falls back to the current revision,
        # which is the old behavior and the best available answer.
        design_revision_id = run.design_revision_id
        target_node_id = run.target_node_id
        experiment = await get_experiment(db, experiment_id) if experiment_id else None
        design_spec = experiment.design_spec if experiment is not None else None
        # The stage plan comes from the PINNED revision's snapshot, not from the
        # live design_spec: a plan edit made while this replicate was queued
        # would otherwise stage a cell through a pipeline its own design never
        # declared. Everything else here still reads the live spec, which is the
        # pre-existing behaviour; the plan is singled out because it is the one
        # design field that writes durable, versioned artifacts to disk.
        pinned_spec = design_spec
        if design_revision_id is not None:
            pinned = await get_design_revision(db, design_revision_id)
            if pinned is not None and pinned.design_spec is not None:
                pinned_spec = pinned.design_spec

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
        validate_coordination_strategy(design_spec, graph=graph)
        validate_stage_plan(design_spec)
        # After apply_factor_bindings above, so a reference that arrived as a
        # factor level is checked as the text the agent will actually get.
        validate_prompt_references(graph=graph)
        order = topological_order(graph, require_acyclic=not is_conversation_strategy(design_spec))
        gated_by = find_gated_pairs(graph)
    except ProtocolValidationError as e:
        async with get_session() as db:
            await set_status(db, protocol_run_id, status="failed", error=str(e))
        return

    effective_cell_label = _effective_cell_label(replicate_label, protocol_run_id)
    workspace_id = _compute_workspace_id(experiment_id, replicate_label, protocol_run_id)
    # Derived from the *pinned* graph, not the live canvas: a canvas edit
    # mid-run must not change which stages this run's later nodes are staging
    # through.
    stage_plan = stage_plan_spec(pinned_spec, graph=graph)

    async with get_session() as db:
        await set_status(db, protocol_run_id, status="running")
        if replicate_label and experiment_id and await is_current_replicate_attempt(db, protocol_run_id):
            # Pre-write, before any node executes: a crash/timeout mid-run
            # still leaves this cell's provenance recorded (mirrors the
            # notebook's own pre-scoring upsert_replicate call). workspace_id is
            # already computed above -- FactorialReplicateResult.workspace_id
            # existed for this before anything ever populated it.
            await upsert_replicate(
                db,
                experiment_id=experiment_id,
                replicate_label=replicate_label,
                fields={"run_id": protocol_run_id, "factor_values": factor_values or {}, "workspace_id": workspace_id},
                revision_id=design_revision_id,
            )

    node_runs: dict[str, Any] = {}
    failed = False
    cancelled = False
    # The node whose output_text becomes this cell's result. For a pipeline
    # that's the graph's single sink; a conversation has no sink to speak of,
    # so it's the agent that was asked -- the one that writes the final answer.
    result_node_id: str | None = None
    failure_status, failure_error = "failed", "one or more nodes failed"

    if coordination_strategy_slug(design_spec) == "peer_collaboration":
        # Imported here, not at module scope: agent_messenger imports *this*
        # module, and that direction is what keeps a pipeline run structurally
        # unable to know conversations exist.
        from asaree.services.agent_messenger import execute_conversation

        entry_agent_id = resolve_conversation_entry_id(graph)  # already validated above
        entry_node = next(n for n in graph["nodes"] if str(n.get("id")) == entry_agent_id)
        ambient_meta, entry_dataset = await _node_run_context(
            graph,
            entry_agent_id,
            workspace_id,
            owner_id,
            protocol_run_id=protocol_run_id,
            stage_plan=stage_plan,
        )
        node_run, conversation_status = await execute_conversation(
            protocol_run_id,
            protocol_id=protocol_id,
            owner_id=owner_id,
            graph=graph,
            entry_agent_id=entry_agent_id,
            # The entry agent's own prompt, with this cell's factor values
            # already substituted in -- the task, not a chat message. There is
            # no upstream to fold in: in a conversation everything the other
            # agents contribute arrives as a reply, not as a prior node's output.
            user_input=_build_user_input(
                entry_node,
                graph,
                {},
                experiment_id=experiment_id,
                effective_cell_label=effective_cell_label,
                script_bound="script_paths" in ambient_meta,
                seeded_datasets=entry_dataset.seeded,
                unsplit_dataset=entry_dataset.unsplit_name,
            ),
            workspace_id=workspace_id,
            ambient_meta=ambient_meta,
            stage_plan=stage_plan,
            unsplit_dataset=entry_dataset.unsplit_name,
        )
        node_runs[entry_agent_id] = node_run
        cancelled = conversation_status == "cancelled"
        failed = conversation_status in ("failed", "limit_reached")
        if failed:
            failure_status = conversation_status
            failure_error = node_run["error"] or failure_error
        result_node_id = entry_agent_id
        # One conversation replaces the whole DAG walk, so there are no pipeline
        # nodes left to step through. Everything below the loop -- result
        # write-back, metric promotion, terminal status -- is shared and runs
        # either way.
        order = []
    elif coordination_strategy_slug(design_spec) == "supervisor_architecture":
        from asaree.services.agent_messenger import execute_supervisor_architecture

        roles = resolve_supervisor_roles(graph)  # already validated above
        supervisor_node = next(n for n in graph["nodes"] if str(n.get("id")) == roles.supervisor)
        # Only the seed prompt and the cell's factor values -- the supervisor's
        # own Dataset/Script cues are rebuilt inside each of its two turns,
        # which is where the slot keys it will actually be given are known.
        ambient_meta, supervisor_dataset = await _node_run_context(
            graph,
            roles.supervisor,
            workspace_id,
            owner_id,
            protocol_run_id=protocol_run_id,
            stage_plan=stage_plan,
        )
        node_run, supervisor_status = await execute_supervisor_architecture(
            protocol_run_id,
            protocol_id=protocol_id,
            owner_id=owner_id,
            graph=graph,
            roles=roles,
            user_input=_build_user_input(
                supervisor_node,
                graph,
                {},
                experiment_id=experiment_id,
                effective_cell_label=effective_cell_label,
                script_bound="script_paths" in ambient_meta,
                seeded_datasets=supervisor_dataset.seeded,
                unsplit_dataset=supervisor_dataset.unsplit_name,
            ),
            workspace_id=workspace_id,
            parallel_workers=_supervisor_workers_run_in_parallel(design_spec),
            experiment_id=experiment_id,
            effective_cell_label=effective_cell_label,
            stage_plan=stage_plan,
        )
        node_runs[roles.supervisor] = node_run
        cancelled = supervisor_status == "cancelled"
        failed = supervisor_status in ("failed", "limit_reached")
        if failed:
            failure_status = supervisor_status
            failure_error = node_run["error"] or failure_error
        # The supervisor holds the pen: it wrote the brief and it wrote the
        # answer, so its final turn is the cell's result.
        result_node_id = roles.supervisor
        order = []
    else:
        sinks = sink_node_ids(graph)
        result_node_id = sinks[0] if len(sinks) == 1 else None

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
            # _resolve_model_config/_resolve_tool_config). Memory and
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
                stage_plan=stage_plan,
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
        # What this node's Output Parser contributed, if one ran.
        extraction: dict[str, Any] | None
        # Which of this node's references resolved to nothing. Carried onto the
        # node run so the Runs tab can say so: an empty resolution leaves a
        # literal gap in the prompt, which reads as an agent that was simply
        # never told anything rather than one whose sender produced nothing.
        unresolved: list[str] = []
        if not _is_node_active(node):
            # Deactivated: skip this node's own logic entirely -- its
            # upstream input passes straight through as its output
            # unchanged (the standard node-disable semantic). Gated
            # workers can't reach here (topological_order already rejects
            # that combination), and pure config sources (including
            # mcp_tool) never reach this point at all, so this only ever
            # applies to a plain agent node.
            # No extraction: pass-through hands on the UPSTREAM node's prose,
            # which was read (if at all) against a different node's contract,
            # so claiming its payload as this node's own typed output would be
            # a lie about which contract produced it.
            output_text, error, run_id, extraction = (
                _upstream_output_text(graph, node_id, node_runs),
                None,
                None,
                None,
            )
        else:
            ambient_meta, node_dataset = await _node_run_context(
                graph,
                node_id,
                workspace_id,
                owner_id,
                protocol_run_id=protocol_run_id,
                stage_plan=stage_plan,
            )
            user_input = _build_user_input(
                node,
                graph,
                node_runs,
                experiment_id=experiment_id,
                effective_cell_label=effective_cell_label,
                script_bound="script_paths" in ambient_meta,
                seeded_datasets=node_dataset.seeded,
                unsplit_dataset=node_dataset.unsplit_name,
                unresolved_out=unresolved,
            )
            # Same `unresolved` list as the user prompt: a reference that
            # resolved to nothing left the same gap wherever it was written,
            # and the Runs tab reports the node, not the field.
            node_system_prompt = _build_system_prompt(
                node,
                graph,
                node_runs,
                unresolved_out=unresolved,
            )
            output_text, error, run_id, extraction = await _run_agent_node(
                node,
                protocol_id=protocol_id,
                protocol_run_id=protocol_run_id,
                owner_id=owner_id,
                user_input=user_input,
                graph=graph,
                system_prompt=node_system_prompt,
                workspace_id=workspace_id,
                ambient_meta=ambient_meta,
                unsplit_dataset=node_dataset.unsplit_name,
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
            # Alongside output_text, never instead of it: the extraction is an
            # extra, best-effort read of an answer that already exists. Merged
            # rather than assigned, so a key only appears when there is
            # something in it -- the same treatment unresolved_references gets
            # below.
            node_runs[node_id].update(extraction or {})
            if unresolved:
                node_runs[node_id]["unresolved_references"] = unresolved
            if error:
                failed = True
        async with get_session() as db:
            await update_node_run(db, protocol_run_id, node_id, node_runs[node_id])
    if coordination_strategy_slug(design_spec) == "sequential":
        # A chain's handoffs are agent-to-agent messages, so they get the same
        # transcript a conversation does. Best-effort: a transcript is a view of
        # a run that already happened, and failing to render it must not fail
        # the run.
        chain = sequential_chain_order(graph)
        if len(chain) >= 2:
            from asaree.services.agent_messenger import record_sequential_transcript

            head = next((n for n in graph["nodes"] if str(n.get("id")) == chain[0]), None)
            try:
                await record_sequential_transcript(
                    protocol_run_id,
                    protocol_id=protocol_id,
                    owner_id=owner_id,
                    graph=graph,
                    chain=chain,
                    node_runs=node_runs,
                    # The head's own configured prompt, not the full built
                    # user_input: the Dataset/Script cues are plumbing, and a
                    # transcript is meant to read as what was asked.
                    entry_prompt=_node_seed_prompt(head) if head else "",
                    state="canceled" if cancelled else ("failed" if failed else "completed"),
                )
            except Exception:
                logger.exception("sequential_transcript_failed", extra={"protocol_run_id": str(protocol_run_id)})

    async with get_session() as db:
        if cancelled:
            await set_status(db, protocol_run_id, status="cancelled")
        elif failed:
            # A conversation that ran out of budget gets its own terminal status
            # rather than being flattened into "failed" -- it's the one failure
            # mode the user fixes by raising a cap, not by fixing the protocol.
            await set_status(db, protocol_run_id, status=failure_status, error=failure_error)
        else:
            await set_status(db, protocol_run_id, status="finalizing")
            # Preserve the graph's designated output as a run artifact.
            # Metric observations are finalized below exclusively through
            # the attempt's explicit measurement-plan producer bindings.
            if (
                replicate_label
                and experiment_id
                and result_node_id is not None
                and node_runs.get(result_node_id, {}).get("status") == "completed"
                and await is_current_replicate_attempt(db, protocol_run_id)
            ):
                await upsert_replicate(
                    db,
                    experiment_id=experiment_id,
                    replicate_label=replicate_label,
                    fields={
                        "artifacts": {
                            "output_text": node_runs[result_node_id].get("output_text"),
                            "protocol_run_id": str(protocol_run_id),
                            # Why this replicate will come back unscored (see
                            # record_measurement_evaluation). Written here, on
                            # the same pass as output_text, so the marker
                            # exists even for a replicate whose measurement
                            # never runs -- otherwise "completed but unscored"
                            # would have nothing to explain itself with. Safe
                            # against a rerun: create_protocol_run clears
                            # `artifacts` when it claims the slot.
                            **(
                                {"truncation": truncation}
                                if (truncation := node_run_truncation(node_runs)) is not None
                                else {}
                            ),
                        }
                    },
                    revision_id=design_revision_id,
                )
    if not failed and not cancelled:
        async with get_session() as db:
            current = await get_protocol_run(db, protocol_run_id)
            if current is None or current.status in TERMINAL_PROTOCOL_RUN_STATUSES:
                return
            if experiment_id and (replicate_label or current.is_test_run or current.target_node_id):
                await finalize_attempt_measurement(db, protocol_run_id)
                await db.refresh(current)
                if current.status in TERMINAL_PROTOCOL_RUN_STATUSES:
                    return
                attempt_result = current.attempt_result or {}
                if attempt_result.get("evaluation_state") == "running" and attempt_result.get("measurement") is None:
                    # Another worker owns the durable at-most-once claim. It
                    # alone will publish the terminal evaluation outcome.
                    return
            await set_status(
                db,
                protocol_run_id,
                status="cancelled" if current.cancel_requested_at is not None else "completed",
            )
