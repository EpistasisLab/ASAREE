export interface Protocol {
  id: string
  name: string
  description: string | null
  experiment_id: string | null
  graph: ProtocolGraph
  created_at: string
  updated_at: string
}

export interface ProtocolGraph {
  nodes: ProtocolNode[]
  edges: ProtocolEdge[]
}

export interface ProtocolNode {
  id: string
  type: string
  position: { x: number; y: number }
  data:
    | AgentNodeData
    | McpToolNodeData
    | CriticGateNodeData
    | LlmNodeData
    | MemoryNodeData
    | DatasetNodeData
    | ScriptNodeData
    | ReasonActPatternNodeData
    | SingleAgentBaselinePatternNodeData
}

export interface ProtocolEdge {
  id: string
  source: string
  target: string
  sourceHandle?: string | null
  targetHandle?: string | null
}

export type NodeRunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'cancelled'

export interface NodeRunState {
  status: NodeRunStatus
  run_id?: string | null
  output_text?: string | null
  error?: string | null
  // Critic Gate only -- absent on a plain agent's NodeRunState. `run_id`
  // above doubles as the CRITIC's own run (not the upstream worker's) for a
  // gate, so its own Sense/Reason/Plan/Act steps are inspectable the same
  // way an agent's are. `approved` is null when the gate is disabled
  // (pass-through, no critic ever ran) or when `forced` is true (revisions
  // exhausted, no verdict on the final attempt); `feedback`/`rejection_scope`
  // carry the last verdict produced -- the one that approved it, or, when
  // `forced`, the rejection that led to the forced final attempt.
  approved?: boolean | null
  revisions_used?: number | null
  forced?: boolean
  feedback?: string | null
  rejection_scope?: string | null
}

export interface ProtocolRun {
  id: string
  protocol_id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  node_runs: Record<string, NodeRunState>
  error: string | null
  // Both null for a plain graph run. Set together only for a run created by
  // "run all cells" (POST /protocols/{id}/cell-runs) -- factor_values is the
  // cell's own factor_values, substituted into the graph's factor-bound
  // fields before execution; cell_label is which FactorialCellResult the
  // result gets written back to.
  cell_label: string | null
  factor_values: Record<string, unknown> | null
  // Set only for a per-node "Play" run (POST /protocols/{id}/nodes/{nodeId}/run)
  // -- null for both a plain graph run and a "run all cells" run.
  target_node_id: string | null
  // Set by the Stop button (POST /protocols/{id}/runs/{runId}/cancel) --
  // present but status still "running" means the request has been raised
  // but not yet honored (services.protocol_execution.run_protocol only
  // polls this between nodes, so whatever's currently in flight finishes
  // first). Once honored, status flips straight to "cancelled".
  cancel_requested_at: string | null
  created_at: string
  updated_at: string
}

// One "run all cells" trigger fans out into these -- one ProtocolRun per
// not-yet-scored cell. skipped is how many cells already had metric_values
// and were left alone (resume semantics, same as generate-design's own
// idempotency).
export interface CellRunBatch {
  protocol_run_ids: string[]
  cell_labels: string[]
  skipped: number
}

// Mirrors CreateAgentRequest (src/asaree/api/agents.py) field-for-field so a
// later execution phase can hand `config` straight to client.agents.create(...)
// with zero remapping.
export interface AgentModelConfigData {
  provider: string
  model: string
  temperature?: number | null
  effort?: string | null
  // Nullable so LlmNodeInspector's Input can be backspaced to empty without
  // snapping to a forced value -- null is a real, persisted "not set yet"
  // state flagged by LlmNode's warning triangle and nodeConfigIssues.ts's
  // pre-flight scan, same convention as ReasonActPatternConfig's fields.
  max_tokens: number | null
}

// Mirrors Motoro's output_contract field-spec exactly: {"name":str,
// "fields":[{"name","type","description"?,"default"?}]}. `type`/`default`
// are free-form strings here (not a closed enum) -- the field-builder UI
// offers a curated set of common JSON-ish types, but round-trips whatever a
// hand-edited value already has instead of clobbering it.
export interface OutputContractField {
  name: string
  type: string
  description?: string
  default?: string
}

export interface OutputContract {
  name: string
  fields: OutputContractField[]
}

