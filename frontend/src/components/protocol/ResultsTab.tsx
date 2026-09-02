import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ChevronDown, ChevronRight, CircleDollarSign, Clock3, Coins, Cpu, ExternalLink, X } from 'lucide-react'
import { experimentsApi } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { displayFactorValue, formatMetricLabel, formatMetricValue } from '@/lib/experiment'
import type { ResultCell, ResultReplicate } from '@/types/experiments'

function formatNumber(value: number | null, maximumFractionDigits = 0): string {
  if (value === null || !Number.isFinite(value)) return 'Not reported'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits }).format(value)
}

function formatCurrency(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return 'Not reported'
  return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(value)
}

function formatDuration(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return 'Not reported'
  if (value < 60) return `${Math.round(value)} sec`
  if (value < 3600) return `${(value / 60).toFixed(1)} min`
  return `${(value / 3600).toFixed(1)} hr`
}

function factorSummary(values: Record<string, unknown>): string {
  const entries = Object.entries(values)
  if (entries.length === 0) return 'No varying factors'
  return entries.map(([name, value]) => `${name.split(':').join(' · ')}: ${displayFactorValue(value)}`).join(' · ')
}

function numericMetricValue(replicate: ResultReplicate, metricKey: string | null): number | null {
  if (!metricKey) return null
  const value = replicate.metric_values[metricKey]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function statusLabel(status: ResultReplicate['status'], obsolete: boolean): string {
  if (obsolete) return 'Obsolete'
  return {
    not_started: 'Not started',
    queued: 'Queued',
    running: 'Running',
    completed: 'Completed',
    failed: 'Failed',
    cancelled: 'Cancelled',
  }[status]
}

function statusClass(status: ResultReplicate['status'], obsolete: boolean): string {
  if (obsolete) return 'border-transparent bg-[color:var(--chart-2)]/10 text-[color:var(--chart-2)]'
  if (status === 'completed') return 'border-transparent bg-[color:var(--chart-3)]/10 text-[color:var(--chart-3)]'
  if (status === 'failed') return 'border-transparent bg-destructive/10 text-destructive'
  if (status === 'running' || status === 'queued') return 'border-transparent bg-primary/10 text-primary'
  return 'border-transparent bg-muted text-muted-foreground'
}

function Scorecard({ label, value, note, icon: Icon }: { label: string; value: string; note?: string; icon: typeof Coins }) {
  return (
    <div className="rounded-md border bg-card px-2.5 py-2">
      <div className="flex items-center justify-between gap-2 text-muted-foreground">
        <span className="text-xs">{label}</span>
        <Icon className="size-3.5" aria-hidden="true" />
      </div>
      <p className="mt-1 truncate text-base font-medium tabular-nums" title={value}>{value}</p>
      {note && <p className="mt-0.5 text-[11px] text-muted-foreground">{note}</p>}
    </div>
  )
}

function CellCard({ cell, metricKey }: { cell: ResultCell; metricKey: string | null }) {
  const metric = metricKey ? cell.metric_means[metricKey] : undefined
  return (
    <div className="rounded-md border px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-medium" title={factorSummary(cell.factor_values)}>{factorSummary(cell.factor_values)}</p>
        {cell.obsolete_count > 0 && <AlertTriangle className="mt-0.5 size-4 shrink-0 text-[color:var(--chart-4)]" aria-label="Includes obsolete results" />}
      </div>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs">
        {metricKey && (
          <span>
            <span className="text-muted-foreground">{formatMetricLabel(metricKey)} </span>
            <span className="font-medium tabular-nums">{metric === undefined ? '—' : formatMetricValue(metricKey, metric)}</span>
          </span>
        )}
        <span className="text-muted-foreground">{cell.current_completed_count}/{cell.replicate_count} current complete</span>
        {cell.cost_usd !== null && <span className="text-muted-foreground">{formatCurrency(cell.cost_usd)}</span>}
      </div>
    </div>
  )
}

function CellResultSummary({ cell, metricKeys }: { cell: ResultCell; metricKeys: string[] }) {
  const metrics = metricKeys.filter((key) => typeof cell.metric_means[key] === 'number')
  const hasUsage = cell.cost_usd !== null || cell.total_tokens !== null || cell.duration_seconds !== null
  return (
    <section className="mb-2 rounded-md border bg-background px-2.5 py-2" aria-label="Cell result summary">
      <p className="text-xs text-muted-foreground">Current replicates only</p>
      {metrics.length > 0 && (
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {metrics.map((key) => (
            <div key={key} className="rounded border px-2 py-1.5">
              <p className="truncate text-[11px] text-muted-foreground" title={formatMetricLabel(key)}>{formatMetricLabel(key)}</p>
              <p className="mt-0.5 text-sm font-medium tabular-nums">{formatMetricValue(key, cell.metric_means[key])}</p>
            </div>
          ))}
        </div>
      )}
      {hasUsage && (
        <div className={`${metrics.length > 0 ? 'mt-2 border-t pt-2' : 'mt-2'} flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground`}>
          {cell.cost_usd !== null && <span>Cost <span className="font-medium text-foreground">{formatCurrency(cell.cost_usd)}</span></span>}
          {cell.total_tokens !== null && <span>Tokens <span className="font-medium text-foreground">{formatNumber(cell.total_tokens)}</span></span>}
          {cell.duration_seconds !== null && <span>Duration <span className="font-medium text-foreground">{formatDuration(cell.duration_seconds)}</span></span>}
        </div>
      )}
      {metrics.length === 0 && !hasUsage && <p className="mt-1 text-xs text-muted-foreground">No current result data has been reported yet.</p>}
    </section>
  )
}

function ReplicateResultDetail({ replicate, metricKeys }: {
  replicate: ResultReplicate
  metricKeys: string[]
}) {
  const metrics = metricKeys.filter((key) => typeof replicate.metric_values[key] === 'number')
  const hasUsage = replicate.cost_usd !== null || replicate.total_tokens !== null || replicate.duration_seconds !== null || replicate.agent_run_count > 0
  const timelineOnly = metrics.length === 0 && !hasUsage && !replicate.error
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge className={statusClass(replicate.status, replicate.obsolete)}>{statusLabel(replicate.status, replicate.obsolete)}</Badge>
            {replicate.obsolete && <span className="text-xs text-[color:var(--chart-2)]">This result used an older canvas version.</span>}
          </div>
          {metrics.length > 0 && (
            <section className="space-y-2">
              <h3 className="text-sm font-medium">Outcome</h3>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {metrics.map((key) => <div key={key} className="rounded-md border px-2.5 py-2"><p className="truncate text-xs text-muted-foreground" title={formatMetricLabel(key)}>{formatMetricLabel(key)}</p><p className="mt-0.5 font-medium tabular-nums">{formatMetricValue(key, replicate.metric_values[key])}</p></div>)}
            </div>
            </section>
          )}
          {hasUsage && (
            <section className="space-y-2">
              <h3 className="text-sm font-medium">Usage</h3>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Scorecard label="Estimated cost" value={formatCurrency(replicate.cost_usd)} icon={CircleDollarSign} />
                <Scorecard label="Total tokens" value={formatNumber(replicate.total_tokens)} icon={Coins} />
                <Scorecard label="Duration" value={formatDuration(replicate.duration_seconds)} icon={Clock3} />
                <Scorecard label="Agent calls" value={String(replicate.agent_run_count)} icon={Cpu} />
              </div>
              {replicate.agent_run_count > 0 && (replicate.reported_usage_count < replicate.agent_run_count || replicate.reported_cost_count < replicate.agent_run_count) && <p className="text-xs text-muted-foreground">Usage and cost are shown only where the provider reported them.</p>}
            </section>
          )}
          {replicate.error && <section className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"><h3 className="font-medium">Run error</h3><p className="mt-1 whitespace-pre-wrap break-words">{replicate.error}</p></section>}
          <section className={timelineOnly ? 'flex min-h-[20rem] flex-1 flex-col space-y-2' : 'space-y-2'}>
            <h3 className="text-sm font-medium">Run timeline</h3>
            {replicate.node_runs.length === 0 ? <p className="text-sm text-muted-foreground">No node-level run details are available.</p> : (
              <ol className={timelineOnly ? 'flex-1 space-y-2' : 'space-y-2'}>
              {replicate.node_runs.map((node) => (
                <li key={node.node_id} className="rounded-md border px-3 py-2">
                  <div className="flex items-center justify-between gap-3"><p className="font-medium" title={node.node_id}>{node.node_label}</p><Badge variant="outline" className="capitalize">{node.status}</Badge></div>
                  <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted-foreground">{node.cost_usd !== null && <span>{formatCurrency(node.cost_usd)}</span>}{node.total_tokens !== null && <span>{formatNumber(node.total_tokens)} tokens</span>}</div>
                  {node.error && <p className="mt-2 whitespace-pre-wrap break-words text-xs text-destructive">{node.error}</p>}
                  {node.output_text && <p className="mt-2 max-h-32 overflow-y-auto whitespace-pre-wrap rounded bg-muted/50 p-2 font-mono text-xs">{node.output_text}</p>}
                </li>
              ))}
            </ol>
          )}
        </section>
    </div>
  )
}

export type ResultsSelection =
  | { type: 'cell'; cellLabel: string }
  | { type: 'replicate'; replicateLabel: string }

// This lives alongside the canvas rather than inside the left Results panel.
// Keeping selection in the page lets the left panel remain usable while this
// inspector updates in place for every cell or replicate the user chooses.
export function ResultsInspectorPanel({
  experimentId,
  selection,
  onClose,
}: {
  experimentId: string
  selection: ResultsSelection | null
  onClose: () => void
}) {
  const resultsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'run-results'],
    queryFn: () => experimentsApi.getRunResults(experimentId),
    enabled: selection !== null,
    refetchInterval: 5000,
  })
  if (!selection) return null

  const replicate = selection.type === 'replicate'
    ? resultsQuery.data?.replicates.find((candidate) => candidate.replicate_label === selection.replicateLabel) ?? null
    : null
  const cell = selection.type === 'cell'
    ? resultsQuery.data?.cells.find((candidate) => candidate.cell_label === selection.cellLabel) ?? null
    : null
  const title = replicate ? `Replicate ${replicate.replicate_number}` : 'Cell results'
  const description = replicate
    ? factorSummary(replicate.factor_values)
    : cell
      ? factorSummary(cell.factor_values)
      : undefined

  return (
    <aside className="absolute inset-0 z-20 flex w-full flex-col border-l bg-card shadow-xl" aria-label="Result details">
      <div className="flex min-h-11 shrink-0 items-start justify-between gap-3 border-b px-3 py-2.5">
        <div className="min-w-0"><h2 className="truncate text-sm font-medium">{title}</h2>{description && <p className="mt-0.5 truncate text-xs text-muted-foreground" title={description}>{description}</p>}</div>
        <Button variant="ghost" size="icon-sm" className="shrink-0" aria-label="Close result details" title="Close result details" onClick={onClose}><X className="size-4" /></Button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-3">
        {resultsQuery.isLoading ? <div className="space-y-3"><Skeleton className="h-20 w-full" /><Skeleton className="h-36 w-full" /></div>
          : resultsQuery.isError || !resultsQuery.data ? <p className="text-sm text-muted-foreground">Could not load these results.</p>
            : replicate ? <ReplicateResultDetail replicate={replicate} metricKeys={resultsQuery.data.metric_keys} />
              : cell ? <CellResultSummary cell={cell} metricKeys={resultsQuery.data.metric_keys} />
                : <p className="text-sm text-muted-foreground">This result is no longer available.</p>}
      </div>
    </aside>
  )
}

