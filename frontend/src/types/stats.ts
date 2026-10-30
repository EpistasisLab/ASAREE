export type StatsRow = Record<string, unknown>
export interface StatsConfig {
  metric: string
  factors: string[]
  family: string
  measurement_type: string
  denominator: string | null
  ordinal_levels: string[]
  max_order: number
  n_boot: number
  n_perm: number
  seed: number
  alpha?: number
  confidence_level?: number
  minimum_success_fraction?: number
}
export interface StatsSource {
  factors: { name: string; levels: string[] }[]
  metrics: { name: string; n: number }[]
  n_replicates: number
  n_measured_replicates?: number
  protocol_revision_id?: string | null
  design_revision: number | null
  fingerprint: string
  excluded_row_replicates: number
}
export interface StatsAnalysis {
  id: string
  status: 'pending' | 'running' | 'completed' | 'incomplete' | 'failed'
  error: string | null
  created_at: string
  config: StatsConfig
  fingerprint: string
  design_revision: number | null
  source?: StatsSource
  report_status?: StatsSummary['status'] | null
  output?: {
    family: string
    n_observations: number
    n_excluded: number
    runtime_s: number
    tables: Record<string, StatsRow[]>
    notes: string[]
    report?: string
    validity?: 'valid' | 'unreliable' | 'unavailable' | 'descriptive'
    summary?: StatsSummary
  }
}

export interface StatsSummary {
  primary_analysis?: 'parametric_bootstrap_intervals'
  confidence_level?: number
  distribution?: {
    n: number
    minimum: number
    maximum: number
    mean: number
    sd: number | null
    distinct: number
    zeros: number
    plot?: { kind: 'frequency' | 'histogram'; bins: { label: string; count: number; lower?: number; upper?: number }[]; binning?: string; values?: number[] } | null
  }
  metric_info?: { description: string; unit: string | null; coding: string[] }
  metric_definition?: { description?: string; unit?: string }
  version: number
  alpha?: number
  execution_status?: 'completed' | 'incomplete'
  metric: string
  status: 'valid' | 'unreliable' | 'unavailable' | 'descriptive'
  description: string
  findings: string[]
  tests: { effect: string; p_adjusted: number | null; result: string; method?: string; purpose?: string
    permutation_p_adjusted?: number | null; permutation_method?: string | null
    permutation_status?: string; permutation_result?: string; resampling_name?: string
    resampling_label?: string }[]
  effects: {
    comparison: string; estimate: number | null; scale: string
    unit?: string | null
    method?: string
    purpose?: string
    interval_method?: string | null
    bootstrap_ci_low?: number | null; bootstrap_ci_high?: number | null
    bootstrap_status?: string; interval_agreement?: string
    primary_ci_low?: number | null; primary_ci_high?: number | null
    primary_status?: string; primary_method?: string; analytic_result?: string
    ci_low: number | null; ci_high: number | null; p_adjusted: number | null; result: string
  }[]
  warnings: string[]
  methods: string[]
  resampling?: { bootstrap: { requested: number; attempted: number; successful: number; failed: number; status: string }
    permutations: Record<string, unknown>[] }
  provenance: Record<string, unknown>
}
