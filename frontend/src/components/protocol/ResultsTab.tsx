import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronDown, ChevronRight, CircleDollarSign, Clock3, Coins, Cpu, Download, ExternalLink, Trophy, X } from 'lucide-react'
import { experimentsApi } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { displayFactorValue, formatMetricLabel, formatMetricValue } from '@/lib/experiment'
import { sanitizeFilename } from '@/lib/utils'
import type { Experiment, ObsoleteRun, ResultCell, ResultNodeRun, ResultReplicate } from '@/types/experiments'
import { InfoTooltip } from './InfoTooltip'
import { RunStepTrace } from './NodeRunOutputPanel'

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

function evaluationStatusLabel(replicate: ResultReplicate): string | null {
  const status = replicate.metric_evaluation?.status
  if (!status) return null
  return { queued: 'Scoring queued', running: 'Scoring', completed: 'Scored', failed: 'Scoring failed' }[status]
}

function nodeStatusClass(status: string): string {
  if (status === 'completed') return 'border-transparent bg-[color:var(--chart-3)]/10 text-[color:var(--chart-3)]'
  if (status === 'failed' || status === 'cancelled') return 'border-transparent bg-destructive/10 text-destructive'
  if (status === 'running' || status === 'queued') return 'border-transparent bg-primary/10 text-primary'
  return 'border-transparent bg-muted text-muted-foreground'
}

function Scorecard({ label, help, value, note, icon: Icon }: { label: string; help: string; value: string; note?: string; icon: typeof Coins }) {
  return (
    <div className="rounded-md border bg-card px-2.5 py-2 transition-colors hover:bg-muted/30">
      <div className="flex items-center justify-between gap-2 text-muted-foreground">
        <span className="flex items-center gap-1 text-xs">{label}<InfoTooltip>{help}</InfoTooltip></span>
        <Icon className="size-3.5" aria-hidden="true" />
      </div>
      <p className="mt-1 truncate text-base font-medium tabular-nums" title={value}>{value}</p>
      {note && <p className="mt-0.5 text-[11px] text-muted-foreground">{note}</p>}
    </div>
  )
}

function CellResultSummary({ cell, metricKeys }: { cell: ResultCell; metricKeys: string[] }) {
  const metrics = metricKeys.filter((key) => typeof cell.metric_means[key] === 'number')
  const hasUsage = cell.cost_usd !== null || cell.total_tokens !== null || cell.duration_seconds !== null
  return (
    <section className="rounded-lg border bg-card p-3 shadow-sm" aria-label="Cell result summary">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="flex items-center gap-1 text-sm font-medium">Condition summary<InfoTooltip>Metrics are averages over current, non-obsolete replicates in this condition.</InfoTooltip></p>
          <p className="mt-1 text-xs text-muted-foreground">Current replicates only</p>
        </div>
        <Badge variant="outline" className="shrink-0 border-[color:var(--chart-3)]/40 bg-[color:var(--chart-3)]/10 text-[color:var(--chart-3)]" title="Completed current replicates out of the generated replicates for this condition">
          {cell.current_completed_count}/{cell.replicate_count} complete
        </Badge>
      </div>
      {metrics.length > 0 && (
        <section className="mt-4">
          <h3 className="flex items-center gap-1 text-xs font-medium text-muted-foreground">Outcome metrics<InfoTooltip>Each value is the sum of reported numeric scores across current replicates in this condition.</InfoTooltip></h3>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {metrics.map((key) => (
            <div key={key} className="rounded-md border bg-background px-2.5 py-2 transition-colors hover:border-primary/30">
              <p className="truncate text-[11px] text-muted-foreground" title={formatMetricLabel(key)}>{formatMetricLabel(key)}</p>
              <p className="mt-1 text-base font-semibold tabular-nums">{formatMetricValue(key, cell.metric_means[key])}</p>
            </div>
          ))}
          </div>
        </section>
      )}
      {hasUsage && (
        <section className={`${metrics.length > 0 ? 'mt-4 border-t pt-3' : 'mt-4'}`}>
          <h3 className="flex items-center gap-1 text-xs font-medium text-muted-foreground">Usage<InfoTooltip>Totals across current replicates in this condition. Provider telemetry can be unavailable for some calls.</InfoTooltip></h3>
          <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
            {cell.cost_usd !== null && <div className="rounded-md bg-muted/40 px-2 py-1.5"><p className="text-muted-foreground">Cost</p><p className="mt-0.5 font-medium tabular-nums text-foreground">{formatCurrency(cell.cost_usd)}</p></div>}
            {cell.total_tokens !== null && <div className="rounded-md bg-muted/40 px-2 py-1.5"><p className="text-muted-foreground">Tokens</p><p className="mt-0.5 font-medium tabular-nums text-foreground">{formatNumber(cell.total_tokens)}</p></div>}
            {cell.duration_seconds !== null && <div className="rounded-md bg-muted/40 px-2 py-1.5"><p className="text-muted-foreground">Duration</p><p className="mt-0.5 font-medium tabular-nums text-foreground">{formatDuration(cell.duration_seconds)}</p></div>}
          </div>
        </section>
      )}
      {metrics.length === 0 && !hasUsage && <p className="mt-4 rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground">No current numeric results or provider usage have been reported yet.</p>}
    </section>
  )
}

