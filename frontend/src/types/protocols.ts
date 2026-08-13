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
  data: AgentNodeData | McpToolNodeData
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
  created_at: string
  updated_at: string
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

export interface AgentToolConfig {
  server_names: string[]
  tool_names: string[]
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
  model_config_data: AgentModelConfigData
  pattern_config: AgentPatternConfig
  tool_config: AgentToolConfig
  output_contract: OutputContract | null
  budget_limit_usd: number | null
  max_run_duration_seconds: number | null
}

export interface AgentNodeData {
  label: string
  config: AgentNodeConfig
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
      model_config_data: { provider: 'anthropic', model: 'claude-sonnet-5', temperature: 0.7, max_tokens: 4096 },
      pattern_config: { execution_pattern: 'reason_act', pattern_params: {} },
      tool_config: { server_names: [], tool_names: [] },
      output_contract: null,
      budget_limit_usd: null,
      max_run_duration_seconds: null,
    },
  }
}

// An "MCP Tool" node references one tool on one registered MCP server --
// resolved at execution time through agentic_core's mcp_service, the same
// way an Agent node's tool_config.tool_names does (no ASAREE-side tool
// registry to duplicate). server_name is carried alongside server_id purely
// for display -- the id is the only thing execution will ever key off of.
export interface McpToolNodeConfig {
  server_id: string | null
  server_name: string | null
  tool_name: string | null
}

export interface McpToolNodeData {
  label: string
  config: McpToolNodeConfig
  [key: string]: unknown
}

export function defaultMcpToolNodeData(label = 'MCP Tool'): McpToolNodeData {
  return { label, config: { server_id: null, server_name: null, tool_name: null } }
}
