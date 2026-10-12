import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { experimentsApi, type ResultsScope } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { executionKeys, useExecutionSummary } from '@/lib/executionQueries'
import type { AttemptDetail, ExecutionSlot } from '@/types/executionResults'
import type { Experiment, ResultReplicate, ResultNodeRun } from '@/types/experiments'
import { ReplicateResultDetail, type ResultsSelection } from './ResultsTab'
import { CursorControls, useCursor } from './ExecutionBrowser'
import { ExecutionRows } from './ExecutionBrowser'

export function attemptAsReplicate(slot: ExecutionSlot, detail: AttemptDetail): ResultReplicate {
  const usage = detail.usage
  const observations = detail.attempt_result?.measurement?.observations ?? []
  const metrics = { ...detail.attempt_result?.metric_values }
  for (const observation of observations) if (observation.status === 'measured') metrics[observation.metric_name] = observation.value
  const nodes: ResultNodeRun[] = Object.entries(detail.node_runs).filter(([, node]) =>
    !!node.run_id || !!node.output_text || !!node.error || ['running', 'failed', 'cancelled'].includes(node.status),
  ).map(([id, node]) => ({ node_id: id, node_label: detail.node_labels[id] ?? id, status: node.status,
    bypassed: node.bypassed, output_text: node.output_text ?? null, error: node.error ?? null,
    agent_run_id: node.run_id ?? null, input_tokens: null, output_tokens: null, total_tokens: null, cost_usd: null,
    unresolved_reference_labels: (node.unresolved_references ?? []).map(ref => typeof ref === 'string' ? detail.node_labels[ref] ?? ref : String(ref)),
  }))
  return { replicate_label: slot.replicate_label, replicate_number: slot.replicate_number, cell_label: slot.cell_label,
    factor_values: slot.factor_values, metric_values: metrics, status: detail.status === 'pending' ? 'queued' : detail.status as ResultReplicate['status'],
    obsolete: false, error: detail.error, run_id: detail.run_id, protocol_revision_id: detail.protocol_revision_id,
    updated_at: detail.completed_at ?? detail.started_at ?? '', node_runs: nodes,
    duration_seconds: usage?.duration_seconds ?? null, cost_usd: usage?.cost_usd ?? null,
    input_tokens: usage?.input_tokens ?? null, output_tokens: usage?.output_tokens ?? null, total_tokens: usage?.total_tokens ?? null,
    agent_run_count: usage?.agent_run_count ?? 0, reported_usage_count: usage?.complete ? 1 : 0, reported_cost_count: usage?.cost_usd != null ? 1 : 0,
    metric_evaluation: detail.attempt_result?.metric_evaluation,
    metric_observations: observations, evaluation_artifacts: detail.attempt_result?.measurement?.artifacts ?? [],
    obsolete_runs: [], superseded_runs: [] }
}