function ReplicateTimelineNode({ node }: { node: ResultNodeRun }) {
  const [open, setOpen] = useState(false)
  const hasDetails = Boolean(node.error || node.output_text || node.agent_run_id)

  return (
    <li className="overflow-hidden rounded-md border bg-card">
      <button
        type="button"
        onClick={() => hasDetails && setOpen((value) => !value)}
        disabled={!hasDetails}
        aria-expanded={hasDetails ? open : undefined}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/40 disabled:cursor-default disabled:hover:bg-transparent"
      >
        {hasDetails ? (
          open ? <ChevronDown className="size-4 shrink-0 text-muted-foreground" /> : <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
        ) : <span className="size-4 shrink-0" />}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium" title={node.node_id}>{node.node_label}</span>
          <span className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
            {node.cost_usd !== null && <span>{formatCurrency(node.cost_usd)}</span>}
            {node.total_tokens !== null && <span>{formatNumber(node.total_tokens)} tokens</span>}
            {!hasDetails && <span>No output details recorded</span>}
          </span>
        </span>
        <Badge variant="outline" className={`shrink-0 capitalize ${nodeStatusClass(node.status)}`}>{node.status}</Badge>
      </button>
      {open && (
        <div className="space-y-4 border-t bg-muted/15 px-3 py-3">
          {node.error && <section className="space-y-1.5"><h4 className="text-xs font-medium">Error</h4><p className="rounded border border-destructive/30 bg-destructive/5 p-2 text-xs whitespace-pre-wrap break-words text-destructive">{node.error}</p></section>}
          {node.output_text ? (
            <section className="space-y-1.5">
              <h4 className="text-xs font-medium">Output</h4>
              <p className="max-h-64 overflow-y-auto rounded border bg-background/70 p-2 font-mono text-xs whitespace-pre-wrap break-words">{node.output_text}</p>
            </section>
          ) : !node.error && <p className="text-xs text-muted-foreground">No output was recorded for this node.</p>}
          {node.agent_run_id && <RunStepTrace runId={node.agent_run_id} />}
        </div>
      )}
    </li>
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
            {evaluationStatusLabel(replicate) && <Badge variant="outline" className={replicate.metric_evaluation?.status === 'failed' ? 'border-destructive/50 text-destructive' : ''}>{evaluationStatusLabel(replicate)}</Badge>}
            {replicate.obsolete && <span className="text-xs text-[color:var(--chart-2)]">This result used an older canvas version.</span>}
          </div>
          {replicate.metric_evaluation?.status === 'failed' && replicate.metric_evaluation.error && <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">Scoring failed: {replicate.metric_evaluation.error}</p>}
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
                <Scorecard label="Estimated cost" help="Provider-reported or estimated spend for this replicate’s Agent calls." value={formatCurrency(replicate.cost_usd)} icon={CircleDollarSign} />
                <Scorecard label="Total tokens" help="Input and output tokens reported by the provider for this replicate." value={formatNumber(replicate.total_tokens)} icon={Coins} />
                <Scorecard label="Duration" help="Wall-clock time from the protocol run starting to it finishing." value={formatDuration(replicate.duration_seconds)} icon={Clock3} />
                <Scorecard label="Agent calls" help="Number of Agent runs recorded while executing this replicate." value={String(replicate.agent_run_count)} icon={Cpu} />
              </div>
              {replicate.agent_run_count > 0 && (replicate.reported_usage_count < replicate.agent_run_count || replicate.reported_cost_count < replicate.agent_run_count) && <p className="text-xs text-muted-foreground">Usage and cost are shown only where the provider reported them.</p>}
            </section>
          )}
          {replicate.error && <section className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"><h3 className="font-medium">Run error</h3><p className="mt-1 whitespace-pre-wrap break-words">{replicate.error}</p></section>}
          <section className={timelineOnly ? 'flex min-h-[20rem] flex-1 flex-col space-y-2' : 'space-y-2'}>
            <h3 className="text-sm font-medium">Run timeline</h3>
            {replicate.node_runs.length === 0 ? <p className="text-sm text-muted-foreground">No node-level run details are available.</p> : (
              <ol className={timelineOnly ? 'min-h-0 flex-1 space-y-2' : 'space-y-2'}>
              {replicate.node_runs.map((node) => <ReplicateTimelineNode key={node.node_id} node={node} />)}
            </ol>
          )}
        </section>
    </div>
  )
}

