import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { experimentsApi, type ResultsScope } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { displayFactorCondition, formatMetricLabel, formatMetricValue } from '@/lib/experiment'
import { metricCatalogEntry } from '@/lib/metricCatalog'
import { executionKeys, useExecutionSummary } from '@/lib/executionQueries'
import type { AttemptDetail, ExecutionSlot } from '@/types/executionResults'
import type { Experiment, ResultReplicate, ResultNodeRun } from '@/types/experiments'
import { ReplicateResultDetail, type ResultsSelection } from './ResultsTab'
import { CursorControls, useGenerationRefresh } from './ExecutionBrowser'
import { CellResultStatus } from './CellResultStatus'
import { useCursor } from '@/lib/executionPagination'
import { ExecutionRows } from './ExecutionBrowser'
import { ResultsInspectorSplit } from './ResultsInspectorSplit'

function attemptAsReplicate(slot: ExecutionSlot, detail: AttemptDetail): ResultReplicate {
  const usage = detail.usage
  const observations = detail.attempt_result?.measurement?.observations ?? []
  const metrics = { ...detail.attempt_result?.metric_values }
  for (const observation of observations) if (observation.status === 'measured') {
    metrics[observation.metric_name] = observation.value
    metrics[observation.metric_id] = observation.value
  }
  const nodes: ResultNodeRun[] = Object.entries(detail.node_runs).filter(([, node]) =>
    !!node.run_id || !!node.output_text || !!node.error || ['running', 'failed', 'cancelled'].includes(node.status),
  ).map(([id, node]) => ({ node_id: id, node_label: detail.node_labels[id] ?? id, status: node.status,
    bypassed: node.bypassed, output_text: node.output_text ?? null, error: node.error ?? null,
    agent_run_id: node.run_id ?? null, input_tokens: usage?.by_node?.[id]?.input_tokens ?? null, output_tokens: usage?.by_node?.[id]?.output_tokens ?? null, total_tokens: usage?.by_node?.[id]?.total_tokens ?? null, cost_usd: usage?.by_node?.[id]?.cost_usd ?? null,
    unresolved_reference_labels: (node.unresolved_references ?? []).map(ref => typeof ref === 'string' ? detail.node_labels[ref] ?? ref : String(ref)),
  }))
  return { replicate_label: slot.replicate_label, replicate_number: slot.replicate_number, cell_label: slot.cell_label,
    factor_values: slot.factor_values, metric_values: metrics, status: detail.status === 'pending' ? 'queued' : detail.status as ResultReplicate['status'],
    obsolete: false, error: detail.error, run_id: detail.run_id, protocol_revision_id: detail.protocol_revision_id,
    updated_at: detail.completed_at ?? detail.started_at ?? '', node_runs: nodes,
    duration_seconds: usage?.duration_seconds ?? null, cost_usd: usage?.cost_usd ?? null,
    input_tokens: usage?.input_tokens ?? null, output_tokens: usage?.output_tokens ?? null, total_tokens: usage?.total_tokens ?? null,
    agent_run_count: usage?.agent_run_count ?? 0, reported_usage_count: usage?.reported_usage_count ?? 0, reported_cost_count: usage?.reported_cost_count ?? 0,
    metric_evaluation: detail.attempt_result?.metric_evaluation,
    metric_observations: observations, evaluation_artifacts: detail.attempt_result?.measurement?.artifacts ?? [],
    legacy_values: Object.entries(detail.attempt_result?.metric_values ?? {}).filter(([name]) => !observations.some(item => item.metric_name === name)).map(([name, value]) => ({ metric_id: `legacy:${name}`, metric_name: name, value, attempt_id: detail.run_id, producer: { binding_id: 'legacy-unknown' as const, producer_id: 'legacy.unknown' as const, kind: 'legacy' as const, version: 'unknown' as const } })),
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

function SlotInspector({ experimentId, slotId, scope, onClose, onBackToCell, onSelectSlot, embedded = false }: {
  experimentId: string; slotId: string; scope: ResultsScope; onClose: () => void
  onBackToCell?: () => void; onSelectSlot?: (slotId: string) => void
  embedded?: boolean
}) {
  const [selected, setSelected] = useState<string>()
  const [conversationOpen, setConversationOpen] = useState(false)
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const pager = useCursor()
  const summary = useExecutionSummary(experimentId, scope)
  const neighbors = useQuery({ queryKey: [...executionKeys.root(experimentId), 'neighbors', scope, slotId],
    queryFn: ({ signal }) => experimentsApi.getExecutionNeighbors(experimentId, slotId, scope, signal), enabled: !!onSelectSlot })
  const history = useQuery({ queryKey: [...executionKeys.root(experimentId), 'attempts', scope, slotId, pager.cursor],
    queryFn: ({ signal }) => experimentsApi.listExecutionAttempts(experimentId, slotId, scope, pager.cursor, signal) })
  const runId = selected ?? history.data?.slot.run_id ?? history.data?.items[0]?.run_id
  const detail = useQuery({ queryKey: [...executionKeys.root(experimentId), 'attempt', scope, runId],
    queryFn: ({ signal }) => experimentsApi.getExecutionAttempt(experimentId, runId!, scope, signal), enabled: !!runId,
    refetchInterval: query => query.state.data && ['pending','running','finalizing'].includes(query.state.data.status) ? 5000 : false })
  const slot = history.data?.slot
  const cell = useQuery({ queryKey: [...executionKeys.root(experimentId), 'cell', scope, slot?.cell_id],
    queryFn: ({ signal }) => experimentsApi.getExecutionCell(experimentId, scope, { cell_id: slot!.cell_id }, signal),
    enabled: !!slot?.cell_id })
  const cellLabel = slot ? Object.entries(slot.factor_values).map(([name, value]) =>
    displayFactorCondition(name, value, summary.data?.design_spec, slot.cell_label)).join(' · ') || slot.cell_label : ''
  return <aside className={`${embedded ? 'relative h-full min-h-0 min-w-0' : 'absolute inset-0 z-20'} @container flex flex-col overflow-y-auto border-l bg-card p-3`} aria-label="Result details">
    <div className="mb-3 flex items-center justify-between gap-2">
      {onBackToCell && <Button variant="outline" size="xs" className={embedded ? '@min-[800px]/inspectors:hidden' : undefined} onClick={onBackToCell}>Back to cell</Button>}
      <Button variant="ghost" size="xs" className="ml-auto" onClick={onClose}>{embedded ? 'Close replicate' : 'Close'}</Button>
    </div>
    <div className="-mx-3 mb-4 shrink-0 border-b bg-muted/20 px-3 pb-4 pt-2">
      <div className={onSelectSlot ? 'grid grid-cols-[2rem_minmax(0,1fr)_2rem] items-stretch gap-2' : ''}>
        {onSelectSlot && <Button variant="outline" size="icon" className="h-auto w-full"
          aria-label={summary.data?.unit === 'row' ? 'Previous row' : 'Previous replicate'} title={summary.data?.unit === 'row' ? 'Previous row' : 'Previous replicate'}
          disabled={neighbors.isFetching || neighbors.isError || !neighbors.data?.previous_slot_id}
          onClick={() => neighbors.data?.previous_slot_id && onSelectSlot(neighbors.data.previous_slot_id)}>
          <ChevronLeft aria-hidden="true" />
        </Button>}
    <Card className="min-w-0 gap-1 p-2">
      <h2 className="font-mono text-base font-semibold">{slot ? `Replicate ${slot.replicate_number} details` : 'Replicate details'}</h2>
      <p className="truncate text-xs text-muted-foreground" title={cell.data?.cell_number != null ? `Cell ${cell.data.cell_number}: ${cellLabel}` : cellLabel}>
        {cell.data?.cell_number != null && <span className="font-mono">Cell {cell.data.cell_number}: </span>}{cellLabel || 'Loading cell…'}
      </p>
      {slot?.row_index != null && <p className="font-mono text-xs text-muted-foreground">Source row {slot.row_index + 1}</p>}
    </Card>
        {onSelectSlot && <Button variant="outline" size="icon" className="h-auto w-full"
          aria-label={summary.data?.unit === 'row' ? 'Next row' : 'Next replicate'} title={summary.data?.unit === 'row' ? 'Next row' : 'Next replicate'}
          disabled={neighbors.isFetching || neighbors.isError || !neighbors.data?.next_slot_id}
          onClick={() => neighbors.data?.next_slot_id && onSelectSlot(neighbors.data.next_slot_id)}>
          <ChevronRight aria-hidden="true" />
        </Button>}
      </div>
      {neighbors.error && <p role="alert" className="mt-2 text-xs text-destructive">Navigation unavailable. <Button variant="ghost" size="xs" disabled={neighbors.isFetching} onClick={() => void neighbors.refetch()}>Refresh</Button></p>}
    </div>
    <div className="mt-2 space-y-2"><h3 className="text-xs font-medium">Attempts</h3>{history.data?.items.map(attempt => <Button key={attempt.run_id} size="xs" variant={runId === attempt.run_id ? 'default' : 'outline'} className="mr-1 mb-1" onClick={() => { setSelected(attempt.run_id); setConversationOpen(false) }}>{new Date(attempt.created_at).toLocaleString()} · {attempt.status}{attempt.current ? ' · current' : ' · prior'}</Button>)}
      <CursorControls next={history.data?.next_cursor} back={pager.back} onNext={pager.next} onBack={pager.previous} busy={history.isFetching} />
    </div>
    {(history.isPending || detail.isPending && !!runId) && <Skeleton className="my-3 h-32" />}
    {(history.error || detail.error) && <p role="alert" className="text-xs text-destructive">{history.error?.message ?? detail.error?.message}</p>}
    {slot && detail.data && <div className="mt-3"><ReplicateResultDetail replicate={attemptAsReplicate(slot, detail.data)} metricKeys={Object.keys(summary.data?.metric_types ?? {})} metricTypes={summary.data?.metric_types ?? {}} metricLabels={summary.data?.metric_labels} executionContext={{ experimentId, runId: detail.data.run_id, scope }} />
      <Button size="xs" variant="outline" className="my-3" onClick={() => setEvidenceOpen(value => !value)}>{evidenceOpen ? 'Hide recorded JSON' : 'View complete recorded JSON'}</Button>
      {evidenceOpen && runId && <RecordedEvidence key={runId} experimentId={experimentId} runId={runId} scope={scope} />}
      <Button size="xs" variant="outline" className="my-3" onClick={() => setConversationOpen(value => !value)}>{conversationOpen ? 'Hide conversation' : 'View conversation'}</Button>
      {conversationOpen && runId && <ConversationPage key={runId} experimentId={experimentId} runId={runId} scope={scope} />}
    </div>}
    {history.data && !runId && <p className="text-xs text-muted-foreground">This {slot?.row_index != null ? 'row' : 'replicate'} has no run attempts.</p>}
    {history.data?.legacy_result && <section className="mt-3 space-y-2"><h3 className="text-sm">Legacy result</h3><p className="text-xs text-muted-foreground">Original values; producer unknown. These values are not ranked.</p><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border p-2 font-mono text-xs">{JSON.stringify(history.data.legacy_result, null, 2)}</pre></section>}
  </aside>
}

function RecordedEvidence({ experimentId, runId, scope }: { experimentId: string; runId: string; scope: ResultsScope }) {
  const [document, setDocument] = useState('attempt_result')
  return <section className="space-y-2"><select aria-label="Recorded document" className="rounded border bg-background p-1 text-xs" value={document} onChange={event => setDocument(event.target.value)}>{['attempt_result', 'node_runs', 'conversation', 'dataset_row'].map(name => <option key={name} value={name}>{name.replaceAll('_',' ')}</option>)}</select><EvidencePage key={document} experimentId={experimentId} runId={runId} scope={scope} document={document} /></section>
}

function EvidencePage({ experimentId, runId, scope, document }: { experimentId: string; runId: string; scope: ResultsScope; document: string }) {
  const [offsets, setOffsets] = useState([0])
  const offset = offsets.at(-1) ?? 0
  const query = useQuery({ queryKey: [...executionKeys.root(experimentId), 'evidence', scope, runId, document, offset],
    queryFn: ({ signal }) => experimentsApi.getExecutionEvidence(experimentId, runId, scope, document, offset, signal) })
  return <div className="space-y-2"><p className="font-mono text-xs">Recorded JSON · characters {offset.toLocaleString()}–{(offset + (query.data?.text.length ?? 0)).toLocaleString()}</p><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border p-2 font-mono text-xs">{query.data?.text ?? 'Loading…'}</pre>
    {query.error && <p role="alert" className="text-xs text-destructive">{query.error.message}</p>}
    <div className="flex gap-2"><Button size="xs" variant="outline" disabled={offsets.length === 1 || query.isFetching} onClick={() => setOffsets(current => current.slice(0,-1))}>Previous JSON</Button><Button size="xs" variant="outline" disabled={query.data?.next_offset == null || query.isFetching} onClick={() => query.data?.next_offset != null && setOffsets(current => [...current, query.data!.next_offset!])}>Next JSON</Button></div>
  </div>
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
  return <aside className="absolute inset-0 z-20 bg-card p-3"><Button size="xs" onClick={onClose}>Close</Button><p className="mt-2 text-xs">{lookup.isFetching ? 'Loading result…' : lookup.error?.message ?? 'Expand the cell to inspect a replicate.'}</p></aside>
}

function CellInspector({ experimentId, selection, scope, onClose }: { experimentId: string; selection: Extract<ResultsSelection, { type: 'cell' }>; scope: ResultsScope; onClose: () => void }) {
  const [navigation, setNavigation] = useState({ origin: selection, current: selection })
  const current = navigation.origin === selection ? navigation.current : selection
  return <CellInspectorContent key={current.cellId ?? current.cellLabel} experimentId={experimentId} selection={current}
    scope={scope} onClose={onClose} onSelectCell={cellId => setNavigation({ origin: selection, current: { type: 'cell', cellId, scope } })} />
}

function CellInspectorContent({ experimentId, selection, scope, onClose, onSelectCell }: {
  experimentId: string; selection: Extract<ResultsSelection, { type: 'cell' }>; scope: ResultsScope
  onClose: () => void; onSelectCell: (cellId: string) => void
}) {
  const [slot, setSlot] = useState<string>()
  const listPanel = useRef<HTMLElement>(null)
  const savedScroll = useRef(0)
  const cell = useQuery({ queryKey: [...executionKeys.root(experimentId), 'cell', scope, selection.cellId ?? selection.cellLabel],
    queryFn: ({ signal }) => experimentsApi.getExecutionCell(experimentId, scope, { cell_id: selection.cellId, cell_label: selection.cellLabel }, signal) })
  const summary = useExecutionSummary(experimentId, scope)
  useGenerationRefresh(cell.refetch, summary.data?.generation)
  useEffect(() => { if (!slot && listPanel.current) listPanel.current.scrollTop = savedScroll.current }, [slot])
  const cellLabel = cell.data ? Object.entries(cell.data.factor_values).map(([name, value]) =>
    displayFactorCondition(name, value, summary.data?.design_spec, cell.data!.cell_label)).join(' · ') || cell.data.cell_label : ''
  return <ResultsInspectorSplit replicate={slot ? <SlotInspector key={slot} embedded experimentId={experimentId} slotId={slot} scope={scope} onClose={() => setSlot(undefined)}
    onBackToCell={() => setSlot(undefined)} onSelectSlot={setSlot} /> : undefined}>
    <aside ref={listPanel} className={`@container min-h-0 min-w-0 overflow-y-auto border-l bg-card p-3 ${slot ? 'hidden @min-[800px]/inspectors:block' : ''}`} aria-label="Cell result details"><div className="mb-3 flex items-center justify-between gap-2"><p className="text-xs text-muted-foreground">Results / Cell</p><Button size="xs" variant="ghost" onClick={onClose}>Close</Button></div>
    {cell.isPending && <Skeleton className="h-24" />}
    {cell.data && <>
      <div className="-mx-3 mb-4 grid grid-cols-[2rem_minmax(0,1fr)_2rem] items-stretch gap-2 border-b bg-muted/20 px-3 pb-4 pt-2">
        <Button variant="outline" size="icon" className="h-auto w-full" aria-label="Previous cell" title="Previous cell"
          disabled={cell.isFetching || !cell.data.previous_cell_id} onClick={() => cell.data?.previous_cell_id && onSelectCell(cell.data.previous_cell_id)}>
          <ChevronLeft aria-hidden="true" />
        </Button>
        <Card className="min-w-0 gap-1 p-2">
          <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
            {cell.data.cell_number != null && <p className="font-mono">Cell {cell.data.cell_number}</p>}
            <p className="shrink-0">{(cell.data.replicate_count ?? 0).toLocaleString()} replicates</p>
          </div>
          <p className="truncate text-sm font-medium" title={cellLabel}>{cellLabel}</p>
        </Card>
        <Button variant="outline" size="icon" className="h-auto w-full" aria-label="Next cell" title="Next cell"
          disabled={cell.isFetching || !cell.data.next_cell_id} onClick={() => cell.data?.next_cell_id && onSelectCell(cell.data.next_cell_id)}>
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
      <CellResultStatus cell={cell.data} unit={summary.data?.unit} />
      <section aria-label="Cell measurements" className="my-4 space-y-2">
        <h3 className="text-sm font-medium">Measurements</h3>
        {Object.keys(cell.data.metric_values ?? {}).length ? <dl className="grid grid-cols-2 gap-2 @min-[600px]:grid-cols-3">
          {Object.entries(cell.data.metric_values ?? {}).map(([key, value]) => {
            const label = summary.data?.metric_labels?.[key] ?? metricCatalogEntry(key)?.name ?? formatMetricLabel(key)
            const formatted = summary.data?.metric_types[key] === 'boolean'
              ? new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 }).format(value)
              : key === 'cost_usd' ? new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value)
              : formatMetricValue(key, value)
            return <Card key={key} className="min-w-0 gap-1 p-3">
              <dt className="text-xs text-muted-foreground break-words">{label}</dt>
              <dd>
                <span className="font-mono text-lg font-medium tabular-nums break-words">{formatted}</span>
                {summary.data?.metric_aggregations[key] && <p className="mt-1 text-[0.65rem] text-muted-foreground">{summary.data.metric_aggregations[key] === 'sum' ? 'Total' : 'Average'}</p>}
              </dd>
            </Card>
          })}
        </dl> : <p className="text-xs text-muted-foreground">No aggregated measurements available yet.</p>}
      </section>
      <h3 className="mb-2 text-sm font-medium">Replicates</h3>
      <ExecutionRows key={`${summary.data?.scope_key}:${summary.data?.source_row_count}`} experimentId={experimentId} scope={scope} cellId={cell.data.cell_id} selectedSlotId={slot} generation={summary.data?.generation} summary={summary.data} onSelect={item => {
        if (item.type === 'execution') { savedScroll.current = listPanel.current?.scrollTop ?? 0; setSlot(item.slotId) }
      }} />
    </>}{cell.error && <p role="alert">{cell.error.message}</p>}
  </aside>
  </ResultsInspectorSplit>
}
