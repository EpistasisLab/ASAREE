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
  data: AgentNodeData
}

export interface ProtocolEdge {
  id: string
  source: string
  target: string
  sourceHandle?: string | null
  targetHandle?: string | null
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

export interface AgentNodeConfig {
  name: string
  goal: string
  description: string
  system_prompt: string
  model_config_data: AgentModelConfigData
  pattern_config: AgentPatternConfig
  tool_config: AgentToolConfig
  output_contract: Record<string, unknown> | null
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