export interface AgentNodeConfig {
  // The task/message for a specific run -- optional; falls back to `goal`
  // (services.protocol_execution's _build_user_input) when blank. This is the
  // per-invocation user message -- the one thing meant to change between runs,
  // distinct from `goal` (a persistent objective) and `system_prompt`
  // (persistent behavioral instructions).
  prompt: string
  goal: string
  description: string
  system_prompt: string
  // Model, tool assignment, and execution pattern are no longer fields
  // here -- resolved from the node's required LLM connector, optional Tool
  // connector(s), and optional Architectural Pattern connector instead (see
  // LlmNodeData/McpToolNodeData/ReasonActPatternNodeData and
  // services.protocol_execution's _resolve_llm_config/_resolve_tool_config/
  // _resolve_pattern_config) -- deliberately kept out of a node's own
  // settings.
  output_contract: OutputContract | null
  budget_limit_usd: number | null
  max_run_duration_seconds: number | null
}

export interface AgentNodeData {
  label: string
  config: AgentNodeConfig
  // field path (e.g. "model_config_data.temperature") -> factor name, for
  // fields turned into experimental factors via "+ Make experimental
  // factor". The factor itself lives on the linked experiment's own
  // design_spec.factors -- this is only the node-side half of the binding.
  factor_bindings?: Record<string, string>
  // Absent/undefined means active -- every graph saved before this field
  // existed is unaffected. A deactivated node's own logic is skipped
  // entirely by the executor; its upstream input passes straight through
  // as its own output unchanged (services.protocol_execution's
  // _upstream_output_text). Toggled via the canvas's per-node hover
  // toolbar, not exposed in the inspector.
  active?: boolean
  [key: string]: unknown
}

export function defaultAgentNodeData(label = 'Agent'): AgentNodeData {
  return {
    label,
    config: {
      prompt: '',
      goal: '',
      description: '',
      system_prompt: '',
      output_contract: null,
      budget_limit_usd: null,
      max_run_duration_seconds: null,
    },
  }
}

// An "MCP Tool" node represents one connection to a registered MCP server,
// with an allow-list of that server's tools (tool_names, plural) -- one node
// per server with a tools filter inside it, matching Motoro's real
// allow-list primitive (RunContext.available_tools/_tool_in_allowlist), not
// "one node per tool." Always an Agent's Tool-
// connector source -- never a standalone pipeline step (there's no single
// well-defined action for "run all of these tools" with no agent). Rendered
// via CircleNode, same as Llm/Memory/pattern nodes -- a pure config source,
// never its own execution turn (services.protocol_execution's
// _PURE_CONFIG_SOURCE_TYPES). server_name is carried alongside server_id
// purely for display -- the NAME is what Tool-connector resolution
// (_resolve_tool_config) keys off of (matches the `server_names` field name
// AgentToolConfig always had).
export interface McpToolNodeConfig {
  server_id: string | null
  server_name: string | null
  tool_names: string[]
  // Absent means enabled, matching `active`'s own convention (AgentNodeData)
  // -- lets a Tool factor's levels be a plain boolean (this server on/off)
  // as well as an entirely different server (see bindableFields.ts).
  // services.protocol_execution's _resolve_tool_config skips a disabled
  // tool node's contribution entirely.
  enabled?: boolean
}

export interface McpToolNodeData {
  label: string
  config: McpToolNodeConfig
  factor_bindings?: Record<string, string>
  [key: string]: unknown
}

export function defaultMcpToolNodeData(label = 'MCP Tool'): McpToolNodeData {
  return { label, config: { server_id: null, server_name: null, tool_names: [], enabled: true } }
}

// A "Critic Gate" reviews its single upstream Agent node's output and can
// request revisions -- generalizes the notebook's run_stage revision loop
// (src/asaree/services/protocol_execution.py's _run_gated_worker). No
// tool_config/pattern_config/output_contract here: the critic never gets
// tools, always runs single-pass, and its output_contract is hardcoded by
// the executor (CRITIC_OUTPUT_CONTRACT) so it can always trust the verdict's
// field names -- none of that is user-configurable.
export interface CriticGateNodeConfig {
  name: string
  goal: string
  description: string
  system_prompt: string
  // Resolved from the gate's required LLM connector instead, same as an
  // agent node -- see AgentNodeConfig's own comment.
  // Critic gates have no separate top-level `active` flag the way
  // AgentNodeData/McpToolNodeData do -- this field already means exactly
  // that for the review step specifically ("off" = the worker's output
  // passes straight through, no review), so the canvas's hover power icon
  // toggles this same field rather than introducing a redundant one.
  enabled: boolean
  max_revisions: number
}

