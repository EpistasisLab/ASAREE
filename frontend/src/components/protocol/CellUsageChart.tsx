import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { ReactNode } from 'react'
import type { ExecutionCell, UsageSummary } from '@/types/executionResults'
import { formatTokens } from '@/lib/tokenFormatting'

import { usageMetricLabels, type UsageMetric } from '@/lib/resultUsage'

type DurationUnit = 's' | 'min' | 'hr'

function formatValue(value: number, metric: UsageMetric, durationUnit: DurationUnit): string {
  if (metric === 'cost_usd') return new Intl.NumberFormat(undefined, {
    style: 'currency', currency: 'USD', maximumFractionDigits: value > 0 && value < 0.01 ? 4 : 2,
  }).format(value)
  if (metric === 'duration_seconds') {
    const divisor = durationUnit === 'hr' ? 3600 : durationUnit === 'min' ? 60 : 1
    const duration = value / divisor
    return `${duration.toLocaleString(undefined, { maximumFractionDigits: duration > 0 && duration < 0.1 ? 3 : 1 })} ${durationUnit}`
  }
  return formatTokens(value)
}

function exactDuration(seconds: number): string {
  const rounded = Math.round(seconds * 10) / 10
  const hours = Math.floor(rounded / 3600)
  const minutes = Math.floor((rounded % 3600) / 60)
  const remainder = Math.round((rounded % 60) * 10) / 10
  return [hours > 0 ? `${hours} hr` : '', minutes > 0 ? `${minutes} min` : '',
    remainder > 0 || rounded === 0 ? `${remainder.toLocaleString()} sec` : ''].filter(Boolean).join(' ')
}

export function CellUsageChart({ cells, metric, loading, error, attemptScope = 'latest', pagination, completed = false, onSelectCell, selectedCellId }: {
  cells: ExecutionCell[]; metric: UsageMetric; loading: boolean; error?: string
  attemptScope?: 'latest' | 'all'; pagination?: ReactNode
  completed?: boolean
  onSelectCell?: (cell: ExecutionCell) => void
  selectedCellId?: string
}) {
  const labels = usageMetricLabels(completed)
  const hasSelection = cells.some(cell => cell.cell_id === selectedCellId)
  const chartTitle = `${labels[metric].replace(' so far', '')} by cell`
  const valueFor = (usage: UsageSummary | undefined) => {
    const value = usage?.[metric]
    return value != null && Number.isFinite(value) && value >= 0 ? value : null
  }
  const cellUsage = (cell: ExecutionCell) => attemptScope === 'all' ? cell.all_attempt_usage ?? undefined : cell.usage
  const maximum = Math.max(0, ...cells.map(cell => valueFor(cellUsage(cell)) ?? 0))
  const scale = maximum > 0 ? maximum * 1.2 : 1
  const durationUnit: DurationUnit = maximum < 120 ? 's' : maximum < 7200 ? 'min' : 'hr'
  const tokenScale = maximum >= 1e9 ? { divisor: 1e9, label: 'Tokens (billions)' }
    : maximum >= 1e6 ? { divisor: 1e6, label: 'Tokens (millions)' }
      : maximum >= 1e3 ? { divisor: 1e3, label: 'Tokens (thousands)' } : { divisor: 1, label: 'Tokens' }
  const format = (value: number) => formatValue(value, metric, durationUnit)
  const formatTick = (value: number) => metric === 'total_tokens'
    ? (value / tokenScale.divisor).toLocaleString('en-US', { maximumFractionDigits: tokenScale.divisor === 1 ? 0 : 2 })
    : format(value)
  return <Card size="sm" className="gap-3 p-3" role="region" aria-label={chartTitle}>
    <div>
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-xs font-medium">{chartTitle}</h3>
        {!completed && <Badge variant="outline" className="shrink-0 border-[color:var(--chart-4)]/40 text-[color:var(--chart-4)]">Partial data</Badge>}
      </div>
      {metric === 'total_tokens' && <p className="mt-1 text-[11px] text-muted-foreground">{tokenScale.label}</p>}
      {metric === 'duration_seconds' && <p className="mt-1 text-[11px] text-muted-foreground">Execution time ({durationUnit})</p>}
    </div>
    {loading ? <p role="status" className="text-xs text-muted-foreground">Loading cell usage…</p>
      : error ? <p role="alert" className="text-xs text-destructive">{error}</p>
        : cells.length === 0 ? <p className="text-xs text-muted-foreground">No cells to display.</p>
          : <div className="flex gap-2">
            <div aria-hidden="true" className="flex h-44 shrink-0 flex-col justify-between text-right font-mono text-[10px] text-muted-foreground">
              {[scale, scale / 2, 0].map(value => <span key={value}>{formatTick(value)}</span>)}
            </div>
            <div className="min-w-0 flex-1 overflow-x-auto pb-1">
              <div className="relative" style={{ minWidth: `${cells.length * 64}px` }}>
                <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 flex h-44 flex-col justify-between">
                  {[0, 1, 2].map(line => <div key={line} className="border-t border-border" />)}
                </div>
                <ul aria-label="Cell usage values" className="relative flex">
                  {cells.map((cell, index) => {
                    const selected = cell.cell_id === selectedCellId
                    const value = valueFor(cellUsage(cell))
                    const label = `Cell ${cell.cell_number ?? index + 1}`
                    const formatted = value === null ? 'Not reported' : format(value)
                    const exactValue = value !== null && metric === 'total_tokens' ? value.toLocaleString('en-US') : formatted
                    const tooltipValue = value !== null && metric === 'duration_seconds' ? exactDuration(value) : exactValue
                    return <li key={cell.cell_id} className="min-w-16 flex-1 px-1 text-center">
                      <div className="flex h-44 flex-col justify-end">
                        <Button variant="ghost" aria-label={`Open ${label} details`} aria-pressed={selected} disabled={!onSelectCell} onClick={() => onSelectCell?.(cell)}
                          title={`${label} · ${cell.cell_label}\n${labels[metric]}: ${tooltipValue}`}
                          className={`relative mx-auto w-full max-w-12 rounded-t-sm rounded-b-none p-0 transition-[height,background-color] hover:bg-primary/80 dark:hover:bg-primary/80 ${selected ? 'bg-primary/90' : hasSelection ? 'bg-primary/25' : 'bg-primary/50'}`}
                          style={{ height: value === null ? '2px' : `${Math.max(1, value / scale * 100)}%` }}>
                          <span role="img" aria-label={`${label}: ${exactValue}`} title={`${label} · ${cell.cell_label}\n${labels[metric]}: ${tooltipValue}`} className="absolute inset-0">
                            <span className="absolute inset-x-0 bottom-full pb-1 font-mono text-[10px] text-foreground">{value === null ? '—' : formatted}</span>
                          </span>
                        </Button>
                      </div>
                      <p className={`mt-2 font-mono text-[10px] ${selected ? 'font-semibold text-primary' : 'text-muted-foreground'}`}>{label}</p>
                    </li>
                  })}
                </ul>
              </div>
            </div>
          </div>}
    {pagination}
    {!loading && !error && cells.length > 0 && <p className="text-[11px] text-muted-foreground">— = usage not reported.</p>}
  </Card>
}
