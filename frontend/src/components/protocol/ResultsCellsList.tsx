import { CircleCheck } from 'lucide-react'
import { CellCard } from './CellPresentation'
import { formatTokens } from '@/lib/tokenFormatting'
import type { UsageMetric } from '@/lib/resultUsage'
import type { ExecutionCell, ExecutionSummary } from '@/types/executionResults'
import type { DesignSpec } from '@/types/experiments'
import { displayFactorCondition } from '@/lib/experiment'
import { cellMetricValue, formatCellMetric, hasNumericCellAggregation, metricAggregationLabel, type ResultMetric } from '@/lib/resultMetrics'

const metricLabels: Record<UsageMetric, string> = {
  total_tokens: 'Tokens', cost_usd: 'Cost', duration_seconds: 'Duration',
}

export function ResultsCellsList({ cells, metric, measurement, attemptScope, unit, startIndex = 0, onSelect, designSpec, selectedCellId }: {
  cells: ExecutionCell[]; metric: UsageMetric; attemptScope: 'latest' | 'all'
  measurement?: ResultMetric
  unit: ExecutionSummary['unit']; startIndex?: number; onSelect: (cell: ExecutionCell) => void
  designSpec?: DesignSpec | null
  selectedCellId?: string
}) {
  const numericMeasurement = measurement && unit === 'replicate' && hasNumericCellAggregation(measurement)
  return <ul aria-label="Cells" className="space-y-2">
        {cells.map((cell, index) => {
          const conditions = Object.entries(cell.factor_values).map(([name, value]) =>
            displayFactorCondition(name, value, designSpec, cell.cell_label))
          const completed = cell.completed_replicate_count ?? (unit === 'replicate' ? cell.counts?.completed ?? 0 : null)
          const total = cell.replicate_count ?? (unit === 'replicate' ? cell.total : null)
          const done = total != null && total > 0 && completed === total
          const usage = attemptScope === 'all' ? cell.all_attempt_usage : cell.usage
          const value = usage?.[metric]
          let formatted = '—'
          if (value != null && Number.isFinite(value)) {
            formatted = metric === 'total_tokens' ? formatTokens(value)
              : metric === 'cost_usd' ? new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: value > 0 && value < 0.01 ? 4 : 2 }).format(value)
                : value < 60 ? `${Math.round(value)} sec` : value < 3600 ? `${(value / 60).toFixed(1)} min` : `${(value / 3600).toFixed(1)} hr`
          }
          const measurementValue = measurement ? cellMetricValue(cell, measurement) : null
          if (measurement) formatted = !numericMeasurement ? 'Open recorded results'
            : measurementValue === null ? '—' : formatCellMetric(measurementValue, measurement)
          return <li key={cell.cell_id}>
            <CellCard results selected={cell.cell_id === selectedCellId} label={cell.cell_label}
              conditions={conditions.length ? conditions : [cell.cell_label]} cellNumber={cell.cell_number ?? startIndex + index + 1}
              toggleLabel={`Cell ${cell.cell_number ?? startIndex + index + 1}: ${conditions.join(' · ') || cell.cell_label}`}
              replicateCount={cell.replicate_count ?? 0} expanded={false} listId={`results-${cell.cell_id}`} onToggle={() => onSelect(cell)}
              progress={<>
              <span className="inline-flex items-center gap-1.5 whitespace-nowrap font-mono tabular-nums"
                title={`${completed ?? 'Unknown'} of ${total ?? 'unknown'} replicates completed`}>
                {completed?.toLocaleString() ?? '—'}/{total?.toLocaleString() ?? '—'}
                {done && <CircleCheck className="size-3.5 shrink-0 text-[color:var(--chart-3)]" aria-label="Completed" />}
              </span>
              <span className="break-words">{measurement ? `${measurement.label}${numericMeasurement ? ` · ${metricAggregationLabel(measurement)}` : ''}` : metricLabels[metric]}:{' '}
                <span className="font-mono tabular-nums"
                  title={measurement ? numericMeasurement ? `${measurement.unit === 'tokens' && measurementValue !== null ? `${measurementValue.toLocaleString()} tokens · ` : ''}${cell.metric_counts?.[measurement.key] ?? 0} measured replicates` : 'Open the cell details to inspect recorded values and missing-result reasons.' : value == null ? 'Not reported' : metric === 'total_tokens' ? `${value.toLocaleString()} tokens` : metric === 'duration_seconds' ? `${value.toLocaleString()} seconds` : `${value.toLocaleString(undefined, { maximumFractionDigits: 6 })} USD`}>{formatted}</span>
              </span>
              {measurement && numericMeasurement && <span className="font-mono text-[10px]">{cell.metric_counts?.[measurement.key] ?? 0} measured</span>}
              </>}>{null}</CellCard>
          </li>
        })}
  </ul>
}
