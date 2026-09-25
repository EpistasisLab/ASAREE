import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ChevronDown, ChevronRight, CircleDollarSign, Clock3, Coins, Cpu, Download, ExternalLink, Trophy, X } from 'lucide-react'
import { experimentsApi } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { displayFactorLevel, formatMetricLabel, formatMetricValue } from '@/lib/experiment'
import { OBSERVATION_LABELS } from '@/lib/measurementPlan'
import { sanitizeFilename } from '@/lib/utils'
import type { DesignMetric, EvaluationArtifact, Experiment, HistoricalRun, MetricObservation, ObsoleteRun, ResultCell, ResultNodeRun, ResultReplicate, SupersededRun } from '@/types/experiments'
import { InfoTooltip } from './InfoTooltip'
import { ReceivedPromptPanel, RunStepTrace, UnresolvedReferencesNote } from './NodeRunOutputPanel'

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

function factorSummary(
  values: Record<string, unknown>,
  designSpec: Experiment['design_spec'],
  cellLabel?: string | null,
): string {
  const entries = Object.entries(values)
  if (entries.length === 0) return 'No varying factors'
  return entries.map(([name, value]) => `${name.split(':').join(' · ')}: ${displayFactorLevel(designSpec, name, value, cellLabel)}`).join(' · ')
}

