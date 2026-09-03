export interface DesignFactor {
  name: string
  levels: unknown[]
  // Absent means 'string' -- factors created before this field existed keep
  // working unchanged. Drives which control the factor editor renders per
  // level (see components/protocol/factorLevels.ts); purely a frontend/UX
  // concern, not enforced by the backend (design_spec is opaque JSONB).
  level_type?:
    | 'string'
    | 'text'
    | 'number'
    | 'boolean'
    | 'llm_config'
    | 'tool_config'
    | 'pattern'
    | 'script_config'
    | 'dataset_config'
    | 'tool_names'
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
// patterns, pending a later ARES -> Motoro migration -- selectable and
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
  // Every dataset attached to this experiment, in canvas wiring order -- an
  // experiment can run against several since the Dataset connector was
  // uncapped. `dataset_id` is a read-only view of the first one, kept for
  // code written before that; it is no longer a stored column.
  dataset_ids: string[]
  dataset_id: string | null
  locked_at: string | null
  locked_protocol_revision_id: string | null
  created_at: string
  archived_at: string | null
}

// One row of the Runs tab's trial list -- one replicate, not ProtocolRun; a replicate that's
// never been run at all is still a trial, reported with status "not_started".
// Matches src/asaree/api/experiments.py's TrialResponse exactly.
export interface Trial {
  replicate_label: string
  factor_values: Record<string, unknown>
  metric_values: Record<string, unknown>
  status: 'not_started' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  run_id: string | null
  // True when this run used an older published canvas version than the
  // protocol's current published version.
  obsolete: boolean
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

// The general-purpose Results panel is intentionally not limited to a
// balanced factorial design. These records combine execution facts (status,
// duration, usage) with whatever numeric metrics the experiment produced.
export interface ResultNodeRun {
  node_id: string
  node_label: string
  status: string
  output_text: string | null
  error: string | null
  agent_run_id: string | null
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  cost_usd: number | null
}

export interface ResultReplicate {
  replicate_label: string
  replicate_number: number
  cell_label: string
  factor_values: Record<string, unknown>
  metric_values: Record<string, unknown>
  status: Trial['status']
  obsolete: boolean
  error: string | null
  run_id: string | null
  protocol_revision_id: string | null
  updated_at: string
  duration_seconds: number | null
  node_runs: ResultNodeRun[]
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  cost_usd: number | null
  agent_run_count: number
  reported_usage_count: number
  reported_cost_count: number
  obsolete_runs: ObsoleteRun[]
}

// Immutable ProtocolRun records that used an earlier canvas version. This
// includes the latest stored run when it has since become obsolete, as well as
// runs superseded by later attempts for the same replicate.
export interface ObsoleteRun {
  run_id: string
  status: Trial['status']
  obsolete: true
  error: string | null
  protocol_revision_id: string | null
  updated_at: string
  duration_seconds: number | null
  node_runs: ResultNodeRun[]
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  cost_usd: number | null
  agent_run_count: number
  reported_usage_count: number
  reported_cost_count: number
}

export interface ResultCell {
  cell_label: string
  factor_values: Record<string, unknown>
  replicate_count: number
  completed_count: number
  current_completed_count: number
  obsolete_count: number
  metric_means: Record<string, number>
  cost_usd: number | null
  total_tokens: number | null
  duration_seconds: number | null
}

export interface RunResultsOverview {
  total_replicates: number
  completed_replicates: number
  running_replicates: number
  queued_replicates: number
  failed_replicates: number
  not_started_replicates: number
  obsolete_replicates: number
  total_cost_usd: number | null
  total_input_tokens: number | null
  total_output_tokens: number | null
  total_tokens: number | null
  total_duration_seconds: number | null
  agent_run_count: number
  reported_usage_count: number
  reported_cost_count: number
}

export interface ExperimentRunResults {
  overview: RunResultsOverview
  metric_keys: string[]
  primary_metric: string | null
  primary_metric_direction: 'maximize' | 'minimize'
  cells: ResultCell[]
  replicates: ResultReplicate[]
}

export interface Replicate {
  // One independently runnable observation within the owning cell.
  id: string
  cell_id: string
  cell_label: string
  replicate_label: string
  replicate_number: number
  // Which generation of the design this observation was made under. Replicates
  // from a superseded revision stay in the database as history, so listReplicates
  // returns only the current revision's unless asked for another one.
  design_revision_id: string
  run_id: string | null
  workspace_id: string | null
  factor_values: Record<string, unknown> | null
  metric_values: Record<string, unknown> | null
  artifacts: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

// One generation of an experiment's design. Regenerating a design whose set
// of cells has changed supersedes the current revision and opens a new one,
// keeping the old cells (and anything scored in them) as history rather than
// deleting them -- `superseded_at === null` marks the one that is current.
export interface DesignRevision {
  id: string
  revision: number
  superseded_at: string | null
  design_spec: DesignSpec | null
  cell_count: number
  replicate_count: number
  scored_replicate_count: number
  created_at: string
}

export interface DesignImpact {
  has_generated_design: boolean
  regeneration_required: boolean
  current_cell_count: number
  proposed_cell_count: number
  added_cell_count: number
  retained_cell_count: number
  removed_cell_count: number
  current_replicate_count: number
  proposed_replicate_count: number
  added_replicate_count: number
  retained_replicate_count: number
  removed_replicate_count: number
}
