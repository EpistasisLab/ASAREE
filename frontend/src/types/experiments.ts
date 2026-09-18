export interface DesignFactor {
  name: string
  levels: unknown[]
  // Short, user-editable names for individual treatments. They are persisted
  // alongside raw levels and become categorical factor values in the Results
  // CSV, so a long system prompt never becomes CSV data outside its secure
  // execution record.
  level_labels?: string[]
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
  // Stable instance identity.  Catalog entries also retain their catalog key;
  // custom and legacy declarations intentionally have no catalog key.
  id?: string
  catalogKey?: string
  name: string
  description?: string
  kind?: 'runtime' | 'custom'
  valueType?: 'number' | 'boolean' | 'string' | 'opaque'
  unit?: string
  primary: boolean
  direction: 'maximize' | 'minimize' | 'neutral'
  aggregation?: 'mean' | 'sum' | 'none'
  [key: string]: unknown
}

export type MeasurementProducerKind = 'runtime' | 'reported'
export type MeasurementDirection = 'maximize' | 'minimize' | 'neutral'
export type MeasurementAggregation = 'mean' | 'sum' | 'rate' | 'pooled' | 'none'

export interface MeasurementPlan {
  metrics: Array<{
    id: string
    name: string
    value_type: 'number' | 'boolean' | 'opaque'
    direction: MeasurementDirection
    aggregation: MeasurementAggregation
    primary: boolean
    description?: string
    unit?: string
  }>
  producers: Array<{
    id: string
    producer_id: string
    kind: MeasurementProducerKind
    outputs: Record<string, string>
    artifacts: string[]
    config: Record<string, unknown>
  }>
  inputs: Array<{
    producer_binding_id: string
    input_key: string
    source_key: string
  }>
}

export interface MeasurementPlanValidationReport {
  valid: boolean
  issues: Array<{ code: string; message: string; path: string; blocking?: boolean }>
}

export interface MeasurementCapabilities {
  outputs: Record<string, string[]>
}

export type ObservationStatus =
  | 'measured'
  | 'unavailable'
  | 'failed'
  | 'timed_out'
  | 'cancelled'
  | 'not_applicable'

// Every slug here is implemented -- there is deliberately no "coming soon"
// entry. Six ARES coordination-category placeholders (supervisor, swarm, task
// bidding, supervision tree, event-driven, multi-agent planning) used to be
// listed and were removed: an option that always fails at run time is worse
// than an option that isn't offered. The backend still recognizes the five that
// remain unbuilt, so an experiment saved with one gets a real explanation, not
// "unknown" -- supervisor came back off that list once it was actually built.
export type CoordinationStrategySlug =
  | 'sequential'
  | 'critic_gate'
  | 'peer_collaboration'
  | 'supervisor_architecture'

export interface CoordinationStrategyConfig {
  slug: CoordinationStrategySlug
  params?: Record<string, unknown>
}

