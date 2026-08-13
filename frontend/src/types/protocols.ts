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
  data: AgentNodeData | McpToolNodeData | CriticGateNodeData | LlmNodeData | MemoryNodeData
}

export interface ProtocolEdge {
  id: string
  source: string
  target: string
  sourceHandle?: string | null
  targetHandle?: string | null
}

export type NodeRunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'

export interface NodeRunState {
  status: NodeRunStatus
  run_id?: string | null
  output_text?: string | null
  error?: string | null
}

export interface ProtocolRun {
  id: string
  protocol_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  node_runs: Record<string, NodeRunState>
  error: string | null
  // Both null for a plain graph run. Set together only for a run created by
  // "run all cells" (POST /protocols/{id}/cell-runs) -- factor_values is the
  // cell's own factor_values, substituted into the graph's factor-bound
  // fields before execution; cell_label is which FactorialCellResult the
  // result gets written back to.
  cell_label: string | null
  factor_values: Record<string, unknown> | null
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
  max_tokens: number
}

export interface AgentPatternConfig {
  execution_pattern: 'reason_act' | 'single_agent_baseline'
  pattern_params?: Record<string, Record<string, unknown>>
}

// Mirrors agentic-core's output_contract field-spec exactly: {"name":str,
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
  name: string
  goal: string
  description: string
  system_prompt: string
  // Model and tool assignment are no longer fields here -- resolved from
  // the node's required LLM connector and optional Tool connector(s)
  // instead (see LlmNodeData/McpToolNodeData and
  // services.protocol_execution's _resolve_llm_config/_resolve_tool_config),
  // matching n8n's own choice to keep these out of a node's own settings.
  pattern_config: AgentPatternConfig
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

export function defaultAgentNodeData(label = 'New Agent'): AgentNodeData {
  return {
    label,
    config: {
      name: label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'agent',
      goal: '',
      description: '',
      system_prompt: '',
      pattern_config: { execution_pattern: 'reason_act', pattern_params: {} },
      output_contract: null,
      budget_limit_usd: null,
      max_run_duration_seconds: null,
    },
  }
}

// An "MCP Tool" node references one tool on one registered MCP server --
// resolved at execution time through agentic_core's mcp_service. Dual role:
// wired inline via a normal edge it's a standalone pipeline step (unchanged
// behavior); wired into an Agent's Tool connector it becomes one of that
// agent's own callable tools instead, and isn't executed as its own step
// (services.protocol_execution's _resolve_tool_config/_tool_source_node_ids).
// A node can only be used one way at a time -- add a second MCP Tool node
// for the other role. server_name is carried alongside server_id purely for
// display -- the id is what standalone execution keys off of, the NAME is
// what Tool-connector resolution keys off of (matches the `server_names`
// field name AgentToolConfig always had).
export interface McpToolNodeConfig {
  server_id: string | null
  server_name: string | null
  tool_name: string | null
}

export interface McpToolNodeData {
  label: string
  config: McpToolNodeConfig
  // Same passthrough-deactivate semantic as AgentNodeData.active.
  active?: boolean
  [key: string]: unknown
}

export function defaultMcpToolNodeData(label = 'MCP Tool'): McpToolNodeData {
  return { label, config: { server_id: null, server_name: null, tool_name: null } }
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

// An "LLM" node -- ASAREE's own name for n8n's "Chat Model" connector
// (matches this app's existing LLMProvider/LLMSetting vocabulary instead of
// copying n8n's term). Supplies model/provider/temperature/effort/max_tokens
// to whichever agent/critic_gate node(s) it's wired into via their required
// LLM connector -- exactly today's AgentModelConfigData shape, just moved
// out of the agent's own config into its own node so the agent's inspector
// stays about the agent's behavior, not its model. One LLM node's output can
// fan out to multiple agents (shared config, reused rather than
// re-entered per agent) -- nothing prevents this, though there's no
// dedicated UI for it yet.
export type LlmNodeConfig = AgentModelConfigData

export interface LlmNodeData {
  label: string
  config: LlmNodeConfig
  factor_bindings?: Record<string, string>
  [key: string]: unknown
}

export function defaultLlmNodeData(label = 'LLM'): LlmNodeData {
  return {
    label,
    config: { provider: 'anthropic', model: 'claude-sonnet-5', temperature: 0.7, max_tokens: 4096 },
  }
}

// A "Memory" node -- visual/validation scaffolding only for now. Wiring one
// into an Agent's Memory connector is accepted by the graph (validated the
// same way LLM/Tool connectors are) but has NO effect on execution yet --
// porting agentic-core's actual episodic-memory service (already built,
// Postgres+pgvector-backed, just not yet invoked anywhere in ASAREE's own
// execution path) is an explicit, deliberate follow-up, not this phase.
export interface MemoryNodeConfig {
  name: string
}

export interface MemoryNodeData {
  label: string
  config: MemoryNodeConfig
  [key: string]: unknown
}

export function defaultMemoryNodeData(label = 'Memory'): MemoryNodeData {
  return { label, config: { name: label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'memory' } }
}
