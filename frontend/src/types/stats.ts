export type StatsBackend = 'stats' | 'stats_sm'
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
  n_boot_lrt: number
  n_perm: number
  seed: number
  tweedie_power: number
}
export interface StatsSource {
  factors: { name: string; levels: string[] }[]
  metrics: { name: string; n: number }[]
  n_replicates: number
  design_revision: number | null
  fingerprint: string
  excluded_row_replicates: number
}
export interface StatsAnalysis {
  id: string
  backend: StatsBackend
  status: 'pending' | 'running' | 'completed' | 'failed'
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
    unavailable: { title: string; reason: string; reference: string }[]
    report?: string
    validity?: 'valid' | 'unreliable' | 'unavailable'
    summary?: StatsSummary
  }
}

export interface StatsSummary {
  metric_info?: { description: string; unit: string | null; coding: string[] }
  metric_definition?: { description?: string; unit?: string }
  version: number
  metric: string
  status: 'valid' | 'unreliable' | 'unavailable'
  description: string
  findings: string[]
  tests: { effect: string; p_adjusted: number | null; result: string; method?: string; purpose?: string
    permutation_p_adjusted?: number | null; permutation_method?: string | null
    permutation_status?: string; permutation_result?: string }[]
  effects: {
    comparison: string; estimate: number | null; scale: string
    unit?: string | null
    method?: string
    purpose?: string
    interval_method?: string | null
    bootstrap_ci_low?: number | null; bootstrap_ci_high?: number | null
    bootstrap_status?: string; interval_agreement?: string
    ci_low: number | null; ci_high: number | null; p_adjusted: number | null; result: string
  }[]
  warnings: string[]
  methods: string[]
  resampling?: { bootstrap: { requested: number; attempted: number; successful: number; failed: number; status: string }
    permutations: Record<string, unknown>[] }
  provenance: Record<string, unknown>
}
