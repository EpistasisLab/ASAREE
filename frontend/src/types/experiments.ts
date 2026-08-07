export interface Experiment {
  id: string
  name: string
  description: string | null
  design_type: string
  task_brief: Record<string, unknown> | null
  design_spec: Record<string, unknown> | null
  created_at: string
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
