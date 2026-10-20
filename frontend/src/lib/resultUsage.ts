export type UsageMetric = 'total_tokens' | 'cost_usd' | 'duration_seconds'

export function usageMetricLabels(completed: boolean): Record<UsageMetric, string> {
  return {
    total_tokens: completed ? 'Total tokens' : 'Tokens so far',
    cost_usd: completed ? 'Final cost' : 'Cost so far',
    duration_seconds: completed ? 'Total execution time' : 'Accumulated execution time',
  }
}
