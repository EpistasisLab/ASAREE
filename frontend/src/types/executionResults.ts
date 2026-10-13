import type { DesignSpec, EvaluationArtifact, MetricObservation, MeasurementDirection } from './experiments'
import type { DatasetRowSnapshot, NodeRunState } from './protocols'

export interface CursorPage<T> { items: T[]; next_cursor: string | null }
export interface ResultJob {
  id: string; kind: 'plan' | 'cancel' | 'export'
  status: 'pending' | 'awaiting_confirmation' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'expired'
  progress: { scanned?: number; planned?: number; created?: number; skipped?: number; cancelled?: number; bytes?: number; cell_count?: number; replicate_count?: number }
  error: string | null; protocol_revision_id: string | null; download_url?: string; schema_url?: string
}
export interface UsageSummary {
  cost_usd: number | null
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  duration_seconds: number | null
  agent_run_count: number | null
  complete?: boolean
  reported_usage_count?: number; reported_cost_count?: number
  by_node?: Record<string, UsageSummary>
}
export interface ObservationPreview {
  metric_id: string; metric_name: string; status: string
  preview: string; preview_truncated: boolean
}
export interface ExecutionPreview {
  metrics?: Record<string, number>
  observations?: ObservationPreview[]
  truncated?: boolean
  evaluation_state?: string | null
}
export interface ExecutionSummary {
  scope_key: string; unit: 'row' | 'replicate'; ready: boolean
  protocol_id: string; protocol_revision_id: string | null; design_revision_id: string | null
  generation: number; change_id: number; refreshing: boolean; updated_at: string | null; as_of?: string
  counts?: Record<string, number>; total?: number; cell_count?: number; replicate_count?: number
  source_row_count?: number | null; expected?: number | null; has_active?: boolean
  executed_count?: number; scored_count?: number; best_cell?: { cell_id: string; cell_label: string; metric_key: string; value: number } | null
  usage?: UsageSummary; usage_complete_count?: number; truncated_count?: number; evaluating_count?: number
  all_attempt_usage?: UsageSummary; usage_pending_count?: number
  design_spec: DesignSpec | null
  metric_types: Record<string, 'number' | 'boolean'>
  metric_labels?: Record<string, string>
  metric_aggregations: Record<string, 'mean' | 'sum'>
  metric_directions: Record<string, MeasurementDirection>
  primary_metric: string | null; primary_metric_direction: string | null
  etag?: string
}
export interface ExecutionCell {
  cell_id: string; cell_label: string; factor_values: Record<string, unknown>
  replicate_count?: number; total?: number; counts?: Record<string, number>; truncated?: number
  usage?: UsageSummary; metric_values?: Record<string, number>; metric_counts?: Record<string, number>
  primary_rank?: number
}
export interface ExecutionSlot {
  slot_id: string; cell_id: string; cell_label: string; factor_values: Record<string, unknown>
  replicate_id: string; replicate_label: string; replicate_number: number; row_result_id: string | null
  row_index: number | null; dataset_id: string | null; raw_sha256: string | null
  run_id: string | null; status: string; updated_at: string | null; error: string | null
  result: ExecutionPreview; usage: UsageSummary | null
}
export interface AttemptPreview {
  run_id: string; status: string; created_at: string; completed_at: string | null
  error: string | null; current: boolean; result: ExecutionPreview; usage: UsageSummary | null
}
export interface AttemptPage extends CursorPage<AttemptPreview> { slot: ExecutionSlot; legacy_result?: { metric_values: Record<string, unknown> | null; artifacts: unknown } | null }
export interface AttemptDetail {
  run_id: string; status: string; error: string | null; started_at: string | null; completed_at: string | null
  protocol_revision_id: string | null; dataset_row: DatasetRowSnapshot | null
  node_runs: Record<string, NodeRunState>; node_labels: Record<string, string>; usage: UsageSummary | null
  attempt_result: {
    metric_values?: Record<string, unknown>
    metric_evaluation?: { status: 'queued' | 'running' | 'completed' | 'failed' | 'skipped'; error?: string | null }
    measurement?: { observations?: MetricObservation[]; artifacts?: EvaluationArtifact[] }
  } | null
}