function historicalRunAsReplicate(replicate: ResultReplicate, historicalRun: ObsoleteRun): ResultReplicate {
  // The shared detail component needs the stable replicate identity/factors,
  // while the historical run contributes the immutable execution facts.
  return {
    ...replicate,
    ...historicalRun,
    metric_values: {},
    obsolete_runs: [],
  }
}

function latestObsoleteRun(replicate: ResultReplicate | null): ObsoleteRun | null {
  if (!replicate?.obsolete || !replicate.run_id) return null
  return {
    run_id: replicate.run_id,
    status: replicate.status,
    obsolete: true,
    error: replicate.error,
    protocol_revision_id: replicate.protocol_revision_id,
    updated_at: replicate.updated_at,
    duration_seconds: replicate.duration_seconds,
    node_runs: replicate.node_runs,
    input_tokens: replicate.input_tokens,
    output_tokens: replicate.output_tokens,
    total_tokens: replicate.total_tokens,
    cost_usd: replicate.cost_usd,
    agent_run_count: replicate.agent_run_count,
    reported_usage_count: replicate.reported_usage_count,
    reported_cost_count: replicate.reported_cost_count,
  }
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
  const [expandedObsoleteRuns, setExpandedObsoleteRuns] = useState<Set<string>>(() => new Set())
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
  // During a rolling frontend/backend restart, accept the prior response name
  // too. The earlier API omitted the latest stale run from its history, so add
  // it locally when necessary rather than hiding a real obsolete result.
  const legacyObsoleteRuns = (replicate as (ResultReplicate & { previous_obsolete_runs?: ObsoleteRun[] }) | null)
    ?.previous_obsolete_runs
  const reportedObsoleteRuns = Array.isArray(replicate?.obsolete_runs)
    ? replicate.obsolete_runs
    : Array.isArray(legacyObsoleteRuns) ? legacyObsoleteRuns : []
  const latestStaleRun = latestObsoleteRun(replicate)
  const obsoleteRuns = latestStaleRun && !reportedObsoleteRuns.some((run) => run.run_id === latestStaleRun.run_id)
    ? [latestStaleRun, ...reportedObsoleteRuns]
    : reportedObsoleteRuns

  return (
    <aside className="absolute inset-0 z-20 flex w-full flex-col border-l bg-card shadow-xl" aria-label="Result details">
      <div className="flex min-h-11 shrink-0 items-start justify-between gap-3 border-b px-3 py-2.5">
        <div className="min-w-0"><h2 className="truncate text-sm font-medium">{title}</h2>{description && <p className="mt-0.5 truncate text-xs text-muted-foreground" title={description}>{description}</p>}</div>
        <Button variant="ghost" size="icon-sm" className="shrink-0" aria-label="Close result details" title="Close result details" onClick={onClose}><X className="size-4" /></Button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-3">
        {resultsQuery.isLoading ? <div className="space-y-3"><Skeleton className="h-20 w-full" /><Skeleton className="h-36 w-full" /></div>
          : resultsQuery.isError || !resultsQuery.data ? <p className="text-sm text-muted-foreground">Could not load these results.</p>
            : replicate ? (
              <Tabs defaultValue="current" className="flex min-h-0 flex-1 flex-col">
                <TabsList className="w-full rounded-md border bg-muted/50 p-1">
                  <TabsTrigger value="current" className="px-3 data-active:border-primary/30 data-active:bg-primary data-active:text-primary-foreground">Current</TabsTrigger>
                  <TabsTrigger value="obsolete" className="px-3 data-active:border-primary/30 data-active:bg-primary data-active:text-primary-foreground">Obsolete{obsoleteRuns.length > 0 ? ` (${obsoleteRuns.length})` : ''}</TabsTrigger>
                </TabsList>
                <TabsContent value="current" className="mt-3 flex min-h-0 flex-1 flex-col">
                  {replicate.obsolete ? (
                    <p className="text-sm text-muted-foreground">No result has run against the current canvas version yet. Run this replicate to create one.</p>
                  ) : (
                    <ReplicateResultDetail replicate={replicate} metricKeys={resultsQuery.data.metric_keys} />
                  )}
                </TabsContent>
                <TabsContent value="obsolete" className="mt-3 min-h-0 overflow-y-auto">
                  {obsoleteRuns.length === 0 ? (
                    <p className="text-sm text-muted-foreground">This replicate has no obsolete runs.</p>
                  ) : (
                    <div className="space-y-4">
                      {obsoleteRuns.map((historicalRun) => {
                        const expanded = expandedObsoleteRuns.has(historicalRun.run_id)
                        const detailId = `obsolete-run-${historicalRun.run_id}`
                        return (
                        <section key={historicalRun.run_id} className="rounded-md border p-3">
                          <button
                            type="button"
                            className="flex w-full items-center gap-2 text-left"
                            aria-expanded={expanded}
                            aria-controls={detailId}
                            onClick={() => setExpandedObsoleteRuns((current) => {
                              const next = new Set(current)
                              if (next.has(historicalRun.run_id)) next.delete(historicalRun.run_id)
                              else next.add(historicalRun.run_id)
                              return next
                            })}
                          >
                            <ChevronDown className={`size-4 shrink-0 text-muted-foreground transition-transform ${expanded ? '' : '-rotate-90'}`} />
                            <span className="min-w-0 flex-1">
                              <span className="block text-sm font-medium">Run from {new Date(historicalRun.updated_at).toLocaleString()}</span>
                              <span className="mt-0.5 block truncate text-xs text-muted-foreground">{historicalRun.protocol_revision_id ? `Canvas revision ${historicalRun.protocol_revision_id.slice(0, 8)}` : 'Earlier canvas version'}</span>
                            </span>
                            <Badge className={statusClass(historicalRun.status, true)}>Obsolete</Badge>
                          </button>
                          {expanded && (
                            <div id={detailId} className="mt-3 border-t pt-3">
                              <ReplicateResultDetail replicate={historicalRunAsReplicate(replicate, historicalRun)} metricKeys={[]} />
                            </div>
                          )}
                        </section>
                        )
                      })}
                    </div>
                  )}
                </TabsContent>
              </Tabs>
            )
              : cell ? <CellResultSummary cell={cell} metricKeys={resultsQuery.data.metric_keys} />
                : <p className="text-sm text-muted-foreground">This result is no longer available.</p>}
      </div>
    </aside>
  )
}

