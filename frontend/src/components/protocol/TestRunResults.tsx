import { AlertTriangle, ChevronRight, CircleDollarSign, Clock3, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { OBSERVATION_LABELS } from '@/lib/measurementPlan'
import { TERMINAL_RUN_STATUSES, nodeRunBadge } from '@/lib/protocolRun'
import type { EvaluationArtifact, MetricObservation } from '@/types/experiments'
import type { TestRun, TestRunResourceUsage } from '@/types/protocols'
import { ConversationTranscript } from './ConversationTranscript'

function outcomeLabel(run: TestRun): string {
  if (run.status === 'finalizing') return 'Calculating metrics…'
  if (run.status === 'pending') return 'Queued'
  if (run.status === 'running') return 'Running'
  if (run.status === 'completed' && run.observations.some((item) => item.status !== 'measured')) return 'Partially evaluated'
  return { completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled', limit_reached: 'Limit reached' }[run.status]
}

function formatDuration(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return 'Unknown'
  if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)} sec`
  return `${(value / 60).toFixed(1)} min`
}

function formatCost(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return 'Unknown'
  return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 4 }).format(value)
}

function formatObservationValue(observation: MetricObservation): string {
  if (observation.status !== 'measured') return OBSERVATION_LABELS[observation.status]
  if (typeof observation.value === 'boolean') return observation.value ? 'Yes' : 'No'
  if (typeof observation.value === 'number') return new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(observation.value)
  if (typeof observation.value === 'string') return observation.value
  return observation.value == null ? '—' : JSON.stringify(observation.value)
}

function staleReason(reasons: TestRun['freshness']['reasons']): string {
  if (reasons.includes('canvas') && reasons.includes('measurement_plan')) return 'The canvas and measurement plan have changed since this Test Run.'
  if (reasons.includes('canvas')) return 'The canvas has changed since this Test Run.'
  return 'The measurement plan has changed since this Test Run.'
}

function ResourceGroup({ label, duration_seconds, cost_usd }: TestRunResourceUsage & { label: string }) {
  return (
    <div className="rounded-lg border bg-background/55 p-2.5">
      <p className="mb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
      <p className="flex items-center gap-1.5 text-xs"><Clock3 className="size-3.5 text-[color:var(--card-accent,var(--primary))]" /> {formatDuration(duration_seconds)}</p>
      <p className="mt-1 flex items-center gap-1.5 text-xs"><CircleDollarSign className="size-3.5 text-[color:var(--card-accent,var(--primary))]" /> {formatCost(cost_usd)}</p>
    </div>
  )
}

function ObservationCard({ observation, artifacts }: { observation: MetricObservation; artifacts: EvaluationArtifact[] }) {
  const isFailed = observation.status === 'failed' || observation.status === 'timed_out'
  const artifactCount = artifacts.filter((artifact) => artifact.producer.binding_id === observation.producer.binding_id).length
  return (
    <article className={`rounded-lg border p-3 ${isFailed ? 'border-destructive/35 bg-destructive/5' : 'bg-background/55'}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-medium">{observation.metric_name}</p>
          <Badge variant="outline" className={isFailed ? 'mt-1 border-destructive/40 text-destructive' : 'mt-1'}>{OBSERVATION_LABELS[observation.status]}</Badge>
        </div>
        <pre className="max-w-[60%] overflow-auto font-mono text-xs font-semibold whitespace-pre-wrap break-words text-[color:var(--card-accent,var(--primary))]">{formatObservationValue(observation)}</pre>
      </div>
      {observation.error && <p className="mt-2 text-xs text-destructive">{observation.error}</p>}
      <details className="mt-2 text-xs text-muted-foreground">
        <summary className="cursor-pointer select-none">Provenance and diagnostics</summary>
        <pre className="mt-2 max-h-40 overflow-auto rounded bg-muted/60 p-2 font-mono text-[11px] whitespace-pre-wrap">{JSON.stringify({ producer: observation.producer, inputs: observation.input_provenance }, null, 2)}</pre>
        {artifactCount > 0 && <p className="mt-2">{artifactCount} linked evaluation {artifactCount === 1 ? 'artifact' : 'artifacts'} shown below.</p>}
      </details>
    </article>
  )
}

