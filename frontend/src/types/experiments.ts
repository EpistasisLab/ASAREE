export interface DesignFactor {
  name: string
  levels: unknown[]
  // Absent means 'string' -- factors created before this field existed keep
  // working unchanged. Drives which control the factor editor renders per
  // level (see components/protocol/factorLevels.ts); purely a frontend/UX
  // concern, not enforced by the backend (design_spec is opaque JSONB).
  level_type?: 'string' | 'text' | 'number' | 'boolean' | 'llm_config' | 'tool_config' | 'pattern' | 'script_config'
}

export interface DesignMetric {
  name: string
  primary: boolean
  direction: 'maximize' | 'minimize'
}

// "sequential" (default when design_spec.coordination_strategy is absent --
// today's exact existing DAG-handoff behavior) and "critic_gate" (promotes
// the existing gated-pair mechanism to an explicit declaration) are real.
// The rest are named placeholders for ARES's own coordination-category
// patterns, pending a later ARES -> agentic-core migration -- selectable and
// saveable, but services.protocol_execution rejects a run attempted with one
// of these active. See COORDINATION_STRATEGY_CATALOG for display metadata.
export type CoordinationStrategySlug =
  | 'sequential'
  | 'critic_gate'
  | 'supervisor_architecture'
  | 'swarm_architecture'
  | 'task_bidding'
  | 'supervision_tree_with_guarded_capabilities'
  | 'event_driven_reactivity'
  | 'multi_agent_planning'

export interface CoordinationStrategyConfig {
  slug: CoordinationStrategySlug
  params?: Record<string, unknown>
}

export const COORDINATION_STRATEGY_CATALOG: {
  slug: CoordinationStrategySlug
  label: string
  description: string
  implemented: boolean
}[] = [
  {
    slug: 'sequential',
    label: 'Sequential (default)',
    description: "Each agent's output becomes the next agent's input, following the canvas's own edges in order.",
    implemented: true,
  },
  {
    slug: 'critic_gate',
    label: 'Critic Gate',
    description: 'A reviewer agent approves or requests revisions at a fixed point in the sequential pipeline.',
    implemented: true,
  },
  {
    slug: 'supervisor_architecture',
    label: 'Supervisor',
    description: 'One coordinator delegates sub-tasks to worker agents and aggregates their results.',
    implemented: false,
  },
  {
    slug: 'swarm_architecture',
    label: 'Swarm',
    description: 'Agents self-organize around a shared task board -- no fixed coordinator.',
    implemented: false,
  },
  {
    slug: 'task_bidding',
    label: 'Task Bidding',
    description: 'Agents competitively bid for tasks; the best-scoring bid is awarded the work.',
    implemented: false,
  },
  {
    slug: 'supervision_tree_with_guarded_capabilities',
    label: 'Supervision Tree',
    description: 'A hierarchical tree of agents with capability-scoped subtrees and structured failure recovery.',
    implemented: false,
  },
  {
    slug: 'event_driven_reactivity',
    label: 'Event-Driven',
    description: 'Agents react to published events on shared topics instead of a fixed plan.',
    implemented: false,
  },
  {
    slug: 'multi_agent_planning',
    label: 'Multi-Agent Planning',
    description: 'Multiple planner agents propose in parallel; a coordinator merges them into one plan for workers.',
    implemented: false,
  },
]

export interface DesignSpec {
  factors?: DesignFactor[]
  // Copies per factor-level combination (default 1 when absent).
  replicates?: number
  // Shuffles generated cells' execution order when set (never affects which
  // combinations/replicates are generated).
  randomization_seed?: number | null
  // Declared up front, unlike the Results tab's purely-inferred metric keys
  // -- lets the Design tab show "Metrics" before any cell has run.
  metrics?: DesignMetric[]
  coordination_strategy?: CoordinationStrategyConfig
  [key: string]: unknown
}

export interface Experiment {
  id: string
  name: string
  description: string | null
  hypothesis: string | null
  design_type: string
  task_brief: Record<string, unknown> | null
  design_spec: DesignSpec | null
  dataset_id: string | null
  created_at: string
  archived_at: string | null
}

// One row of the Runs tab's trial list -- "trial" means cell (a
// factor-level combination x replicate), not ProtocolRun; a cell that's
// never been run at all is still a trial, reported with status "queued".
// Matches src/asaree/api/experiments.py's TrialResponse exactly.
export interface Trial {
  cell_label: string
  factor_values: Record<string, unknown>
  metric_values: Record<string, unknown>
  status: 'queued' | 'running' | 'completed' | 'failed'
  run_id: string | null
  error: string | null
  updated_at: string
}

// One row of analysis["emm_cells"] -- one factor-level combination's
// estimated marginal mean, with its own CI (the Results tab's "uncertainty"
// ask). _condition_label matches services.design_generation.cell_label_for's
// own format (e.g. "tier_large"), computed fresh from condition_factors, not
// stored anywhere.
export interface EmmCell {
  _condition_label: string
  mean: number
  std: number
  count: number
  se: number
  ci_lo: number
  ci_hi: number
}

// One row of analysis["factorial_effects"] -- "effect" has no ":" for a
// single factor's main effect, one or more ":"-joined factor names for an
// interaction (see services.factorial_analysis._design_matrix's own
// ":".join(...) term naming).
export interface FactorialEffect {
  effect: string
  estimate_half_diff: number
  t: number
  p_perm: number
  p_maxstat_fwer: number
  mc_se_p: number
}

export interface NonInferiorityRow {
  condition: string
  contrast_vs_reference: number
  lower_bound: number
  neg_delta: number
  p_one_sided: number
  p_holm?: number
  ni_decision?: string
  [key: string]: unknown
}

// The full services.factorial_analysis.analyze_factorial return shape --
// deliberately loose (an index signature, not every key modeled) since this
// is a dict[str, Any] on the backend too, not a typed Pydantic model.
export interface ExperimentAnalysis {
  n_attempted: number
  n_scored: number
  n_failed: number
  n_not_yet_run: number
  emm_cells: EmmCell[]
  factorial_effects: FactorialEffect[]
  non_inferiority: NonInferiorityRow[]
  ni_reportable: boolean
  metric_summary: Record<string, unknown>[]
  cost_time_summary: Record<string, unknown>[]
  footer: { primary_metric: string; condition_factors: string[]; [key: string]: unknown }
  [key: string]: unknown
}

// GET /experiments/{id}/results -- available is false (with a human-
// readable reason) whenever there's nothing to show yet: no factors/primary
// metric declared, a factor with other than 2 levels, or not enough scored
// replicates (see services.factorial_analysis.analyze_experiment_design).
export interface ExperimentResults {
  available: boolean
  reason: string | null
  analysis: ExperimentAnalysis | null
  best_condition: EmmCell | null
}

export interface Cell {
  id: string
  cell_label: string
  run_id: string | null
  workspace_id: string | null
  factor_values: Record<string, unknown> | null
  metric_values: Record<string, unknown> | null
  artifacts: Record<string, unknown> | null
  created_at: string
  updated_at: string
}
