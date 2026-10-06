import type { MetricObservation } from '@/types/experiments'

export function reportedMetricText(observation?: MetricObservation): string {
  if (!observation) return 'Unavailable'
  if (observation.status !== 'measured') return observation.status.replaceAll('_', ' ')
  return typeof observation.value === 'string' ? observation.value : JSON.stringify(observation.value)
}
