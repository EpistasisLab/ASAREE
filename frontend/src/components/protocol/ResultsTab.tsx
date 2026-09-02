import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ChevronRight, CircleDollarSign, Clock3, Coins, Cpu, ExternalLink } from 'lucide-react'
import { experimentsApi } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
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

function ReplicateDetailDialog({ replicate, metricKeys, onOpenChange }: {
  replicate: ResultReplicate | null
  metricKeys: string[]
  onOpenChange: (open: boolean) => void
}) {
  if (!replicate) return null
  const metrics = metricKeys.filter((key) => typeof replicate.metric_values[key] === 'number')
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Replicate {replicate.replicate_number}</DialogTitle>
          <DialogDescription>{factorSummary(replicate.factor_values)}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap items-center gap-2">
          <Badge className={statusClass(replicate.status, replicate.obsolete)}>{statusLabel(replicate.status, replicate.obsolete)}</Badge>
          {replicate.obsolete && <span className="text-xs text-[color:var(--chart-2)]">This result used an older canvas version.</span>}
        </div>
        <section className="space-y-2">
          <h3 className="text-sm font-medium">Outcome</h3>
          {metrics.length === 0 ? <p className="text-sm text-muted-foreground">This replicate did not produce numeric metrics.</p> : (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {metrics.map((key) => <div key={key} className="rounded-md border px-2.5 py-2"><p className="truncate text-xs text-muted-foreground" title={formatMetricLabel(key)}>{formatMetricLabel(key)}</p><p className="mt-0.5 font-medium tabular-nums">{formatMetricValue(key, replicate.metric_values[key])}</p></div>)}
            </div>
          )}
        </section>
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
        {replicate.error && <section className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"><h3 className="font-medium">Run error</h3><p className="mt-1 whitespace-pre-wrap break-words">{replicate.error}</p></section>}
        <section className="space-y-2">
          <h3 className="text-sm font-medium">Run timeline</h3>
          {replicate.node_runs.length === 0 ? <p className="text-sm text-muted-foreground">No node-level run details are available.</p> : (
            <ol className="space-y-2">
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
        <div className="flex justify-end"><Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>Close</Button></div>
      </DialogContent>
    </Dialog>
  )
}

export function ResultsTab({
  experimentId,
  initialReplicateLabel,
  onInitialReplicateShown,
}: {
  experimentId: string
  initialReplicateLabel?: string | null
  onInitialReplicateShown?: () => void
}) {
  const [metricPreference, setMetricPreference] = useState<string | null>(null)
  const [selectedReplicate, setSelectedReplicate] = useState<ResultReplicate | null>(null)
  const handledInitialReplicate = useRef<string | null>(null)
  const resultsQuery = useQuery({ queryKey: ['experiments', experimentId, 'run-results'], queryFn: () => experimentsApi.getRunResults(experimentId), refetchInterval: 5000 })
  useEffect(() => {
    if (!initialReplicateLabel || !resultsQuery.data || handledInitialReplicate.current === initialReplicateLabel) return
    const replicate = resultsQuery.data.replicates.find((candidate) => candidate.replicate_label === initialReplicateLabel)
    if (replicate) {
      setSelectedReplicate(replicate)
      onInitialReplicateShown?.()
    }
    handledInitialReplicate.current = initialReplicateLabel
  }, [initialReplicateLabel, onInitialReplicateShown, resultsQuery.data])
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
        <div><h2 className="text-sm font-medium">Replicate results</h2><p className="text-xs text-muted-foreground">Open a replicate to inspect metrics, usage, outputs, and node activity.</p></div>
        <div className="overflow-hidden rounded-md border">
          {replicates.map((replicate, index) => <button key={replicate.replicate_label} type="button" onClick={() => setSelectedReplicate(replicate)} className={`flex w-full items-center gap-2 px-2.5 py-2 text-left transition-colors hover:bg-muted/50 ${index > 0 ? 'border-t' : ''}`}><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">Replicate {replicate.replicate_number} <span className="font-normal text-muted-foreground">· {factorSummary(replicate.factor_values)}</span></p><p className="mt-0.5 text-xs text-muted-foreground">{replicate.cost_usd !== null ? formatCurrency(replicate.cost_usd) : 'Cost not reported'}{replicate.duration_seconds !== null ? ` · ${formatDuration(replicate.duration_seconds)}` : ''}</p></div><div className="flex shrink-0 items-center gap-2">{metricKey && typeof replicate.metric_values[metricKey] === 'number' && <span className="text-xs font-medium tabular-nums">{formatMetricValue(metricKey, replicate.metric_values[metricKey])}</span>}<Badge className={statusClass(replicate.status, replicate.obsolete)}>{statusLabel(replicate.status, replicate.obsolete)}</Badge><ExternalLink className="size-3.5 text-muted-foreground" /></div></button>)}
        </div>
      </section>
      <ReplicateDetailDialog replicate={selectedReplicate} metricKeys={metricKeys} onOpenChange={(open) => { if (!open) setSelectedReplicate(null) }} />
    </div>
  )
}