export const COORDINATION_STRATEGY_CATALOG: {
  slug: CoordinationStrategySlug
  label: string
  description: string
}[] = [
  {
    slug: 'sequential',
    label: 'Sequential (default)',
    description: "Each agent's output becomes the next agent's input, following the canvas's own edges in order.",
  },
  {
    slug: 'critic_gate',
    label: 'Critic Gate',
    description: 'A reviewer agent approves or requests revisions at a fixed point in the sequential pipeline.',
  },
  {
    slug: 'peer_collaboration',
    // "(beta)" rides on the label rather than a separate badge for the same
    // reason "(default)" does above: the label is what the picker, the
    // canvas-mismatch warning and the switch-confirm dialog all render, so
    // one string keeps the qualifier from appearing in only one of the three.
    label: 'Peer Collaboration (beta)',
    // The cost ceiling belongs here rather than in RunConfirmDialog: this is
    // where the choice is actually made, and every consultation is a full agent
    // run of its own (Reason/Plan/Act cycle, own tokens). The caps are the
    // messenger's -- services/agent_messenger.py.
    description:
      'Connected agents work the task together as a conversation -- the lead agent can consult its peers, and every reply is shared with everyone, instead of handing off once. Each consultation is a full agent run (up to 8, nested 2 deep), so a cell can cost several times a sequential one.',
  },
  {
    slug: 'supervisor_architecture',
    label: 'Supervisor (beta)',
    // The contrast with Peer Collaboration is the whole reason to pick one over
    // the other, so it's stated rather than left to be discovered at run time:
    // there, the lead *may* consult; here, ASAREE dispatches every worker
    // itself. The turn count is the topology's, which is why no cap is quoted.
    description:
      'One supervisor agent briefs the workers wired to it, they all run (in parallel by default), an optional reviewer assesses their output advisorily, and the supervisor writes the final answer. Every one of those turns is dispatched by ASAREE, so no worker can be skipped -- a cell costs one agent run per agent, plus a second for the supervisor.',
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
  // Which staged pipeline this experiment's dataset workspaces use. There is
  // deliberately no UI for this: a canvas-built experiment leaves it absent and
  // the backend derives the plan from which stage-writing MCP servers the
  // canvas wires. It stays declared here (untyped -- the shape is
  // asaree_workspace_core's, not the GUI's) purely so a spread that rebuilds
  // design_spec carries an SDK-declared plan through instead of erasing it.
  stage_plan?: unknown
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
  measurement_plan: MeasurementPlan | null
  metric_recommendations?: MetricRecommendationMetadata | null
  metric_recommendation_set_version?: number
  latest_test_run_id?: string | null
  // Every dataset attached to this experiment, in canvas wiring order -- an
  // experiment can run against several since the Dataset connector was
  // uncapped. `dataset_id` is a read-only view of the first one, kept for
  // code written before that; it is no longer a stored column.
  dataset_ids: string[]
  dataset_id: string | null
  locked_at: string | null
  locked_protocol_revision_id: string | null
  locked_design_spec: DesignSpec | null
  locked_measurement_plan: MeasurementPlan | null
  created_at: string
  updated_at: string
  archived_at: string | null
}

export interface MetricRecommendationMetadata {
  applied_version: number | null
  dismissed_version: number | null
  intentionally_removed_keys: string[]
  contextual_suggestion_dismissals?: Record<string, string>
}

// One row of the Runs tab's trial list -- one replicate, not ProtocolRun; a replicate that's
// never been run at all is still a trial, reported with status "not_started".
// Matches src/asaree/api/experiments.py's TrialResponse exactly.
export interface Trial {
  replicate_label: string
  factor_values: Record<string, unknown>
  metric_values: Record<string, unknown>
  status: 'not_started' | 'queued' | 'running' | 'finalizing' | 'completed' | 'failed' | 'cancelled'
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
  // Senders this node's prompt referenced that produced nothing, by label
  // (already resolved server-side -- see experiment_run_results). Empty in the
  // normal case. The assembled prompt just has a gap where their output should
  // be, so without this it reads as an agent that was never told anything.
  unresolved_reference_labels?: string[]
  agent_run_id: string | null
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  cost_usd: number | null
}

export interface MetricObservation {
  metric_id: string
  metric_name: string
  value_type: 'number' | 'boolean' | 'opaque'
  status: ObservationStatus
  value: unknown
  error: string | null
  attempt_id: string
  producer: {
    binding_id: string
    producer_id: string
    kind: MeasurementProducerKind | 'unbound'
    version: string
    binding_config?: Record<string, unknown>
  }
  input_provenance: Record<string, { source_key: string; value_type: string; provenance: Record<string, unknown> }>
}

export interface EvaluationArtifact {
  artifact_key: string
  kind: string
  payload: unknown
  attempt_id: string
  producer: MetricObservation['producer']
  input_provenance: MetricObservation['input_provenance']
}

export interface LegacyMetricValue {
  metric_id: string
  metric_name: string
  value: unknown
  attempt_id: string
  producer: {
    binding_id: 'legacy-unknown'
    producer_id: 'legacy.unknown'
    kind: 'legacy'
    version: 'unknown'
  }
}

export interface ResultReplicate {
  replicate_label: string
  replicate_number: number
  cell_label: string
  factor_values: Record<string, unknown>
  metric_values: Record<string, unknown>
  status: Trial['status']
  obsolete: boolean
  // The prior attempt is obsolete; this stable replicate slot has no result
  // yet against the current canvas version.
  requires_current_attempt?: boolean
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
  metric_evaluation?: {
    status: 'queued' | 'running' | 'completed' | 'failed' | 'skipped'
    error?: string | null
    evaluator_run_id?: string | null
    metric_ids?: string[]
  } | null
  metric_observations: MetricObservation[]
  evaluation_artifacts: EvaluationArtifact[]
  legacy_values?: LegacyMetricValue[]
  obsolete_runs: ObsoleteRun[]
  superseded_runs: SupersededRun[]
}

// Read-only facts for a past ProtocolRun attempt. Both obsolete and
// superseded attempts stay available for inspection, but never participate in
// current results, CSV, or statistical analysis.
export interface HistoricalRun {
  run_id: string
  status: Trial['status']
  obsolete: boolean
  error: string | null
  protocol_revision_id: string | null
  updated_at: string
  metric_values: Record<string, unknown>
  metric_evaluation?: ResultReplicate['metric_evaluation']
  metric_observations: MetricObservation[]
  evaluation_artifacts: EvaluationArtifact[]
  legacy_values?: LegacyMetricValue[]
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

// A prior attempt used an older published canvas revision.
export interface ObsoleteRun extends HistoricalRun {
  obsolete: true
}

// A prior attempt used the current canvas revision, but was replaced by a
// later run of the same stable replicate slot.
export interface SupersededRun extends HistoricalRun {
  obsolete: false
}

export interface ResultCell {
  cell_label: string
  factor_values: Record<string, unknown>
  replicate_count: number
  completed_count: number
  current_completed_count: number
  obsolete_count: number
  metric_means: Record<string, number>
  metric_counts: Record<string, number>
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
  metric_types: Record<string, 'number' | 'boolean'>
  metric_aggregations: Record<string, 'mean' | 'sum'>
  metric_directions: Record<string, MeasurementDirection>
  primary_metric: string | null
  primary_metric_direction: 'maximize' | 'minimize' | null
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
  // Why an update is needed, not just that it is. 'coordination_strategy_changed'
  // is the reason the counts above can't express -- it adds and removes no
  // cells, so on its own it reads as "no change" beside the banner.
  regeneration_reasons: (
    | 'no_design_generated'
    | 'coordination_strategy_changed'
    | 'design_matrix_changed'
    | 'cells_drifted'
  )[]
}