export function ResultsTab({
  experimentId,
  experimentName,
  experiment,
  onSelectResult,
}: {
  experimentId: string
  experimentName: string
  experiment: Experiment
  onSelectResult: (selection: ResultsSelection) => void
}) {
  const [metricPreference, setMetricPreference] = useState<string | null>(null)
  const [expandedResultCells, setExpandedResultCells] = useState<Set<string>>(() => new Set())
  const [downloading, setDownloading] = useState(false)
  const [scoring, setScoring] = useState(false)
  const queryClient = useQueryClient()
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
  const bestCell = metricKey ? sortedCells.find((cell) => typeof cell.metric_means[metricKey] === 'number') ?? null : null
  const directionLabel = primaryMetricDirection === 'minimize' ? 'Lowest' : 'Highest'
  const hasJudgeMetrics = (experiment.design_spec?.metrics ?? []).some((metric) => metric.kind === 'model_judge' && metric.scoring?.method === 'model_judge')

  async function downloadResults() {
    setDownloading(true)
    try {
      const blob = await experimentsApi.downloadRunResultsCsv(experimentId)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${sanitizeFilename(experimentName, 'experiment')}-results.csv`
      link.click()
      URL.revokeObjectURL(url)
    } finally {
      setDownloading(false)
    }
  }

  async function scoreCompletedRuns() {
    setScoring(true)
    try {
      await experimentsApi.scoreCompletedRuns(experimentId)
      await queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'run-results'] })
    } finally {
      setScoring(false)
    }
  }

  return (
    <div className="space-y-4 p-3">
      <section>
        <div className="flex items-start justify-between gap-3 rounded-lg border border-primary/25 bg-primary/5 p-3 shadow-[0_0_20px_-12px_var(--primary)]">
          {bestCell && metricKey ? (
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 text-xs font-medium text-primary"><Trophy className="size-3.5" /> Best current result</div>
              <p className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{formatMetricValue(metricKey, bestCell.metric_means[metricKey])}</p>
              <p className="mt-1 truncate text-xs text-muted-foreground" title={factorSummary(bestCell.factor_values)}>{directionLabel} {formatMetricLabel(metricKey)} · {factorSummary(bestCell.factor_values)}</p>
            </div>
          ) : (
            <div><p className="text-sm font-medium">Results are arriving</p><p className="mt-1 text-xs text-muted-foreground">Complete a run with reported metrics to rank conditions here.</p></div>
          )}
          <div className="flex shrink-0 gap-1.5">
            {hasJudgeMetrics && <Button variant="outline" size="xs" disabled={scoring} onClick={() => void scoreCompletedRuns()}>{scoring ? 'Queueing…' : 'Score completed runs'}</Button>}
            <Button variant="outline" size="xs" disabled={downloading} onClick={() => void downloadResults()}>{downloading ? 'Preparing…' : <><Download className="size-3" /> Download CSV</>}</Button>
          </div>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <Scorecard label="Replicates" help="Completed replicates out of the current experiment design. Running and failed attempts are shown below." value={`${overview.completed_replicates}/${overview.total_replicates}`} note={`${overview.running_replicates} running · ${overview.failed_replicates} failed`} icon={ChevronRight} />
          <Scorecard label="Cost $ (USD)" help="Total estimated provider cost across current, non-obsolete runs. Some providers may not report a cost." value={formatCurrency(overview.total_cost_usd)} note={overview.agent_run_count ? `${overview.reported_cost_count}/${overview.agent_run_count} calls reported` : 'No agent calls yet'} icon={CircleDollarSign} />
          <Scorecard label="Total tokens" help="Combined input and output tokens reported across current runs. Missing provider usage stays unreported rather than becoming zero." value={formatNumber(overview.total_tokens)} note={overview.agent_run_count ? `${overview.reported_usage_count}/${overview.agent_run_count} calls reported` : 'No agent calls yet'} icon={Coins} />
          <Scorecard label="Run time" help="Total wall-clock duration across current results. Runs may overlap, so this is not elapsed calendar time." value={formatDuration(overview.total_duration_seconds)} note="Across current results" icon={Clock3} />
        </div>
        {overview.obsolete_replicates > 0 && <div className="mt-2 flex gap-2 rounded-md border border-[color:var(--chart-4)]/50 bg-[color:var(--chart-4)]/10 px-2.5 py-2 text-xs text-foreground"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-[color:var(--chart-4)]" /><span>{overview.obsolete_replicates} run{overview.obsolete_replicates === 1 ? '' : 's'} used an older canvas version and are excluded from current totals.</span></div>}
      </section>
      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2"><div><h2 className="flex items-center gap-1 text-sm font-medium">Condition ranking<InfoTooltip>Conditions are ranked by the selected metric using current, non-obsolete replicates only.</InfoTooltip></h2><p className="text-xs text-muted-foreground">Current replicates only{metricKey === primaryMetric ? ` · ${primaryMetricDirection === 'minimize' ? 'lower is better' : 'higher is better'}` : ''}</p></div>{metricKeys.length > 0 && <Select value={metricKey ?? undefined} onValueChange={(value) => setMetricPreference(value ?? null)}><SelectTrigger size="sm" aria-label="Choose comparison metric" title="Choose the metric used to rank conditions"><SelectValue>{(value) => formatMetricLabel(value ?? '')}</SelectValue></SelectTrigger><SelectContent>{metricKeys.map((key) => <SelectItem key={key} value={key}>{formatMetricLabel(key)}</SelectItem>)}</SelectContent></Select>}</div>
        {metricKeys.length === 0 ? <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">No numeric metrics have been reported yet. Run details and provider usage are still available below.</p> : <div className="overflow-x-auto rounded-md border"><table className="w-full min-w-[34rem] text-left text-xs"><thead className="border-b bg-muted/40 text-muted-foreground"><tr><th className="w-10 px-2.5 py-2 font-medium"><span className="flex items-center gap-1">Rank<InfoTooltip>Rank among conditions with a reported value for the selected metric.</InfoTooltip></span></th><th className="px-2.5 py-2 font-medium"><span className="flex items-center gap-1">Condition<InfoTooltip>The factor levels used for this group of replicates.</InfoTooltip></span></th><th className="px-2.5 py-2 text-right font-medium">{formatMetricLabel(metricKey ?? '')}</th><th className="px-2.5 py-2 text-right font-medium">Cost</th><th className="px-2.5 py-2 text-right font-medium">Duration</th><th className="px-2.5 py-2 text-right font-medium"><span className="inline-flex items-center gap-1">Runs<InfoTooltip>Completed current replicates out of all generated replicates for this condition.</InfoTooltip></span></th></tr></thead><tbody>{sortedCells.map((cell, index) => { const value = metricKey ? cell.metric_means[metricKey] : undefined; const ranked = typeof value === 'number'; return <tr key={cell.cell_label} className={`border-b last:border-b-0 ${index === 0 && ranked ? 'bg-primary/5' : 'hover:bg-muted/30'}`}><td className="px-2.5 py-2.5 font-medium text-muted-foreground">{ranked ? index + 1 : '—'}</td><td className="max-w-0 px-2.5 py-2.5"><p className="truncate font-medium text-foreground" title={factorSummary(cell.factor_values)}>{factorSummary(cell.factor_values)}</p>{cell.obsolete_count > 0 && <span className="text-[11px] text-[color:var(--chart-4)]">{cell.obsolete_count} obsolete</span>}</td><td className={`px-2.5 py-2.5 text-right font-medium tabular-nums ${index === 0 && ranked ? 'text-primary' : ''}`}>{ranked ? formatMetricValue(metricKey!, value) : '—'}</td><td className="px-2.5 py-2.5 text-right tabular-nums text-muted-foreground">{formatCurrency(cell.cost_usd)}</td><td className="px-2.5 py-2.5 text-right tabular-nums text-muted-foreground">{formatDuration(cell.duration_seconds)}</td><td className="px-2.5 py-2.5 text-right tabular-nums text-muted-foreground">{cell.current_completed_count}/{cell.replicate_count}</td></tr> })}</tbody></table></div>}
      </section>
      <section className="space-y-2">
        <div><h2 className="flex items-center gap-1 text-sm font-medium">Cell results<InfoTooltip>Each card groups replicates that share the same experimental factor levels. Expand one to compare individual runs.</InfoTooltip></h2><p className="text-xs text-muted-foreground">Expand a condition to inspect its replicates, metrics, usage, outputs, and node activity.</p></div>
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
            const replicateMetricTotal = currentMetricValues.length > 0
              ? currentMetricValues.reduce((sum, value) => sum + value, 0)
              : null
            const replicateMetricAverage = replicateMetricTotal !== null ? replicateMetricTotal / currentMetricValues.length : null
            const replicateMetricDeviation = replicateMetricAverage !== null && currentMetricValues.length > 1
              ? Math.sqrt(currentMetricValues.reduce((sum, value) => sum + (value - replicateMetricAverage) ** 2, 0) / currentMetricValues.length)
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
              <div key={cell.cell_label} className={`overflow-hidden rounded-lg border bg-card transition-shadow ${expanded ? 'border-primary/35 shadow-[0_0_18px_-14px_var(--primary)]' : 'hover:border-muted-foreground/30'}`}>
                <div className="flex items-center gap-2 px-3 py-3">
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
                        <Badge variant="outline" className="shrink-0 border-[color:var(--chart-2)] text-[color:var(--chart-2)]" title="Total generated replicates for this condition">{cell.replicate_count} {cell.replicate_count === 1 ? 'replicate' : 'replicates'}</Badge>
                        {cell.obsolete_count > 0 && <Badge variant="outline" className="shrink-0 border-[color:var(--chart-4)]/60 text-[color:var(--chart-4)]">{cell.obsolete_count} obsolete {cell.obsolete_count === 1 ? 'run' : 'runs'}</Badge>}
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground"><span className="font-medium text-foreground">{cell.current_completed_count}/{cell.replicate_count}</span> current runs complete</p>
                    </div>
                    {metric !== undefined && <span className="shrink-0 rounded-md border border-primary/20 bg-primary/5 px-2 py-1 text-right"><span className="block max-w-28 truncate text-[10px] text-muted-foreground" title={formatMetricLabel(metricKey!)}>{formatMetricLabel(metricKey!)}</span><span className="block text-sm font-semibold tabular-nums text-primary">{formatMetricValue(metricKey!, metric)}</span></span>}
                  </button>
                  <Button variant="outline" size="xs" className="shrink-0" title="Open a detailed view of this condition and its run timeline" onClick={() => onSelectResult({ type: 'cell', cellLabel: cell.cell_label })}>View results</Button>
                </div>
                {expanded && (
                  <div id={replicateListId} className="border-t bg-muted/15 px-3 py-3">
                    {cellReplicates.length === 0 ? <p className="text-sm text-muted-foreground">No replicate results are available for this cell.</p> : (
                      <>
                        {metricKey && replicateMetricTotal !== null && (
                          <div className="mb-3 rounded-md border border-primary/20 bg-primary/5 px-2.5 py-2">
                            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                              <p className="text-xs font-medium">Replicate comparison · {formatMetricLabel(metricKey)}</p>
                              <p className="text-xs text-muted-foreground">Current results only</p>
                            </div>
                            <p className="mt-1 text-xs tabular-nums text-muted-foreground">Total <span className="font-medium text-foreground">{formatMetricValue(metricKey, replicateMetricTotal)}</span> · Range <span className="font-medium text-foreground">{formatMetricValue(metricKey, Math.min(...currentMetricValues))}–{formatMetricValue(metricKey, Math.max(...currentMetricValues))}</span>{replicateMetricDeviation !== null && <> · SD <span className="font-medium text-foreground">{formatMetricValue(metricKey, replicateMetricDeviation)}</span></>}</p>
                          </div>
                        )}
                        <div className="mb-2 flex items-center justify-between"><p className="text-xs font-medium">Individual replicates</p><p className="text-[11px] text-muted-foreground">Select one for full output</p></div>
                        <ul className="space-y-1.5" aria-label={`Replicate results for ${factorSummary(cell.factor_values)}`}>
                        {orderedReplicates.map((replicate) => (
                          <li key={replicate.replicate_label}>
                            <button type="button" onClick={() => onSelectResult({ type: 'replicate', replicateLabel: replicate.replicate_label })} className="flex w-full items-center gap-2 rounded-md border bg-background px-2.5 py-2 text-left transition-colors hover:border-primary/30 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                              <div className="min-w-0 flex-1"><p className="text-sm font-medium">Replicate {replicate.replicate_number}</p><p className="mt-0.5 text-xs text-muted-foreground">{replicate.cost_usd !== null ? formatCurrency(replicate.cost_usd) : 'Cost not reported'}{replicate.duration_seconds !== null ? ` · ${formatDuration(replicate.duration_seconds)}` : ''}</p></div>
                              <div className="flex shrink-0 items-center gap-2">{numericMetricValue(replicate, metricKey) !== null && <span className="text-xs font-medium tabular-nums">{formatMetricValue(metricKey!, numericMetricValue(replicate, metricKey)!)}</span>}{evaluationStatusLabel(replicate) && <span className="text-[11px] text-muted-foreground">{evaluationStatusLabel(replicate)}</span>}<Badge className={statusClass(replicate.status, replicate.obsolete)}>{statusLabel(replicate.status, replicate.obsolete)}</Badge><ExternalLink className="size-3.5 text-muted-foreground" /></div>
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
