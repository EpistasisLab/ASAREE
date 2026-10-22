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
  output?: {
    family: string
    n_observations: number
    n_excluded: number
    runtime_s: number
    tables: Record<string, StatsRow[]>
    notes: string[]
    unavailable: { title: string; reason: string; reference: string }[]
    report?: string
  }
}