function ConversationPage({ experimentId, runId, scope }: { experimentId: string; runId: string; scope: ResultsScope }) {
  const [offsets, setOffsets] = useState([0])
  const offset = offsets.at(-1) ?? 0
  const query = useQuery({ queryKey: [...executionKeys.root(experimentId), 'conversation', scope, runId, offset],
    queryFn: ({ signal }) => experimentsApi.getExecutionConversation(experimentId, runId, scope, offset, signal) })
  return <section className="space-y-2 border-t pt-3"><h3 className="text-sm font-medium">Conversation</h3>
    {query.isPending ? <Skeleton className="h-12" /> : query.error ? <p role="alert">{query.error.message}</p> : query.data?.items.map((item, index) => <pre key={offset+index} className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border p-2 font-mono text-xs">{JSON.stringify(item, null, 2)}</pre>)}
    <div className="flex gap-2"><Button size="xs" variant="outline" disabled={offsets.length === 1} onClick={() => setOffsets(current => current.slice(0,-1))}>Previous messages</Button><Button size="xs" variant="outline" disabled={query.data?.next_offset == null} onClick={() => query.data?.next_offset != null && setOffsets(current => [...current, query.data!.next_offset!])}>Next messages</Button></div>
  </section>
}

function SlotInspector({ experimentId, slotId, scope, onClose }: { experimentId: string; slotId: string; scope: ResultsScope; onClose: () => void }) {
  const [selected, setSelected] = useState<string>()
  const [conversationOpen, setConversationOpen] = useState(false)
  const pager = useCursor()
  const summary = useExecutionSummary(experimentId, scope)
  const history = useQuery({ queryKey: [...executionKeys.root(experimentId), 'attempts', scope, slotId, pager.cursor],
    queryFn: ({ signal }) => experimentsApi.listExecutionAttempts(experimentId, slotId, scope, pager.cursor, signal) })
  const runId = selected ?? history.data?.slot.run_id ?? history.data?.items[0]?.run_id
  const detail = useQuery({ queryKey: [...executionKeys.root(experimentId), 'attempt', scope, runId],
    queryFn: ({ signal }) => experimentsApi.getExecutionAttempt(experimentId, runId!, scope, signal), enabled: !!runId,
    refetchInterval: query => query.state.data && ['pending','running','finalizing'].includes(query.state.data.status) ? 5000 : false })
  const slot = history.data?.slot
  return <aside className="absolute inset-0 z-20 flex flex-col overflow-y-auto border-l bg-card p-3" aria-label="Result details">
    <div className="flex items-start justify-between gap-2"><h2 className="text-sm font-medium">{slot?.row_index != null ? `Source row ${slot.row_index+1} · ` : ''}Replicate {slot?.replicate_number ?? ''}</h2><Button variant="ghost" size="xs" onClick={onClose}>Close</Button></div>
    {slot && <p className="my-2 break-all font-mono text-xs text-muted-foreground">{slot.cell_label}</p>}
    <div className="space-y-2"><h3 className="text-xs font-medium">Attempts</h3>{history.data?.items.map(attempt => <Button key={attempt.run_id} size="xs" variant={runId === attempt.run_id ? 'default' : 'outline'} className="mr-1 mb-1" onClick={() => { setSelected(attempt.run_id); setConversationOpen(false) }}>{new Date(attempt.created_at).toLocaleString()} · {attempt.status}{attempt.current ? ' · current' : ' · prior'}</Button>)}
      <CursorControls next={history.data?.next_cursor} back={pager.back} onNext={pager.next} onBack={pager.previous} busy={history.isFetching} />
    </div>
    {(history.isPending || detail.isPending && !!runId) && <Skeleton className="my-3 h-32" />}
    {(history.error || detail.error) && <p role="alert" className="text-xs text-destructive">{history.error?.message ?? detail.error?.message}</p>}
    {slot && detail.data && <div className="mt-3"><ReplicateResultDetail replicate={attemptAsReplicate(slot, detail.data)} metricKeys={Object.keys(summary.data?.metric_types ?? {})} metricTypes={summary.data?.metric_types ?? {}} />
      <Button size="xs" variant="outline" className="my-3" onClick={() => setConversationOpen(value => !value)}>{conversationOpen ? 'Hide conversation' : 'View conversation'}</Button>
      {conversationOpen && runId && <ConversationPage key={runId} experimentId={experimentId} runId={runId} scope={scope} />}
    </div>}
    {history.data && !runId && <p className="text-xs text-muted-foreground">This execution has no attempts.</p>}
  </aside>
}

export function ExecutionInspector({ experimentId, selection, onClose }: { experimentId: string; experiment: Experiment; selection: ResultsSelection | null; onClose: () => void }) {
  const scope = selection?.scope ?? {}
  const legacyLabel = selection?.type === 'replicate' ? selection.replicateLabel : undefined
  const lookup = useQuery({ queryKey: [...executionKeys.root(experimentId), 'lookup', scope, legacyLabel],
    queryFn: ({ signal }) => experimentsApi.lookupExecution(experimentId, legacyLabel!, scope, signal), enabled: !!legacyLabel })
  if (!selection) return null
  if (selection.type === 'cell') return <CellInspector key={`${JSON.stringify(scope)}:${selection.cellId ?? selection.cellLabel}`} experimentId={experimentId} selection={selection} scope={scope} onClose={onClose} />
  const slotId = selection.type === 'execution' ? selection.slotId : selection.type === 'row' ? selection.rowResultId : lookup.data?.slot_id
  if (slotId) return <SlotInspector key={`${JSON.stringify(scope)}:${slotId}`} experimentId={experimentId} slotId={slotId} scope={scope} onClose={onClose} />
  return <aside className="absolute inset-0 z-20 bg-card p-3"><Button size="xs" onClick={onClose}>Close</Button><p className="mt-2 text-xs">{lookup.isFetching ? 'Loading result…' : lookup.error?.message ?? 'Expand the cell to inspect an execution.'}</p></aside>
}

function CellInspector({ experimentId, selection, scope, onClose }: { experimentId: string; selection: Extract<ResultsSelection, { type: 'cell' }>; scope: ResultsScope; onClose: () => void }) {
  const [slot, setSlot] = useState<string>()
  const cell = useQuery({ queryKey: [...executionKeys.root(experimentId), 'cell', scope, selection.cellId ?? selection.cellLabel],
    queryFn: ({ signal }) => experimentsApi.getExecutionCell(experimentId, scope, { cell_id: selection.cellId, cell_label: selection.cellLabel }, signal) })
  const summary = useExecutionSummary(experimentId, scope)
  if (slot) return <SlotInspector experimentId={experimentId} slotId={slot} scope={scope} onClose={() => setSlot(undefined)} />
  return <aside className="absolute inset-0 z-20 overflow-y-auto border-l bg-card p-3" aria-label="Cell result details"><div className="mb-3 flex justify-between"><h2 className="text-sm font-medium">Cell results</h2><Button size="xs" variant="ghost" onClick={onClose}>Close</Button></div>
    {cell.data && <><p className="font-mono text-xs">{cell.data.cell_label}</p><dl className="my-3 grid grid-cols-2 gap-2 text-xs">{Object.entries(cell.data.metric_values ?? {}).map(([name, value]) => <div key={name}><dt>{name}</dt><dd className="font-mono">{String(value)}</dd></div>)}</dl>
      <ExecutionRows experimentId={experimentId} scope={scope} cellId={cell.data.cell_id} generation={summary.data?.generation} summary={summary.data} onSelect={item => { if (item.type === 'execution') setSlot(item.slotId) }} />
    </>}{cell.error && <p role="alert">{cell.error.message}</p>}
  </aside>
}
