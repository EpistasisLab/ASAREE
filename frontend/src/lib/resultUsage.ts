import type { ExecutionSummary } from '@/types/executionResults'

export type UsageMetric = 'total_tokens' | 'cost_usd' | 'duration_seconds'

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