export interface CriticGateNodeData {
  label: string
  config: CriticGateNodeConfig
  factor_bindings?: Record<string, string>
  [key: string]: unknown
}

export function defaultCriticGateNodeData(label = 'Critic Gate'): CriticGateNodeData {
  return {
    label,
    config: {
      name: label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'critic-gate',
      goal: 'Review the given output and return an approval verdict with feedback.',
      description: '',
      system_prompt: '',
      enabled: true,
      max_revisions: 1, // matches the notebook's own MAX_REVISIONS
    },
  }
}

// The LLM connector's node family -- named to match this app's existing
// LLMProvider/LLMSetting vocabulary. One node type per provider
// (llm_anthropic/llm_openai/llm_azure_foundry), not one generic node with a
// Provider field -- a dedicated node per capability rather than one node with
// an internal picker. Config shape is identical across all three (provider is
// baked into which node type you
// picked, not user-editable), so they share this one config/data shape and
// -- see LlmNodeInspector.tsx -- one inspector component, varying only the
// hardcoded `provider` each default-data factory below sets. Supplies
// model/provider/temperature/effort/max_tokens to whichever agent/
// critic_gate node(s) it's wired into via their required LLM connector.
// One LLM node's output can fan out to multiple agents (shared config,
// reused rather than re-entered per agent) -- nothing prevents this, though
// there's no dedicated UI for it yet.
export type LlmNodeConfig = AgentModelConfigData

export interface LlmNodeData {
  label: string
  config: LlmNodeConfig
  factor_bindings?: Record<string, string>
  [key: string]: unknown
}

// 128000 (not Motoro's own smaller schema default) matches
// WORKER_MAX_TOKENS in the real spinal-fusion notebook
// (asaree-spinal-use-case/spinal_pipeline.ipynb) -- used uniformly across
// every one of its agents/critics, well under Motoro's own
// ModelConfig cap of 200000.
export function defaultAnthropicLlmNodeData(label = 'Anthropic'): LlmNodeData {
  return { label, config: { provider: 'anthropic', model: 'claude-sonnet-5', temperature: 0.7, max_tokens: 128000 } }
}

export function defaultOpenAiLlmNodeData(label = 'OpenAI'): LlmNodeData {
  return { label, config: { provider: 'openai', model: 'gpt-5', temperature: 0.7, max_tokens: 128000 } }
}

export function defaultAzureFoundryLlmNodeData(label = 'Azure AI Foundry'): LlmNodeData {
  return { label, config: { provider: 'azure_foundry', model: 'gpt-5', temperature: 0.7, max_tokens: 128000 } }
}

// A "Memory" node -- visual/validation scaffolding only for now. Wiring one
// into an Agent's Memory connector is accepted by the graph (validated the
// same way LLM/Tool connectors are) but has NO effect on execution yet --
// porting Motoro's actual episodic-memory service (already built,
// Postgres+pgvector-backed, just not yet invoked anywhere in ASAREE's own
// execution path) is an explicit, deliberate follow-up, not this phase.
export interface MemoryNodeConfig {
  name: string
  // Absent means enabled, matching `active`'s own convention (AgentNodeData)
  // -- lets Memory be bound as a plain boolean factor. No runtime effect yet
  // (Memory execution isn't implemented at all), same "framework now,
  // backing later" status as the rest of this node type.
  enabled?: boolean
}

export interface MemoryNodeData {
  label: string
  config: MemoryNodeConfig
  factor_bindings?: Record<string, string>
  [key: string]: unknown
}

export function defaultMemoryNodeData(label = 'Memory'): MemoryNodeData {
  return {
    label,
    config: {
      name: label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'memory',
      enabled: true,
    },
  }
}

// A "Dataset" node -- declares which registered dataset an Agent's
// workspace tools (e.g. a domain MCP server's open_workspace) operate on.
// Unlike Memory/Architectural Pattern, this DOES have a real runtime effect
// once wired: services.protocol_execution's _build_user_input folds a
// "Dataset context" block (naming dataset_name plus this run's own
// experiment_id/cell_label) into the wired agent's own instruction, since
// open_workspace is the one workspace tool with no ambient _meta fallback.
// dataset_id/dataset_name mirrors McpToolNodeConfig's own server_id/
// server_name pairing -- picked from the caller's own registered datasets
// (GET /datasets), never uploaded/ingested through this node itself.
export interface DatasetNodeConfig {
  dataset_id: string | null
  dataset_name: string | null
  // Absent means enabled, matching every other connector's own convention.
  enabled?: boolean
}

