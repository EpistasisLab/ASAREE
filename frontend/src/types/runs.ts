export interface Run {
  id: string
  agent_id: string
  status: string
  run_metadata: Record<string, unknown> | null
  created_at: string
}

// GET /runs/{id}/steps -- Motoro's own Sense/Reason/Plan/Act(/HITL)
// loop trace, persisted per-run (motoro.models.run.RunStep), not
// reconstructed from logs. One row per pattern-loop step.
export interface RunStep {
  id: string
  sequence: number
  iteration: number
  phase: string
  input: unknown
  output: unknown
  llm_call: Record<string, unknown> | null
  tool_call: Record<string, unknown> | null
  started_at: string | null
  completed_at: string | null
}