export function TestRunResults({ run, onClose, agentNames = new Map(), title = 'Test Run Results' }: { run: TestRun; onClose: () => void; agentNames?: Map<string, string>; title?: string }) {
  const running = !TERMINAL_RUN_STATUSES.has(run.status)
  const nodeRuns = Object.entries(run.execution_summary.node_runs)
  const timestamp = new Date(run.created_at)

  return (
    <Card aria-label={title} className="absolute right-3 bottom-3 z-10 max-h-[calc(100%-1.5rem)] w-[min(44rem,calc(100%-1.5rem))] shadow-xl" size="sm">
      <CardHeader className="border-b">
        <CardTitle className="flex flex-wrap items-center gap-2">
          {title}
          {run.freshness.out_of_date && <Badge className="border-transparent bg-[color:var(--chart-4)]/15 text-[color:var(--chart-4)]">Out of date</Badge>}
        </CardTitle>
        <CardDescription>
          <span className={run.status === 'failed' ? 'text-destructive' : run.status === 'completed' ? 'text-[color:var(--chart-3)]' : 'text-[color:var(--card-accent,var(--primary))]'}>{outcomeLabel(run)}</span>
          {run.tested_published_revision && <> · Published revision {run.tested_published_revision.number} · {Number.isNaN(timestamp.valueOf()) ? run.created_at : timestamp.toLocaleString()}</>}
        </CardDescription>
        <CardAction><Button size="icon" variant="ghost" aria-label="Close Test Run Results" onClick={onClose}><X className="size-4" /></Button></CardAction>
      </CardHeader>
      <CardContent className="min-h-0 space-y-4 overflow-y-auto">
        {run.freshness.out_of_date && (
          <div className="flex gap-2 rounded-lg border border-[color:var(--chart-4)]/40 bg-[color:var(--chart-4)]/5 p-3 text-xs text-[color:var(--chart-4)]">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" /><div><p className="font-medium">Out of date</p><p>{staleReason(run.freshness.reasons)}</p></div>
          </div>
        )}
        {running && run.status !== 'finalizing' && <p className="text-sm text-muted-foreground">Task execution in progress…</p>}
        {run.status === 'finalizing' && <p className="text-sm text-[color:var(--card-accent,var(--primary))]">Task complete. Calculating metrics…</p>}
        {run.error && <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">{run.error}</p>}

        {nodeRuns.length > 0 && (
          <section className="space-y-2"><h3 className="text-sm font-medium">Node progress</h3>
            {nodeRuns.map(([nodeId, nodeRun]) => {
              const badge = nodeRunBadge(nodeRun.status)
              return <details key={nodeId} className="rounded-lg border bg-background/45">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2"><ChevronRight className="size-3.5 text-muted-foreground [[open]>&]:rotate-90" /><span className="font-medium">{agentNames.get(nodeId) ?? nodeId}</span>{badge && <Badge className={`ml-auto ${badge.className}`}>{badge.label}</Badge>}</summary>
                <div className="border-t px-3 py-2 text-xs">{nodeRun.output_text ? <pre className="font-mono whitespace-pre-wrap">{nodeRun.output_text}</pre> : <p className="text-muted-foreground">No output recorded yet.</p>}{nodeRun.error && <p className="mt-2 text-destructive">{nodeRun.error}</p>}</div>
              </details>
            })}
          </section>
        )}

        {run.conversation && <ConversationTranscript conversation={run.conversation} agentNames={agentNames} />}
        {run.observations.length > 0 ? (
          <section className="space-y-2"><h3 className="text-sm font-medium">Metric observations</h3><div className="grid gap-2 sm:grid-cols-2">{run.observations.map((item) => <ObservationCard key={item.metric_id} observation={item} artifacts={run.artifacts} />)}</div></section>
        ) : TERMINAL_RUN_STATUSES.has(run.status) ? <p className="text-sm text-muted-foreground">No runtime metrics were declared.</p> : null}

        {run.artifacts.length > 0 && <details className="rounded-lg border bg-background/45"><summary className="cursor-pointer px-3 py-2 text-sm font-medium">Evaluation artifacts</summary><div className="space-y-2 border-t p-3">{run.artifacts.map((artifact) => <div key={`${artifact.artifact_key}-${artifact.attempt_id}`}><p className="text-xs font-medium">{artifact.artifact_key} <span className="font-normal text-muted-foreground">· producer {artifact.producer.binding_id}</span></p><pre className="mt-1 overflow-auto rounded bg-muted/60 p-2 font-mono text-[11px] whitespace-pre-wrap">{JSON.stringify(artifact.payload, null, 2)}</pre></div>)}</div></details>}

        <section className="space-y-2"><h3 className="text-sm font-medium">Resources</h3><div className="grid grid-cols-3 gap-2"><ResourceGroup label="Task" {...run.resources.task} /><ResourceGroup label="Evaluation" {...run.resources.evaluation} /><ResourceGroup label="Combined" {...run.resources.total} /></div><p className="text-[11px] text-muted-foreground">Unknown cost means the provider did not report pricing; it is never treated as zero.</p></section>
      </CardContent>
    </Card>
  )
}

export function ReopenTestRunResultsButton({ onOpen, refresh, label = 'Test Run Results' }: { onOpen: () => void; refresh: () => void; label?: string }) {
  return <Button size="sm" variant="outline" onClick={() => { onOpen(); refresh() }} title={`Reopen and refresh ${label}`}>{label}</Button>
}
