export interface Run {
  id: string
  agent_id: string
  status: string
  // The exact user message this run was given. On a protocol run that is the
  // fully assembled prompt -- the agent's own ask with every reference already
  // resolved -- which is what makes a handoff verifiable from stored data
  // rather than from a token echoed through a prompt.
  input: string
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
