import type { ExecutionSummary } from '@/types/executionResults'
import type { Experiment } from '@/types/experiments'
import { formatTokens } from './tokenFormatting'

export type UsageMetric = 'total_tokens' | 'cost_usd' | 'duration_seconds'

export function runtimeUsageMetricKeys(experiment: Pick<Experiment, 'measurement_plan' | 'design_spec'>): Record<string, UsageMetric> {
  const keys: Record<string, UsageMetric> = {}
  const isUsageMetric = (key: string): key is UsageMetric => ['total_tokens', 'cost_usd', 'duration_seconds'].includes(key)
  if (experiment.measurement_plan) {
    for (const producer of experiment.measurement_plan.producers) {
      if (producer.producer_id !== 'asaree.runtime' && producer.producer_id !== 'asaree.node_runtime') continue
      for (const [output, metricId] of Object.entries(producer.outputs)) if (isUsageMetric(output)) keys[metricId] = output
    }
  } else {
    for (const metric of experiment.design_spec?.metrics ?? []) {
      if (metric.kind === 'runtime' && metric.id && metric.catalogKey && isUsageMetric(metric.catalogKey)) keys[metric.id] = metric.catalogKey
    }
  }
  return keys
}

export function formatUsageDistributionValue(metric: UsageMetric, value: number | null): string {
  if (value === null) return '—'
  if (metric === 'cost_usd') return new Intl.NumberFormat(undefined, {
    style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 6,
  }).format(value)
  const number = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 2 })
  if (metric === 'total_tokens') return number(value)
  if (value < 120) return `${number(value)} sec`
  if (value < 7200) return `${number(value / 60)} min`
  return `${number(value / 3600)} hr`
}

export function formatUsageValue(metric: UsageMetric, value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  if (metric === 'total_tokens') return formatTokens(value)
  if (metric === 'cost_usd') return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value)
  if (value < 60) return `${Math.round(value)} sec`
  if (value < 3600) return `${(value / 60).toFixed(1)} min`
  return `${(value / 3600).toFixed(1)} hr`
}

export function experimentResultStatus(summary: ExecutionSummary): string {
  const completed = summary.counts?.completed ?? 0
  const total = summary.expected ?? summary.total ?? 0
  const active = summary.has_active || ['pending', 'queued', 'running', 'finalizing'].some(status => (summary.counts?.[status] ?? 0) > 0)
  if (active) return 'In progress'
  if (total > 0 && completed >= total) return 'Completed'
  if ((summary.counts?.failed ?? 0) + (summary.counts?.limit_reached ?? 0) > 0) return 'Needs attention'
  if ((summary.counts?.cancelled ?? 0) > 0) return 'Cancelled'
  return completed > 0 ? 'In progress' : 'Not started'
}

export function usageMetricLabels(completed: boolean): Record<UsageMetric, string> {
  return {
    total_tokens: completed ? 'Total tokens' : 'Tokens so far',
    cost_usd: completed ? 'Final cost' : 'Cost so far',
    duration_seconds: completed ? 'Total execution time' : 'Accumulated execution time',
  }
}