export interface DatasetNodeData {
  label: string
  config: DatasetNodeConfig
  factor_bindings?: Record<string, string>
  [key: string]: unknown
}

export function defaultDatasetNodeData(label = 'Dataset'): DatasetNodeData {
  return { label, config: { dataset_id: null, dataset_name: null, enabled: true } }
}

// A "Script" node -- carries a fixed piece of code an Agent passes verbatim
// as some tool's own code-shaped argument (e.g. a domain MCP server's
// run_model_script's `code`). Not executed by ASAREE itself -- same "pure
// config source" status as every other connector; _build_user_input folds
// the code, fenced, into the wired agent's own instruction. Python-only for
// now (language is fixed, not a picker) -- matches the one real use case in
// evidence (a fixed XGBoost+Optuna scoring script whose only per-cell
// variation is the hyperparameters an upstream agent proposes, not the code
// itself).
export interface ScriptNodeConfig {
  name: string
  language: 'python'
  code: string
}

export interface ScriptNodeData {
  label: string
  config: ScriptNodeConfig
  factor_bindings?: Record<string, string>
  [key: string]: unknown
}

export function defaultScriptNodeData(label = 'Script'): ScriptNodeData {
  return { label, config: { name: 'script', language: 'python', code: '' } }
}

// The Architectural Pattern connector's node family -- UNLIKE Memory (see
// MemoryNodeData's own comment), wiring one into an Agent's Architectural
// Pattern connector has a real effect on execution:
// services.protocol_execution's _resolve_pattern_config reads the wired
// node's own config into a real Motoro PatternConfig, passed straight
// into create_agent/update_agent. ASAREE-specific, alongside
// LLM/Tool/Memory.
//
// One node type per pattern (pattern_reason_act/pattern_single_agent_baseline),
// not one generic node with a Pattern-name field -- same reasoning as the
// LLM node family above, and unlike that family these genuinely have
// different config shapes (Motoro's own `pattern_params` schema per
// plugin, see engine/patterns/builtin/*.py), so each gets its own dedicated
// inspector rather than sharing one. `PatternConfig` (Motoro) already
// has unused slots for safety_patterns/coordination_pattern/
// knowledge_patterns/quality_patterns/routing_pattern/resolution_patterns --
// no builtin plugins exist for those yet, but that's exactly where more
// node types land as Motoro grows them, same connector, same
// per-pattern-node convention.

// Mirrors Motoro's ReasonActPattern.configuration_schema
// (engine/patterns/builtin/reason_act.py) -- a native tool-calling loop:
// each iteration either calls a tool or calls `final_answer`, repeating
// until `final_answer` fires or max_iterations is hit.
export interface ReasonActPatternConfig {
  // Nullable so the inspector's Input can be backspaced to empty without
  // snapping to 0 (Number('') === 0) -- null is a real, persisted "not set
  // yet" state flagged by ReasonActPatternNode's warning triangle and
  // nodeConfigIssues.ts's pre-flight scan, rather than being silently
  // coerced into a runnable-but-wrong value.
  max_iterations: number | null
  include_scratchpad: boolean
  scratchpad_window: number | null
  observation_format: 'raw' | 'summarized'
}

export interface ReasonActPatternNodeData {
  label: string
  config: ReasonActPatternConfig
  factor_bindings?: Record<string, string>
  [key: string]: unknown
}

export function defaultReasonActPatternNodeData(label = 'Reason + Act'): ReasonActPatternNodeData {
  return {
    label,
    config: { max_iterations: 15, include_scratchpad: true, scratchpad_window: 10, observation_format: 'raw' },
  }
}

// Mirrors Motoro's SingleAgentBaselinePattern.configuration_schema
// (engine/patterns/builtin/single_agent_baseline.py) -- the plain,
// unmodified Sense->Reason->Plan->Act loop with no tool-call interleaving;
// Motoro's own default/fallback when no pattern_config is set at all.
export interface SingleAgentBaselinePatternConfig {
  max_iterations: number
  stop_on_first_success: boolean
}

export interface SingleAgentBaselinePatternNodeData {
  label: string
  config: SingleAgentBaselinePatternConfig
  factor_bindings?: Record<string, string>
  [key: string]: unknown
}

export function defaultSingleAgentBaselinePatternNodeData(label = 'Single-Agent Baseline'): SingleAgentBaselinePatternNodeData {
  return { label, config: { max_iterations: 10, stop_on_first_success: true } }
}
