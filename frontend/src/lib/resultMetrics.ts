import type { ExecutionCell, ExecutionSummary } from '@/types/executionResults'
import { formatUsageDistributionValue } from './resultUsage'
import { normalizeDesignMetrics } from './metricCatalog'
import { formatTokens } from './tokenFormatting'

export interface ResultMetric {
  key: string
  label: string
  aggregation?: 'mean' | 'sum' | 'none'
  valueType?: 'number' | 'boolean'
  unit?: string | null
}

export function selectedResultMetrics(summary: ExecutionSummary): ResultMetric[] {
  const labels = { ...summary.metric_labels }
  for (const metric of normalizeDesignMetrics(summary.design_spec?.metrics)) {
    const key = metric.id && metric.id in labels ? metric.id
      : metric.catalogKey && metric.catalogKey in labels ? metric.catalogKey : metric.id
    if (key && !(key in labels) && !Object.values(labels).includes(metric.name)) labels[key] = metric.name
  }
  return Object.entries(labels).map(([key, label]) => ({
    key, label, aggregation: summary.metric_aggregations[key], valueType: summary.metric_types[key], unit: summary.metric_units?.[key],
  })).sort((left, right) => left.label.localeCompare(right.label))
}

export function hasNumericCellAggregation(metric: ResultMetric): boolean {
  return (metric.aggregation === 'mean' || metric.aggregation === 'sum')
    && (metric.valueType === 'number' || metric.valueType === 'boolean')
}

export function cellMetricValue(cell: ExecutionCell, metric: ResultMetric): number | null {
  const value = cell.metric_values?.[metric.key]
  const count = cell.metric_counts?.[metric.key] ?? 0
  return count > 0 && typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function formatCellMetric(value: number, metric: ResultMetric): string {
  if (metric.unit === 'tokens' && value >= 1_000_000) return `${formatTokens(value)} tokens`
  if (metric.valueType === 'boolean' && metric.aggregation === 'mean') {
    return new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 }).format(value)
  }
  if (metric.unit === 'USD') return formatUsageDistributionValue('cost_usd', value)
  if (metric.unit === 'seconds') return formatUsageDistributionValue('duration_seconds', value)
  const formatted = value.toLocaleString(undefined, { maximumSignificantDigits: 4 })
  return metric.unit ? `${formatted} ${metric.unit}` : formatted
}

export function metricAggregationLabel(metric: ResultMetric): string {
  return metric.aggregation === 'sum' ? 'Sum' : 'Mean'
}
