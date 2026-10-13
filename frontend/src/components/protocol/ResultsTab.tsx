import { CircleDollarSign, Clock3, Coins, Cpu } from 'lucide-react'
import type { ResultsScope } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { formatMetricLabel, formatMetricValue } from '@/lib/experiment'
import { OBSERVATION_LABELS } from '@/lib/measurementPlan'
import type { EvaluationArtifact, Experiment, MetricObservation, ResultReplicate } from '@/types/experiments'
import { ResultsScorecard as Scorecard } from './ResultsPanel'
import { ResultTimelineNode } from './ResultTimelineNode'
import type { ExecutionContext } from './ExecutionNodeEvidence'
import { ExecutionBrowser } from './ExecutionBrowser'
import { ExecutionInspector } from './ExecutionInspector'

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



type ResultMetricTypes = Record<string, 'number' | 'boolean'>

function formatResultMetricValue(metricKey: string, value: unknown, metricTypes: ResultMetricTypes, individual = false): string {
  if (typeof value !== 'number') return '—'
  if (metricTypes[metricKey] === 'boolean') {
    if (individual) return value === 1 ? 'Yes' : 'No'
    return `${new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 1 }).format(value)}`
  }
  return formatMetricValue(metricKey, value)
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
    {observation.status === 'measured' && <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words font-sans font-medium">{observation.value_truncated ? observation.value_preview : measuredValue}</pre>}
    {observation.value_truncated && <p className="mt-1 text-xs text-muted-foreground">Preview · open complete recorded JSON for the original value.</p>}
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
  if (artifact.payload_truncated) return { title: formatMetricLabel(artifact.artifact_key), content: <><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{artifact.payload_preview}</pre><p className="text-xs text-muted-foreground">Preview · open complete recorded JSON for the original artifact.</p></> }
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

function usageSummarizesMetric(
  key: string,
  result: Pick<ResultReplicate, 'cost_usd' | 'total_tokens' | 'duration_seconds'>,
): boolean {
  return (key === 'cost_usd' && result.cost_usd !== null)
    || (key === 'total_tokens' && result.total_tokens !== null)
    || (key === 'duration_seconds' && result.duration_seconds !== null)
}


export function ReplicateResultDetail({ replicate, metricKeys, metricTypes, executionContext, metricLabels }: {
  replicate: ResultReplicate
  metricKeys: string[]
  metricTypes: ResultMetricTypes
  executionContext?: ExecutionContext
  metricLabels?: Record<string, string>
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
                const observation = observationForMetric(replicate, key, metricLabels?.[key] ?? key)
                return <div key={key} className="rounded-md border px-2.5 py-2">
                  <p className="truncate text-xs text-muted-foreground" title={metricLabels?.[key] ?? formatMetricLabel(key)}>{metricLabels?.[key] ?? formatMetricLabel(key)}</p>
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
              {replicate.node_runs.map((node) => <ResultTimelineNode key={`${replicate.run_id ?? replicate.replicate_label}-${node.node_id}`} node={node} defaultOpen={node.node_id === defaultOpenNodeId} context={executionContext} />)}
            </ol>
          )}
        </section>
    </div>
  )
}


export type ResultsSelection =
  | { type: 'cell'; cellLabel: string; cellId?: string; scope?: ResultsScope }
  | { type: 'replicate'; replicateLabel: string; scope?: ResultsScope }
  | { type: 'row'; rowResultId: string; scope: ResultsScope }
  | { type: 'execution'; slotId: string; scope: ResultsScope }

export function ResultsInspectorPanel(props: {
  experimentId: string; experiment: Experiment; selection: ResultsSelection | null; onClose: () => void
}) { return <ExecutionInspector {...props} /> }

export function ResultsTab({ protocolId, experimentId, experiment, onSelectResult, versionId }: {
  protocolId?: string; experimentId: string; experimentName: string; experiment: Experiment
  onSelectResult: (selection: ResultsSelection) => void; versionId?: string
}) {
  const scope = { protocol_id: protocolId, protocol_revision_id: versionId }
  return <ExecutionBrowser key={JSON.stringify(scope)} experimentId={experimentId} scope={scope}
    experiment={experiment} mode="results" onSelect={onSelectResult} />
}
