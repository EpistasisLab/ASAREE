export interface AgentModelConfig {
  provider?: string | null
  model?: string | null
  effort?: string | null
}

export interface Agent {
  id: string
  name: string
  description: string
  goal: string
  model_config: AgentModelConfig
  is_system: boolean
  created_at: string
}
