import { useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Sigma } from 'lucide-react'
import type { ExecutionCell, ExecutionSummary } from '@/types/executionResults'
import { CellUsageChart } from './CellUsageChart'
import { experimentResultStatus, type UsageMetric } from '@/lib/resultUsage'
import { formatCellMetric, selectedResultMetrics } from '@/lib/resultMetrics'
import { ResultsScorecard } from './ResultsPanel'

export function ResultsProgress({ summary, cells, cellsLoading, cellsError, attemptScope = 'latest', onAttemptScopeChange, chartPagination, selectedMetric, onMetricChange, onSelectCell, selectedCellId }: {
  summary: ExecutionSummary; cells: ExecutionCell[]; cellsLoading: boolean; cellsError?: string
  attemptScope?: 'latest' | 'all'; onAttemptScopeChange?: (scope: 'latest' | 'all') => void; chartPagination?: ReactNode
  selectedMetric?: string; onMetricChange?: (metric: string) => void
  onSelectCell?: (cell: ExecutionCell) => void
  selectedCellId?: string
}) {
  const [internalMetric, setInternalMetric] = useState<string>('')
  const measurements = selectedResultMetrics(summary)
  const metric = (selectedMetric ?? internalMetric) || (measurements[0] ? `metric:${measurements[0].key}` : '')
  const measurement = measurements.find(item => `metric:${item.key}` === metric)
  const metricTotal = measurement ? summary.metric_totals?.[measurement.key] : undefined
  const metricTotalCount = measurement ? summary.metric_total_counts?.[measurement.key] ?? 0 : 0
  const usageMetric: UsageMetric = metric === 'cost_usd' || metric === 'duration_seconds' ? metric : 'total_tokens'
  const completed = summary.counts?.completed ?? 0
  const total = summary.expected ?? summary.total ?? 0
  const status = experimentResultStatus(summary)
  const label = summary.unit === 'row' ? 'Rows completed' : 'Replicates completed'
  const completedExperiment = status === 'Completed'
  const number = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString()

  return <section aria-label="Experiment progress" className="space-y-3">
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span id="results-progress-label">{label}</span>
        <span className="font-mono tabular-nums">{number(completed)}/{number(total)}</span>
      </div>
      <div role="progressbar" aria-labelledby="results-progress-label" aria-valuemin={0} aria-valuemax={total || 1} aria-valuenow={completed}
        aria-valuetext={`${number(completed)} of ${number(total)} ${summary.unit === 'row' ? 'rows' : 'replicates'} completed`}
        className="h-2 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${total > 0 ? Math.min(100, completed / total * 100) : 0}%` }} />
      </div>
    </div>
    <div className="space-y-2">
      <div role="group" aria-label="Usage attempts" className="flex flex-wrap gap-1 rounded-md border bg-muted/30 p-1">
        {(['latest', 'all'] as const).map(scope => <Button key={scope} variant="ghost" size="sm"
          aria-pressed={attemptScope === scope} onClick={() => onAttemptScopeChange?.(scope)}
          className="min-w-0 flex-1 aria-pressed:border-primary aria-pressed:bg-primary/10 aria-pressed:text-primary">
          {scope === 'latest' ? 'Latest attempts' : 'All attempts'}
        </Button>)}
      </div>
      <p className="text-[11px] text-muted-foreground">{attemptScope === 'all' ? 'Includes previous attempts and retries.'
        : `Most recent attempt for each ${summary.unit === 'row' ? 'row execution' : 'replicate'}.`}</p>
    </div>
    <label className="block space-y-1 text-xs text-muted-foreground">
      <span>Metric by cell</span>
      <select aria-label="Metric by cell" value={measurement ? metric : ''} disabled={measurements.length === 0}
        onChange={event => { setInternalMetric(event.target.value); onMetricChange?.(event.target.value) }}
        className="h-9 w-full min-w-0 rounded-md border border-input bg-background px-2 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <option value="" disabled hidden={measurements.length > 0}>{measurements.length === 0 ? 'No experiment metrics selected' : ''}</option>
        {measurements.map(item => <option key={item.key} value={`metric:${item.key}`}>{item.label}</option>)}
      </select>
    </label>
    {measurement && metricTotal !== undefined && <div role="region" aria-label={`${measurement.label} total`}>
      <ResultsScorecard label={`${measurement.label} · Total`} icon={Sigma}
        help="Sum of recorded measurements from the latest completed attempts across all cells in this experiment version. Attempts that reached their iteration limit and missing measurements are excluded."
        value={metricTotal === null ? '—' : formatCellMetric(metricTotal, measurement)}
        valueTitle={metricTotal === null ? 'No measurements yet' : `${metricTotal.toLocaleString()}${measurement.unit ? ` ${measurement.unit}` : ''}`}
        note={metricTotalCount > 0
          ? `${metricTotalCount.toLocaleString()} measured ${summary.unit === 'row' ? 'row executions' : 'replicates'} · All cells in this version`
          : 'No measurements yet'} />
    </div>}
    {measurement && <p className="text-[11px] text-muted-foreground">Selected metrics show the latest attempt for each {summary.unit === 'row' ? 'row execution. Row outcomes are listed individually without cell aggregation' : 'replicate. Numeric cell summaries use completed attempts that did not reach their iteration limit'}. Open a cell below to inspect its recorded values and reasons for missing results. The attempt toggle above applies only to operational usage summaries. Missing measurements are excluded, not treated as zero.</p>}
    {(!measurement || summary.unit === 'replicate') && <CellUsageChart cells={cells} metric={usageMetric} measurement={measurement} loading={cellsLoading} error={cellsError} attemptScope={attemptScope} pagination={chartPagination} completed={completedExperiment} onSelectCell={onSelectCell} selectedCellId={selectedCellId} />}
  </section>
}