function numericMetricValue(replicate: ResultReplicate, metricKey: string | null): number | null {
  if (!metricKey) return null
  const value = replicate.metric_values[metricKey]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

type ResultMetricTypes = Record<string, 'number' | 'boolean'>
type ResultMetricAggregations = Record<string, 'mean' | 'sum'>

function isBinaryMetric(metricKey: string | null, metricTypes: ResultMetricTypes): boolean {
  return metricKey !== null && metricTypes[metricKey] === 'boolean'
}

function formatResultMetricValue(metricKey: string, value: unknown, metricTypes: ResultMetricTypes, individual = false): string {
  if (typeof value !== 'number') return '—'
  if (metricTypes[metricKey] === 'boolean') {
    if (individual) return value === 1 ? 'Yes' : 'No'
    return `${new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 1 }).format(value)}`
  }
  return formatMetricValue(metricKey, value)
}

function binaryMetricNote(metricKey: string, value: number, count: number | undefined, metricTypes: ResultMetricTypes): string | null {
  if (metricTypes[metricKey] !== 'boolean' || !count) return null
  return `${Math.round(value * count)}/${count} passed`
}

function metricAggregationLabel(metricKey: string, metricTypes: ResultMetricTypes, metricAggregations: ResultMetricAggregations): string {
  if (metricTypes[metricKey] === 'boolean') return 'pass rate'
  return metricAggregations[metricKey] === 'sum' ? 'total' : 'average'
}

function metricDisplayLabel(metricKey: string, metricTypes: ResultMetricTypes, metricAggregations: ResultMetricAggregations): string {
  return `${formatMetricLabel(metricKey)} · ${metricAggregationLabel(metricKey, metricTypes, metricAggregations)}`
}

function statusLabel(status: ResultReplicate['status'], obsolete: boolean): string {
  if (obsolete) return 'Obsolete'
  return {
    not_started: 'Not started',
    queued: 'Queued',
    running: 'Running',
    finalizing: 'Finalizing',
    completed: 'Completed',
    failed: 'Failed',
    cancelled: 'Cancelled',
  }[status]
}

function statusClass(status: ResultReplicate['status'], obsolete: boolean): string {
  if (obsolete) return 'border-transparent bg-[color:var(--chart-2)]/10 text-[color:var(--chart-2)]'
  if (status === 'completed') return 'border-transparent bg-[color:var(--chart-3)]/10 text-[color:var(--chart-3)]'
  if (status === 'failed') return 'border-transparent bg-destructive/10 text-destructive'
  if (status === 'running' || status === 'finalizing' || status === 'queued') return 'border-transparent bg-primary/10 text-primary'
  return 'border-transparent bg-muted text-muted-foreground'
}

function evaluationStatusLabel(replicate: ResultReplicate): string | null {
  const status = replicate.metric_evaluation?.status
  if (!status) return null
  return {
    queued: 'Scoring queued',
    running: 'Scoring',
    completed: 'Scored',
    failed: 'Scoring failed',
    skipped: 'Scoring skipped (obsolete)',
  }[status]
}

function observationForMetric(replicate: ResultReplicate, metricId: string | undefined, metricKey: string | null) {
  if (!metricKey) return undefined
  return replicate.metric_observations.find((observation) =>
    (metricId && observation.metric_id === metricId)
    || observation.metric_name === metricKey
    || observation.metric_name.toLocaleLowerCase() === formatMetricLabel(metricKey).toLocaleLowerCase(),
  )
}

function missingObservationSummary(
  replicates: ResultReplicate[],
  metricId: string | undefined,
  metricKey: string | null,
): string | null {
  const counts = new Map<MetricObservation['status'], number>()
  for (const replicate of replicates) {
    if (replicate.obsolete) continue
    const status = observationForMetric(replicate, metricId, metricKey)?.status
    if (status && status !== 'measured') counts.set(status, (counts.get(status) ?? 0) + 1)
  }
  if (counts.size === 0) return null
  return (['unavailable', 'failed', 'timed_out', 'cancelled', 'not_applicable'] as const)
    .filter((status) => counts.has(status))
    .map((status) => `${OBSERVATION_LABELS[status]} (${counts.get(status)})`)
    .join(' · ')
}

function ObservationCard({ observation }: { observation: MetricObservation }) {
  const problem = observation.status === 'failed' || observation.status === 'timed_out'
  const measuredValue = typeof observation.value === 'boolean'
    ? (observation.value ? 'True' : 'False')
    : typeof observation.value === 'number'
      ? formatMetricValue(observation.metric_name, observation.value)
      : typeof observation.value === 'string'
        ? observation.value
        : JSON.stringify(observation.value, null, 2)
  return <Card size="sm" className={`gap-0 px-2.5 py-2 ${problem ? 'border-destructive/30 bg-destructive/5' : 'bg-background'}`}>
    <div className="flex items-start justify-between gap-2"><p className="text-xs font-medium">{observation.metric_name}</p><Badge variant="outline" className={problem ? 'border-destructive/50 text-destructive' : ''}>{OBSERVATION_LABELS[observation.status]}</Badge></div>
    {observation.status === 'measured' && <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words font-sans font-medium">{measuredValue}</pre>}
    {observation.error && <p className={`mt-1 text-xs ${problem ? 'text-destructive' : 'text-muted-foreground'}`}>{observation.error}</p>}
    <p className="mt-1 font-mono text-[11px] text-muted-foreground">{observation.producer.producer_id} · v{observation.producer.version}</p>
  </Card>
}

function ConfusionMatrixArtifact({ artifact }: { artifact: EvaluationArtifact }) {
  const payload = artifact.payload as { labels?: unknown[]; matrix?: unknown[][] }
  const labels = Array.isArray(payload?.labels) ? payload.labels : []
  const matrix = Array.isArray(payload?.matrix) ? payload.matrix : []
  return <div className="overflow-x-auto"><table className="text-xs"><thead><tr><th className="px-2 py-1 text-left text-muted-foreground">Actual ↓ / Predicted →</th>{labels.map((label, index) => <th key={index} className="px-2 py-1 text-right font-medium">{String(label)}</th>)}</tr></thead><tbody>{matrix.map((row, rowIndex) => <tr key={rowIndex} className="border-t"><th className="px-2 py-1 text-left font-medium">{String(labels[rowIndex] ?? rowIndex)}</th>{row.map((value, columnIndex) => <td key={columnIndex} className="px-2 py-1 text-right font-mono tabular-nums">{String(value)}</td>)}</tr>)}</tbody></table></div>
}

function PerClassArtifact({ artifact }: { artifact: EvaluationArtifact }) {
  const rows = Array.isArray(artifact.payload) ? artifact.payload.filter((row): row is Record<string, unknown> => !!row && typeof row === 'object') : []
  return <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr className="text-muted-foreground"><th className="px-2 py-1 text-left">Class</th><th className="px-2 py-1 text-right">Precision</th><th className="px-2 py-1 text-right">Recall</th><th className="px-2 py-1 text-right">F1</th><th className="px-2 py-1 text-right">Support</th></tr></thead><tbody>{rows.map((row, index) => <tr key={index} className="border-t"><td className="px-2 py-1">{String(row.class ?? row.label ?? index)}</td>{['precision', 'recall', 'f1', 'support'].map((key) => <td key={key} className="px-2 py-1 text-right font-mono tabular-nums">{String(row[key] ?? '—')}</td>)}</tr>)}</tbody></table></div>
}

function CalibrationArtifact({ artifact }: { artifact: EvaluationArtifact }) {
  const payload = artifact.payload as { fraction_positive?: unknown[]; mean_predicted_probability?: unknown[]; threshold?: unknown; positive_class?: unknown }
  const observed = Array.isArray(payload?.fraction_positive) ? payload.fraction_positive : []
  const predicted = Array.isArray(payload?.mean_predicted_probability) ? payload.mean_predicted_probability : []
  return <div><p className="text-xs text-muted-foreground">Observed frequency by predicted-probability bin{payload?.positive_class !== undefined ? ` for class ${String(payload.positive_class)}` : ''}.</p>{observed.length > 0 && <div className="mt-2 overflow-x-auto"><table className="text-xs"><thead><tr className="text-muted-foreground"><th className="px-2 py-1 text-left">Bin</th><th className="px-2 py-1 text-right">Predicted</th><th className="px-2 py-1 text-right">Observed</th></tr></thead><tbody>{observed.map((value, index) => <tr key={index} className="border-t"><td className="px-2 py-1">{index + 1}</td><td className="px-2 py-1 text-right font-mono">{String(predicted[index] ?? '—')}</td><td className="px-2 py-1 text-right font-mono">{String(value)}</td></tr>)}</tbody></table></div>}</div>
}

function artifactPresentation(artifact: EvaluationArtifact) {
  const kind = artifact.kind || artifact.artifact_key
  if (kind === 'confusion_matrix') return { title: 'Confusion matrix', content: <ConfusionMatrixArtifact artifact={artifact} /> }
  if (kind === 'binary_calibration_curve' || kind === 'calibration') return { title: 'Calibration', content: <CalibrationArtifact artifact={artifact} /> }
  if (kind === 'per_class_classification_report' || kind === 'per_class') return { title: 'Per-class report', content: <PerClassArtifact artifact={artifact} /> }
  return { title: formatMetricLabel(artifact.artifact_key), content: <pre className="overflow-x-auto text-xs">{JSON.stringify(artifact.payload, null, 2)}</pre> }
}

function ArtifactCard({ artifact }: { artifact: EvaluationArtifact }) {
  const presentation = artifactPresentation(artifact)
  return <Card size="sm" className="gap-0 bg-background p-2.5"><div className="mb-2 flex items-center justify-between gap-2"><h4 className="text-xs font-medium">{presentation.title}</h4><span className="font-mono text-[11px] text-muted-foreground">{artifact.producer.producer_id} · v{artifact.producer.version}</span></div>{presentation.content}</Card>
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

function usageSummarizesMetric(
  key: string,
  result: Pick<ResultCell, 'cost_usd' | 'total_tokens' | 'duration_seconds'>,
): boolean {
  return (key === 'cost_usd' && result.cost_usd !== null)
    || (key === 'total_tokens' && result.total_tokens !== null)
    || (key === 'duration_seconds' && result.duration_seconds !== null)
}

function CellResultSummary({ cell, metricKeys, metricTypes, metricAggregations }: { cell: ResultCell; metricKeys: string[]; metricTypes: ResultMetricTypes; metricAggregations: ResultMetricAggregations }) {
  const metrics = metricKeys.filter((key) => typeof cell.metric_means[key] === 'number' && !usageSummarizesMetric(key, cell))
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
          <h3 className="flex items-center gap-1 text-xs font-medium text-muted-foreground">Outcome metrics<InfoTooltip>Each metric is labeled with its cell aggregation: average, total, or pass rate for Boolean outcomes.</InfoTooltip></h3>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {metrics.map((key) => (
            <div key={key} className="rounded-md border bg-background px-2.5 py-2 transition-colors hover:border-primary/30">
              <p className="truncate text-[11px] text-muted-foreground" title={metricDisplayLabel(key, metricTypes, metricAggregations)}>{metricDisplayLabel(key, metricTypes, metricAggregations)}</p>
              <p className="mt-1 text-base font-semibold tabular-nums">{formatResultMetricValue(key, cell.metric_means[key], metricTypes)}</p>
              {binaryMetricNote(key, cell.metric_means[key], cell.metric_counts[key], metricTypes) && <p className="mt-0.5 text-[11px] text-muted-foreground">{binaryMetricNote(key, cell.metric_means[key], cell.metric_counts[key], metricTypes)}</p>}
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
            {cell.duration_seconds !== null && <div className="rounded-md bg-muted/40 px-2 py-1.5"><p className="text-muted-foreground">Duration</p><p className="mt-0.5 font-medium tabular-nums text-foreground">{formatDuration(cell.duration_seconds)}</p></div>}
            {cell.total_tokens !== null && <div className="rounded-md bg-muted/40 px-2 py-1.5"><p className="text-muted-foreground">Tokens</p><p className="mt-0.5 font-medium tabular-nums text-foreground">{formatNumber(cell.total_tokens)}</p></div>}
          </div>
        </section>
      )}
      {metrics.length === 0 && !hasUsage && <p className="mt-4 rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground">No current numeric results or provider usage have been reported yet.</p>}
    </section>
  )
}

function ReplicateTimelineNode({ node, defaultOpen = false }: { node: ResultNodeRun; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
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
          {/* Received before produced, so one node reads as the handoff it
              was: what it was given, then what it made of it. */}
          {node.agent_run_id && <ReceivedPromptPanel runId={node.agent_run_id} />}
          <UnresolvedReferencesNote names={node.unresolved_reference_labels ?? []} />
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

function ReplicateResultDetail({ replicate, metricKeys, metricTypes }: {
  replicate: ResultReplicate
  metricKeys: string[]
  metricTypes: ResultMetricTypes
}) {
  const metrics = metricKeys.filter((key) => {
    if (typeof replicate.metric_values[key] !== 'number') return false
    return !usageSummarizesMetric(key, replicate)
  })
  const summarizedObservations = new Set(
    metrics
      .map((key) => observationForMetric(replicate, undefined, key))
      .filter((observation): observation is MetricObservation => observation?.status === 'measured'),
  )
  const detailedObservations = replicate.metric_observations.filter((observation) => !summarizedObservations.has(observation))
  const hasUsage = replicate.cost_usd !== null || replicate.total_tokens !== null || replicate.duration_seconds !== null || replicate.agent_run_count > 0
  const timelineOnly = metrics.length === 0 && !hasUsage && !replicate.error
  const agentNodes = replicate.node_runs.filter((node) => node.agent_run_id)
  const defaultOpenNodeId = agentNodes.length === 1 ? agentNodes[0].node_id : null
  return (
    <div className="@container flex min-h-0 flex-1 flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge className={statusClass(replicate.status, replicate.obsolete)}>{statusLabel(replicate.status, replicate.obsolete)}</Badge>
            {evaluationStatusLabel(replicate) && <Badge variant="outline" className={replicate.metric_evaluation?.status === 'failed' ? 'border-destructive/50 text-destructive' : ''}>{evaluationStatusLabel(replicate)}</Badge>}
            {replicate.obsolete && <span className="text-xs text-[color:var(--chart-2)]">This result used an older canvas version.</span>}
          </div>
          {replicate.metric_evaluation?.status === 'failed' && replicate.metric_evaluation.error && <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">Scoring failed: {replicate.metric_evaluation.error}</p>}
          {metrics.length > 0 && (
            <section className="space-y-2">
              <h3 className="text-sm font-medium">Outcome</h3>
            <div className="grid grid-cols-2 gap-2 @lg:grid-cols-3">
              {metrics.map((key) => {
                const observation = observationForMetric(replicate, undefined, key)
                return <div key={key} className="rounded-md border px-2.5 py-2">
                  <p className="truncate text-xs text-muted-foreground" title={formatMetricLabel(key)}>{formatMetricLabel(key)}</p>
                  <p className="mt-0.5 font-medium tabular-nums">{formatResultMetricValue(key, replicate.metric_values[key], metricTypes, true)}</p>
                  {observation?.status === 'measured' && <p className="mt-1 font-mono text-[11px] text-muted-foreground">{observation.producer.producer_id} · v{observation.producer.version}</p>}
                </div>
              })}
            </div>
            </section>
          )}
          {detailedObservations.length > 0 && <section className="space-y-2"><h3 className="text-sm font-medium">Measurement observations</h3><div className="grid gap-2 @lg:grid-cols-2">{detailedObservations.map((observation) => <ObservationCard key={observation.metric_id} observation={observation} />)}</div></section>}
          {(replicate.legacy_values ?? []).length > 0 && <section className="space-y-2"><h3 className="text-sm font-medium">Legacy values</h3><p className="text-xs text-muted-foreground">The original producers were not recorded, so these values are shown without inferred provenance and are not ranked.</p><div className="grid gap-2 @lg:grid-cols-2">{replicate.legacy_values!.map((item) => <div key={item.metric_id} className="rounded-md border px-2.5 py-2"><p className="truncate text-xs text-muted-foreground">{item.metric_name}</p><pre className="mt-1 overflow-x-auto text-xs">{typeof item.value === 'string' ? item.value : JSON.stringify(item.value, null, 2)}</pre><Badge variant="outline" className="mt-2">Legacy · producer unknown</Badge></div>)}</div></section>}
          {replicate.evaluation_artifacts.length > 0 && <section className="space-y-2"><h3 className="text-sm font-medium">Evaluation artifacts</h3><div className="space-y-2">{replicate.evaluation_artifacts.map((artifact) => <ArtifactCard key={`${artifact.artifact_key}-${artifact.attempt_id}`} artifact={artifact} />)}</div></section>}
          {hasUsage && (
            <section className="space-y-2">
              <h3 className="text-sm font-medium">Usage</h3>
              <div className="grid grid-cols-2 gap-2 @lg:grid-cols-4">
                <Scorecard label="Estimated cost" help="Provider-reported or estimated cost for this replicate’s Agent calls." value={formatCurrency(replicate.cost_usd)} icon={CircleDollarSign} />
                <Scorecard label="Duration" help="Wall-clock time from the protocol run starting to it finishing." value={formatDuration(replicate.duration_seconds)} icon={Clock3} />
                <Scorecard label="Total tokens" help="Input and output tokens reported by the provider for this replicate." value={formatNumber(replicate.total_tokens)} icon={Coins} />
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
              {replicate.node_runs.map((node) => <ReplicateTimelineNode key={`${replicate.run_id ?? replicate.replicate_label}-${node.node_id}`} node={node} defaultOpen={node.node_id === defaultOpenNodeId} />)}
            </ol>
          )}
        </section>
    </div>
  )
}

function historicalRunAsReplicate(replicate: ResultReplicate, historicalRun: HistoricalRun): ResultReplicate {
  // The shared detail component needs the stable replicate identity/factors,
  // while the historical run contributes the immutable execution facts.
  return {
    ...replicate,
    ...historicalRun,
    metric_values: historicalRun.metric_values ?? {},
    metric_evaluation: historicalRun.metric_evaluation,
    obsolete_runs: [],
    superseded_runs: [],
  }
}

function latestObsoleteRun(replicate: ResultReplicate | null): ObsoleteRun | null {
  if (!replicate?.obsolete || !replicate.run_id) return null
  return {
    run_id: replicate.run_id,
    metric_values: replicate.metric_values,
    metric_evaluation: replicate.metric_evaluation,
    metric_observations: replicate.metric_observations,
    evaluation_artifacts: replicate.evaluation_artifacts,
    legacy_values: replicate.legacy_values,
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

function resultsMetricKey(metric: DesignMetric): string {
  return metric.kind === 'runtime' && metric.catalogKey ? metric.catalogKey : metric.name
}

function orderedResultsMetricKeys(metricKeys: string[], experiment: Experiment): string[] {
  const available = new Set(metricKeys)
  const designMetricsById = new Map((experiment.design_spec?.metrics ?? []).map((metric) => [metric.id, metric]))
  const declared = (experiment.measurement_plan?.metrics ?? []).flatMap((definition) => {
    const designMetric = designMetricsById.get(definition.id)
    const key = designMetric ? resultsMetricKey(designMetric) : definition.name
    return available.has(key) ? [key] : []
  })
  const declaredKeys = new Set(declared)
  return [...declared, ...metricKeys.filter((key) => !declaredKeys.has(key))]
}

// This lives alongside the canvas rather than inside the left Results panel.
// Keeping selection in the page lets the left panel remain usable while this
// inspector updates in place for every cell or replicate the user chooses.
export function ResultsInspectorPanel({
  experimentId,
  experiment,
  selection,
  onClose,
}: {
  experimentId: string
  experiment: Experiment
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
    ? factorSummary(replicate.factor_values, experiment.design_spec, replicate.cell_label)
    : cell
      ? factorSummary(cell.factor_values, experiment.design_spec, cell.cell_label)
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
  const supersededRuns: SupersededRun[] = Array.isArray(replicate?.superseded_runs) ? replicate.superseded_runs : []
  const metricKeys = resultsQuery.data ? orderedResultsMetricKeys(resultsQuery.data.metric_keys, experiment) : []

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
                  <TabsTrigger value="previous" className="px-3 data-active:border-primary/30 data-active:bg-primary data-active:text-primary-foreground">Prior attempts{supersededRuns.length > 0 ? ` (${supersededRuns.length})` : ''}</TabsTrigger>
                </TabsList>
                <TabsContent value="current" className="mt-3 flex min-h-0 flex-1 flex-col">
                  {replicate.requires_current_attempt ? (
                    <p className="text-sm text-muted-foreground">No result has run against the current canvas version yet. Run this replicate to create one.</p>
                  ) : (
                    <ReplicateResultDetail replicate={replicate} metricKeys={metricKeys} metricTypes={resultsQuery.data.metric_types} />
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
                              <ReplicateResultDetail replicate={historicalRunAsReplicate(replicate, historicalRun)} metricKeys={orderedResultsMetricKeys(Object.keys(historicalRun.metric_values ?? {}), experiment)} metricTypes={resultsQuery.data.metric_types} />
                            </div>
                          )}
                        </section>
                        )
                      })}
                    </div>
                  )}
                </TabsContent>
                <TabsContent value="previous" className="mt-3 min-h-0 overflow-y-auto">
                  {supersededRuns.length === 0 ? (
                    <p className="text-sm text-muted-foreground">This replicate has no prior attempts on the current canvas version.</p>
                  ) : (
                    <div className="space-y-4">
                      {supersededRuns.map((historicalRun) => {
                        const expanded = expandedObsoleteRuns.has(historicalRun.run_id)
                        const detailId = `superseded-run-${historicalRun.run_id}`
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
                                <span className="block text-sm font-medium">Prior attempt from {new Date(historicalRun.updated_at).toLocaleString()}</span>
                                <span className="mt-0.5 block truncate text-xs text-muted-foreground">Superseded by a later run of this replicate</span>
                              </span>
                              <Badge variant="outline">Superseded</Badge>
                            </button>
                            {expanded && (
                              <div id={detailId} className="mt-3 border-t pt-3">
                                <ReplicateResultDetail replicate={historicalRunAsReplicate(replicate, historicalRun)} metricKeys={orderedResultsMetricKeys(Object.keys(historicalRun.metric_values ?? {}), experiment)} metricTypes={resultsQuery.data.metric_types} />
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
              : cell ? <CellResultSummary cell={cell} metricKeys={metricKeys} metricTypes={resultsQuery.data.metric_types} metricAggregations={resultsQuery.data.metric_aggregations} />
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
  const resultsQuery = useQuery({ queryKey: ['experiments', experimentId, 'run-results'], queryFn: () => experimentsApi.getRunResults(experimentId), refetchInterval: 5000 })
  if (resultsQuery.isLoading) return <div className="space-y-3 p-3"><Skeleton className="h-20 w-full" /><Skeleton className="h-36 w-full" /></div>
  if (resultsQuery.isError || !resultsQuery.data) return <p className="p-3 text-sm text-muted-foreground">Could not load this experiment’s results.</p>

  const { overview, cells, replicates, metric_types: metricTypes, metric_aggregations: metricAggregations, metric_directions: metricDirections, primary_metric: primaryMetric, primary_metric_direction: primaryMetricDirection } = resultsQuery.data
  const metricKeys = orderedResultsMetricKeys(resultsQuery.data.metric_keys, experiment)
  const metricKey = metricPreference && metricKeys.includes(metricPreference)
    ? metricPreference
    : primaryMetric && metricKeys.includes(primaryMetric)
      ? primaryMetric
      : metricKeys[0] ?? null
  const selectedDirection = metricKey ? (metricDirections?.[metricKey] ?? 'neutral') : null
  const selectedMetricId = metricKey ? experiment.design_spec?.metrics?.find((metric) => {
    return resultsMetricKey(metric) === metricKey
  })?.id : undefined
  const rankable = metricKey === primaryMetric && (selectedDirection === 'maximize' || selectedDirection === 'minimize')
  const sortDirection = selectedDirection === 'minimize' ? -1 : 1
  const sortedCells = [...cells].sort((left, right) => {
    if (!metricKey || !rankable) return left.cell_label.localeCompare(right.cell_label)
    const leftValue = left.metric_means[metricKey]
    const rightValue = right.metric_means[metricKey]
    const leftMeasured = typeof leftValue === 'number'
    const rightMeasured = typeof rightValue === 'number'
    if (leftMeasured && rightMeasured) return sortDirection * (rightValue - leftValue)
    if (leftMeasured) return -1
    if (rightMeasured) return 1
    return left.cell_label.localeCompare(right.cell_label)
  })
  const replicatesByCell = new Map<string, ResultReplicate[]>()
  for (const replicate of replicates) {
    const cellReplicates = replicatesByCell.get(replicate.cell_label) ?? []
    cellReplicates.push(replicate)
    replicatesByCell.set(replicate.cell_label, cellReplicates)
  }
  for (const cellReplicates of replicatesByCell.values()) cellReplicates.sort((left, right) => left.replicate_number - right.replicate_number)
  if (overview.total_replicates === 0) return <p className="p-3 text-sm text-muted-foreground">Generate a design and run a replicate to see results here.</p>
  const bestCell = metricKey && rankable ? sortedCells.find((cell) => typeof cell.metric_means[metricKey] === 'number') ?? null : null
  const directionLabel = selectedDirection === 'minimize' ? 'Lowest' : 'Highest'

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

  return (
    <div className="space-y-4 p-3">
      <section className="space-y-2">
        {metricKeys.length > 0 && (
          <div className="flex items-center justify-end gap-2">
            <span className="text-xs text-muted-foreground">Inspect metric</span>
            <Select value={metricKey ?? undefined} onValueChange={(value) => setMetricPreference(value ?? null)}>
              <SelectTrigger size="sm" aria-label="Choose comparison metric" title="Choose the metric used throughout results">
                <SelectValue>{(value) => formatMetricLabel(value ?? '')}</SelectValue>
              </SelectTrigger>
              <SelectContent>{metricKeys.map((key) => <SelectItem key={key} value={key}>{formatMetricLabel(key)}</SelectItem>)}</SelectContent>
            </Select>
          </div>
        )}
        <Card size="sm" className="flex-row items-start justify-between gap-3 border-primary/25 bg-primary/5 p-3">
          {bestCell && metricKey ? (
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 text-xs font-medium text-primary"><Trophy className="size-3.5" /> Best current result</div>
              <p className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{formatResultMetricValue(metricKey, bestCell.metric_means[metricKey], metricTypes)}</p>
              {binaryMetricNote(metricKey, bestCell.metric_means[metricKey], bestCell.metric_counts[metricKey], metricTypes) && <p className="mt-0.5 text-xs text-muted-foreground">{binaryMetricNote(metricKey, bestCell.metric_means[metricKey], bestCell.metric_counts[metricKey], metricTypes)}</p>}
              <p className="mt-1 truncate text-xs text-muted-foreground" title={factorSummary(bestCell.factor_values, experiment.design_spec)}>{directionLabel} {metricDisplayLabel(metricKey, metricTypes, metricAggregations)} · {factorSummary(bestCell.factor_values, experiment.design_spec)}</p>
            </div>
          ) : metricKey ? (
            <div className="min-w-0"><div className="text-xs font-medium text-primary">Comparison only</div><p className="mt-1 text-sm font-medium">{formatMetricLabel(metricKey)}</p><p className="mt-1 text-xs text-muted-foreground">{primaryMetric === null ? 'No primary metric is declared, so Results compares values without ranking cells or declaring a winner.' : metricKey !== primaryMetric ? `${formatMetricLabel(metricKey)} is not the declared primary metric, so Results compares its values without ranking cells or declaring a winner.` : `${formatMetricLabel(metricKey)} has no maximize/minimize direction, so Results compares values without declaring a winner.`}</p></div>
          ) : (
            <div><p className="text-sm font-medium">Results are arriving</p><p className="mt-1 text-xs text-muted-foreground">Complete a run with reported metrics to rank conditions here.</p></div>
          )}
          <div className="flex shrink-0 gap-1.5">
            <Button variant="outline" size="xs" disabled={downloading} onClick={() => void downloadResults()}>{downloading ? 'Preparing…' : <><Download className="size-3" /> Download CSV</>}</Button>
          </div>
        </Card>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <Scorecard label="Replicates" help="Completed replicates out of the current experiment design. Running and failed attempts are shown below." value={`${overview.completed_replicates}/${overview.total_replicates}`} note={`${overview.running_replicates} running · ${overview.failed_replicates} failed`} icon={ChevronRight} />
          <Scorecard label="Cost $ (USD)" help="Total estimated provider cost across current, non-obsolete runs. Some providers may not report a cost." value={formatCurrency(overview.total_cost_usd)} note={overview.agent_run_count ? `${overview.reported_cost_count}/${overview.agent_run_count} calls reported` : 'No agent calls yet'} icon={CircleDollarSign} />
          <Scorecard label="Total tokens" help="Combined input and output tokens reported across current runs. Missing provider usage stays unreported rather than becoming zero." value={formatNumber(overview.total_tokens)} note={overview.agent_run_count ? `${overview.reported_usage_count}/${overview.agent_run_count} calls reported` : 'No agent calls yet'} icon={Coins} />
          <Scorecard label="Run time" help="Total wall-clock duration across current results. Runs may overlap, so this is not elapsed calendar time." value={formatDuration(overview.total_duration_seconds)} note="Across current results" icon={Clock3} />
        </div>
        {overview.obsolete_replicates > 0 && <div className="mt-2 flex gap-2 rounded-md border border-[color:var(--chart-4)]/50 bg-[color:var(--chart-4)]/10 px-2.5 py-2 text-xs text-foreground"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-[color:var(--chart-4)]" /><span>{overview.obsolete_replicates} run{overview.obsolete_replicates === 1 ? '' : 's'} used an older canvas version and are excluded from current totals.</span></div>}
      </section>
      <section className="space-y-2">
        <div><h2 className="flex items-center gap-1 text-sm font-medium">{rankable ? 'Cell ranking' : 'Cell comparison'}<InfoTooltip>{rankable ? 'Cells are ranked by the selected metric using current, non-obsolete replicates only.' : 'Metrics are shown for comparison without ordering cells or naming a winner.'}</InfoTooltip></h2><p className="text-xs text-muted-foreground">Current replicates only{rankable && isBinaryMetric(metricKey, metricTypes) ? ' · ranked by pass rate' : rankable && metricKey === primaryMetric ? ` · ${primaryMetricDirection === 'minimize' ? 'lower is better' : 'higher is better'}` : ''}</p></div>
        {metricKeys.length === 0 ? <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">No scalar metrics are declared or reported yet. Run details and provider usage are still available below.</p> : <div className="overflow-x-auto rounded-md border"><table className="w-full min-w-[34rem] text-left text-xs"><thead className="border-b bg-muted/40 text-muted-foreground"><tr><th className="w-10 px-2.5 py-2 font-medium"><span className="flex items-center gap-1">{rankable ? 'Rank' : '—'}{rankable && <InfoTooltip>Rank among conditions with a measured value for the selected metric.</InfoTooltip>}</span></th><th className="px-2.5 py-2 font-medium"><span className="flex items-center gap-1">Condition<InfoTooltip>The factor levels used for this group of replicates.</InfoTooltip></span></th><th className="px-2.5 py-2 text-right font-medium">{metricKey ? metricDisplayLabel(metricKey, metricTypes, metricAggregations) : ''}</th><th className="px-2.5 py-2 text-right font-medium">Cost</th><th className="px-2.5 py-2 text-right font-medium">Duration</th><th className="px-2.5 py-2 text-right font-medium"><span className="inline-flex items-center gap-1">Runs<InfoTooltip>Completed current replicates out of all generated replicates for this condition.</InfoTooltip></span></th></tr></thead><tbody>{sortedCells.map((cell, index) => { const value = metricKey ? cell.metric_means[metricKey] : undefined; const measured = typeof value === 'number'; const ranked = rankable && measured; const note = measured && metricKey ? binaryMetricNote(metricKey, value, cell.metric_counts[metricKey], metricTypes) : null; const missingSummary = missingObservationSummary(replicatesByCell.get(cell.cell_label) ?? [], selectedMetricId, metricKey); return <tr key={cell.cell_label} className={`border-b last:border-b-0 ${index === 0 && ranked ? 'bg-primary/5' : 'hover:bg-muted/30'}`}><td className="px-2.5 py-2.5 font-medium text-muted-foreground">{ranked ? index + 1 : '—'}</td><td className="max-w-0 px-2.5 py-2.5"><p className="truncate font-medium text-foreground" title={factorSummary(cell.factor_values, experiment.design_spec)}>{factorSummary(cell.factor_values, experiment.design_spec)}</p>{cell.obsolete_count > 0 && <span className="text-[11px] text-[color:var(--chart-4)]">{cell.obsolete_count} obsolete</span>}</td><td className={`px-2.5 py-2.5 text-right font-medium tabular-nums ${index === 0 && ranked ? 'text-primary' : ''}`}>{measured ? <><span>{formatResultMetricValue(metricKey!, value, metricTypes)}</span>{note && <span className="mt-0.5 block text-[10px] font-normal text-muted-foreground">{note}</span>}{missingSummary && <span className="mt-0.5 block text-[10px] font-normal text-[color:var(--chart-4)]">{missingSummary}</span>}</> : <span className="font-normal text-muted-foreground">{missingSummary ?? 'No measured observations'}</span>}</td><td className="px-2.5 py-2.5 text-right tabular-nums text-muted-foreground">{formatCurrency(cell.cost_usd)}</td><td className="px-2.5 py-2.5 text-right tabular-nums text-muted-foreground">{formatDuration(cell.duration_seconds)}</td><td className="px-2.5 py-2.5 text-right tabular-nums text-muted-foreground">{cell.current_completed_count}/{cell.replicate_count}</td></tr> })}</tbody></table></div>}
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
                        <p className="min-w-0 flex-1 truncate text-sm font-medium" title={factorSummary(cell.factor_values, experiment.design_spec)}>{factorSummary(cell.factor_values, experiment.design_spec)}</p>
                        <Badge variant="outline" className="shrink-0 border-[color:var(--chart-2)] text-[color:var(--chart-2)]" title="Total generated replicates for this condition">{cell.replicate_count} {cell.replicate_count === 1 ? 'replicate' : 'replicates'}</Badge>
                        {cell.obsolete_count > 0 && <Badge variant="outline" className="shrink-0 border-[color:var(--chart-4)]/60 text-[color:var(--chart-4)]">{cell.obsolete_count} obsolete {cell.obsolete_count === 1 ? 'run' : 'runs'}</Badge>}
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground"><span className="font-medium text-foreground">{cell.current_completed_count}/{cell.replicate_count}</span> current runs complete</p>
                    </div>
                    {metric !== undefined && <span className="shrink-0 rounded-md border border-primary/20 bg-primary/5 px-2 py-1 text-right"><span className="block max-w-28 truncate text-[10px] text-muted-foreground" title={metricDisplayLabel(metricKey!, metricTypes, metricAggregations)}>{metricDisplayLabel(metricKey!, metricTypes, metricAggregations)}</span><span className="block text-sm font-semibold tabular-nums text-primary">{formatResultMetricValue(metricKey!, metric, metricTypes)}</span>{binaryMetricNote(metricKey!, metric, cell.metric_counts[metricKey!], metricTypes) && <span className="mt-0.5 block text-[10px] text-muted-foreground">{binaryMetricNote(metricKey!, metric, cell.metric_counts[metricKey!], metricTypes)}</span>}</span>}
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
                              <p className="text-xs font-medium">Replicate comparison · {metricDisplayLabel(metricKey, metricTypes, metricAggregations)}</p>
                              <p className="text-xs text-muted-foreground">Current results only</p>
                            </div>
                            {isBinaryMetric(metricKey, metricTypes) ? <p className="mt-1 text-xs tabular-nums text-muted-foreground"><span className="font-medium text-foreground">{Math.round((replicateMetricAverage ?? 0) * currentMetricValues.length)}/{currentMetricValues.length} passed</span> · {formatResultMetricValue(metricKey, replicateMetricAverage, metricTypes)}</p> : <p className="mt-1 text-xs tabular-nums text-muted-foreground">{metricAggregations[metricKey] === 'sum' ? <>Total <span className="font-medium text-foreground">{formatMetricValue(metricKey, replicateMetricTotal)}</span></> : <>Average <span className="font-medium text-foreground">{formatMetricValue(metricKey, replicateMetricAverage)}</span></>} · Range <span className="font-medium text-foreground">{formatMetricValue(metricKey, Math.min(...currentMetricValues))}–{formatMetricValue(metricKey, Math.max(...currentMetricValues))}</span>{replicateMetricDeviation !== null && <> · SD <span className="font-medium text-foreground">{formatMetricValue(metricKey, replicateMetricDeviation)}</span></>}</p>}
                          </div>
                        )}
                        <div className="mb-2 flex items-center justify-between"><p className="text-xs font-medium">Individual replicates</p><p className="text-[11px] text-muted-foreground">Select one for full output</p></div>
                        <ul className="space-y-1.5" aria-label={`Replicate results for ${factorSummary(cell.factor_values, experiment.design_spec)}`}>
                        {orderedReplicates.map((replicate) => {
                          const observation = observationForMetric(replicate, selectedMetricId, metricKey)
                          const value = numericMetricValue(replicate, metricKey)
                          return (
                          <li key={replicate.replicate_label}>
                            <button type="button" onClick={() => onSelectResult({ type: 'replicate', replicateLabel: replicate.replicate_label })} className="flex w-full items-center gap-2 rounded-md border bg-background px-2.5 py-2 text-left transition-colors hover:border-primary/30 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                              <div className="min-w-0 flex-1"><p className="text-sm font-medium">Replicate {replicate.replicate_number}</p><p className="mt-0.5 text-xs text-muted-foreground">{replicate.cost_usd !== null ? formatCurrency(replicate.cost_usd) : 'Cost not reported'}{replicate.duration_seconds !== null ? ` · ${formatDuration(replicate.duration_seconds)}` : ''}</p></div>
                              <div className="flex shrink-0 items-center gap-2">{value !== null && <span className="text-xs font-medium tabular-nums">{formatResultMetricValue(metricKey!, value, metricTypes, true)}</span>}{observation ? <Badge variant="outline" className={observation.status === 'failed' ? 'border-destructive/50 text-destructive' : ''} title={observation.error ?? undefined}>{OBSERVATION_LABELS[observation.status]}</Badge> : evaluationStatusLabel(replicate) && <span className="text-[11px] text-muted-foreground">{evaluationStatusLabel(replicate)}</span>}<Badge className={statusClass(replicate.status, replicate.obsolete)}>{statusLabel(replicate.status, replicate.obsolete)}</Badge><ExternalLink className="size-3.5 text-muted-foreground" /></div>
                            </button>
                          </li>
                        )})}
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
