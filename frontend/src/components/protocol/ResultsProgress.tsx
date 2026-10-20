import { CircleDollarSign, Clock3, Coins } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import type { ExecutionCell, ExecutionSummary } from '@/types/executionResults'
import { CellUsageChart } from './CellUsageChart'
import { experimentResultStatus, formatUsageValue, usageMetricLabels, type UsageMetric } from '@/lib/resultUsage'

export function ResultsProgress({ summary, cells, cellsLoading, cellsError, attemptScope = 'latest', onAttemptScopeChange, chartPagination, selectedMetric, onMetricChange, onSelectCell, selectedCellId }: {
  summary: ExecutionSummary; cells: ExecutionCell[]; cellsLoading: boolean; cellsError?: string
  attemptScope?: 'latest' | 'all'; onAttemptScopeChange?: (scope: 'latest' | 'all') => void; chartPagination?: ReactNode
  selectedMetric?: UsageMetric; onMetricChange?: (metric: UsageMetric) => void
  onSelectCell?: (cell: ExecutionCell) => void
  selectedCellId?: string
}) {
  const [internalMetric, setInternalMetric] = useState<UsageMetric>('total_tokens')
  const metric = selectedMetric ?? internalMetric
  const completed = summary.counts?.completed ?? 0
  const total = summary.expected ?? summary.total ?? 0
  const status = experimentResultStatus(summary)
  const label = summary.unit === 'row' ? 'Rows completed' : 'Replicates completed'
  const usage = attemptScope === 'all' ? summary.all_attempt_usage : summary.usage
  const completedExperiment = status === 'Completed'
  const labels = usageMetricLabels(completedExperiment)
  const usageDescription = attemptScope === 'all' ? 'all attempts, including retries' : 'the most recent attempt for each replicate'
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
    <div className="grid grid-cols-3 gap-2">
      {([
        ['total_tokens', labels.total_tokens, formatUsageValue('total_tokens', usage?.total_tokens), Coins, `Input and output tokens across ${usageDescription}.`],
        ['cost_usd', labels.cost_usd, formatUsageValue('cost_usd', usage?.cost_usd), CircleDollarSign, `Reported or estimated cost across ${usageDescription}.`],
        ['duration_seconds', labels.duration_seconds, formatUsageValue('duration_seconds', usage?.duration_seconds), Clock3, `Cumulative duration of ${usageDescription}. Parallel runs overlap in wall-clock time.`],
      ] as const).map(([key, title, value, Icon, help]) => <Card key={title} size="sm" className="min-w-0 gap-0 p-0">
        <Button variant="ghost" aria-label={`Show ${title.toLowerCase()} by cell`} aria-pressed={metric === key}
          onClick={() => { setInternalMetric(key); onMetricChange?.(key) }} title={help}
          className="h-full w-full min-w-0 flex-col items-stretch justify-start gap-1 whitespace-normal p-2 text-left aria-pressed:border-primary aria-pressed:bg-primary/10 @min-[450px]:p-3">
          <span className="flex items-start gap-1 text-[11px] text-muted-foreground @min-[450px]:text-xs">
            <Icon className="mt-0.5 size-3 shrink-0" aria-hidden="true" /><span className="min-w-0">{title}</span>
          </span>
          <span className="mt-auto truncate text-center font-mono text-sm font-medium tabular-nums @min-[450px]:text-lg" title={key === 'total_tokens' ? `${number(usage?.total_tokens)} tokens` : value}>{value}</span>
        </Button>
      </Card>)}
    </div>
    <CellUsageChart cells={cells} metric={metric} loading={cellsLoading} error={cellsError} attemptScope={attemptScope} pagination={chartPagination} completed={completedExperiment} onSelectCell={onSelectCell} selectedCellId={selectedCellId} />
  </section>
}
