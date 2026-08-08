export interface Run {
  id: string
  agent_id: string
  status: string
  run_metadata: Record<string, unknown> | null
  created_at: string
}