export function ResultsTab({
  experimentId,
  onSelectResult,
}: {
  experimentId: string
  onSelectResult: (selection: ResultsSelection) => void
}) {
  const [metricPreference, setMetricPreference] = useState<string | null>(null)
  const [expandedResultCells, setExpandedResultCells] = useState<Set<string>>(() => new Set())
  const resultsQuery = useQuery({ queryKey: ['experiments', experimentId, 'run-results'], queryFn: () => experimentsApi.getRunResults(experimentId), refetchInterval: 5000 })
  if (resultsQuery.isLoading) return <div className="space-y-3 p-3"><Skeleton className="h-20 w-full" /><Skeleton className="h-36 w-full" /></div>
  if (resultsQuery.isError || !resultsQuery.data) return <p className="p-3 text-sm text-muted-foreground">Could not load this experiment’s results.</p>

  const { overview, cells, replicates, metric_keys: metricKeys, primary_metric: primaryMetric, primary_metric_direction: primaryMetricDirection } = resultsQuery.data
  const metricKey = metricPreference && metricKeys.includes(metricPreference)
    ? metricPreference
    : primaryMetric && metricKeys.includes(primaryMetric)
      ? primaryMetric
      : metricKeys[0] ?? null
  const sortDirection = metricKey === primaryMetric && primaryMetricDirection === 'minimize' ? -1 : 1
  const sortedCells = [...cells].sort((left, right) => !metricKey
    ? left.cell_label.localeCompare(right.cell_label)
    : sortDirection * ((right.metric_means[metricKey] ?? -Infinity) - (left.metric_means[metricKey] ?? -Infinity)))
  const replicatesByCell = new Map<string, ResultReplicate[]>()
  for (const replicate of replicates) {
    const cellReplicates = replicatesByCell.get(replicate.cell_label) ?? []
    cellReplicates.push(replicate)
    replicatesByCell.set(replicate.cell_label, cellReplicates)
  }
  for (const cellReplicates of replicatesByCell.values()) cellReplicates.sort((left, right) => left.replicate_number - right.replicate_number)
  if (overview.total_replicates === 0) return <p className="p-3 text-sm text-muted-foreground">Generate a design and run a replicate to see results here.</p>

  return (
    <div className="space-y-4 p-3">
      <section>
        <div className="grid grid-cols-2 gap-2">
          <Scorecard label="Current results" value={`${overview.completed_replicates}/${overview.total_replicates}`} note={`${overview.running_replicates} running · ${overview.failed_replicates} failed`} icon={ChevronRight} />
          <Scorecard label="Estimated spend" value={formatCurrency(overview.total_cost_usd)} note={overview.agent_run_count ? `${overview.reported_cost_count}/${overview.agent_run_count} calls reported` : 'No agent calls yet'} icon={CircleDollarSign} />
          <Scorecard label="Total tokens" value={formatNumber(overview.total_tokens)} note={overview.agent_run_count ? `${overview.reported_usage_count}/${overview.agent_run_count} calls reported` : 'No agent calls yet'} icon={Coins} />
          <Scorecard label="Run time" value={formatDuration(overview.total_duration_seconds)} note="Across current results" icon={Clock3} />
        </div>
        {overview.obsolete_replicates > 0 && <div className="mt-2 flex gap-2 rounded-md border border-[color:var(--chart-4)]/50 bg-[color:var(--chart-4)]/10 px-2.5 py-2 text-xs text-foreground"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-[color:var(--chart-4)]" /><span>{overview.obsolete_replicates} result{overview.obsolete_replicates === 1 ? '' : 's'} ran against an older canvas version and are excluded from current totals.</span></div>}
      </section>
      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2"><div><h2 className="text-sm font-medium">Cell comparison</h2><p className="text-xs text-muted-foreground">Average result across current replicates{metricKey === primaryMetric ? ` · ${primaryMetricDirection === 'minimize' ? 'lower is better' : 'higher is better'}` : ''}</p></div>{metricKeys.length > 0 && <Select value={metricKey ?? undefined} onValueChange={(value) => setMetricPreference(value ?? null)}><SelectTrigger size="sm" aria-label="Choose comparison metric"><SelectValue>{(value) => formatMetricLabel(value ?? '')}</SelectValue></SelectTrigger><SelectContent>{metricKeys.map((key) => <SelectItem key={key} value={key}>{formatMetricLabel(key)}</SelectItem>)}</SelectContent></Select>}</div>
        {metricKeys.length === 0 ? <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">No numeric metrics have been reported yet. Run details and provider usage are still available below.</p> : <div className="space-y-2">{sortedCells.map((cell) => <CellCard key={cell.cell_label} cell={cell} metricKey={metricKey} />)}</div>}
      </section>
      <section className="space-y-2">
        <div><h2 className="text-sm font-medium">Cell results</h2><p className="text-xs text-muted-foreground">Expand a cell to inspect its replicates, metrics, usage, outputs, and node activity.</p></div>
        <div className="space-y-2">
          {sortedCells.map((cell) => {
            const expanded = expandedResultCells.has(cell.cell_label)
            const cellReplicates = replicatesByCell.get(cell.cell_label) ?? []
            const replicateListId = `result-cell-${cell.cell_label}-replicates`
            const metric = metricKey ? cell.metric_means[metricKey] : undefined
            const currentMetricValues = cellReplicates
              .filter((replicate) => !replicate.obsolete)
              .map((replicate) => numericMetricValue(replicate, metricKey))
              .filter((value): value is number => value !== null)
            const replicateMetricMean = currentMetricValues.length > 0
              ? currentMetricValues.reduce((sum, value) => sum + value, 0) / currentMetricValues.length
              : null
            const replicateMetricDeviation = replicateMetricMean !== null && currentMetricValues.length > 1
              ? Math.sqrt(currentMetricValues.reduce((sum, value) => sum + (value - replicateMetricMean) ** 2, 0) / currentMetricValues.length)
              : null
            const orderedReplicates = [...cellReplicates].sort((left, right) => {
              const leftValue = left.obsolete ? null : numericMetricValue(left, metricKey)
              const rightValue = right.obsolete ? null : numericMetricValue(right, metricKey)
              if (leftValue !== null && rightValue !== null) return sortDirection * (rightValue - leftValue)
              if (leftValue !== null) return -1
              if (rightValue !== null) return 1
              return left.replicate_number - right.replicate_number
            })
            return (
              <div key={cell.cell_label} className="overflow-hidden rounded-md border">
                <div className="flex items-center gap-2 px-3 py-2.5">
                  <button
                    type="button"
                    onClick={() => setExpandedResultCells((current) => {
                      const next = new Set(current)
                      if (next.has(cell.cell_label)) next.delete(cell.cell_label)
                      else next.add(cell.cell_label)
                      return next
                    })}
                    aria-expanded={expanded}
                    aria-controls={replicateListId}
                    className="flex min-w-0 flex-1 items-center gap-3 text-left transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <ChevronDown className={`size-4 shrink-0 text-muted-foreground transition-transform ${expanded ? '' : '-rotate-90'}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="min-w-0 flex-1 truncate text-sm font-medium" title={factorSummary(cell.factor_values)}>{factorSummary(cell.factor_values)}</p>
                        <Badge variant="outline" className="shrink-0 border-[color:var(--chart-2)] text-[color:var(--chart-2)]">{cell.replicate_count} {cell.replicate_count === 1 ? 'replicate' : 'replicates'}</Badge>
                        {cell.obsolete_count > 0 && <AlertTriangle className="size-4 shrink-0 text-[color:var(--chart-4)]" aria-label="Includes obsolete results" />}
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">{cell.current_completed_count}/{cell.replicate_count} current complete</p>
                    </div>
                    {metric !== undefined && <span className="shrink-0 text-sm font-medium tabular-nums">{formatMetricValue(metricKey!, metric)}</span>}
                  </button>
                  <Button variant="outline" size="xs" className="shrink-0" onClick={() => onSelectResult({ type: 'cell', cellLabel: cell.cell_label })}>View results</Button>
                </div>
                {expanded && (
                  <div id={replicateListId} className="border-t bg-muted/20 px-3 py-2.5">
                    {cellReplicates.length === 0 ? <p className="text-sm text-muted-foreground">No replicate results are available for this cell.</p> : (
                      <>
                        {metricKey && replicateMetricMean !== null && (
                          <div className="mb-2 rounded-md border bg-background px-2.5 py-2">
                            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                              <p className="text-xs font-medium">Replicate comparison · {formatMetricLabel(metricKey)}</p>
                              <p className="text-xs text-muted-foreground">Current results only</p>
                            </div>
                            <p className="mt-1 text-xs tabular-nums text-muted-foreground">Mean <span className="font-medium text-foreground">{formatMetricValue(metricKey, replicateMetricMean)}</span> · Range <span className="font-medium text-foreground">{formatMetricValue(metricKey, Math.min(...currentMetricValues))}–{formatMetricValue(metricKey, Math.max(...currentMetricValues))}</span>{replicateMetricDeviation !== null && <> · SD <span className="font-medium text-foreground">{formatMetricValue(metricKey, replicateMetricDeviation)}</span></>}</p>
                          </div>
                        )}
                        <ul className="space-y-1.5" aria-label={`Replicate results for ${factorSummary(cell.factor_values)}`}>
                        {orderedReplicates.map((replicate) => (
                          <li key={replicate.replicate_label}>
                            <button type="button" onClick={() => onSelectResult({ type: 'replicate', replicateLabel: replicate.replicate_label })} className="flex w-full items-center gap-2 rounded-md border bg-background px-2.5 py-2 text-left transition-colors hover:bg-muted/50">
                              <div className="min-w-0 flex-1"><p className="text-sm font-medium">Replicate {replicate.replicate_number}</p><p className="mt-0.5 text-xs text-muted-foreground">{replicate.cost_usd !== null ? formatCurrency(replicate.cost_usd) : 'Cost not reported'}{replicate.duration_seconds !== null ? ` · ${formatDuration(replicate.duration_seconds)}` : ''}</p></div>
                              <div className="flex shrink-0 items-center gap-2">{numericMetricValue(replicate, metricKey) !== null && <span className="text-xs font-medium tabular-nums">{formatMetricValue(metricKey!, numericMetricValue(replicate, metricKey)!)}</span>}<Badge className={statusClass(replicate.status, replicate.obsolete)}>{statusLabel(replicate.status, replicate.obsolete)}</Badge><ExternalLink className="size-3.5 text-muted-foreground" /></div>
                            </button>
                          </li>
                        ))}
                        </ul>
                      </>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </section>
    </div>
  )
}
