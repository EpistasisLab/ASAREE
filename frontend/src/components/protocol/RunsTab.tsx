import { useEffect, useRef, useState, type ComponentProps } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import { AlertTriangle, Square } from 'lucide-react'
import { experimentsApi, protocolsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { factorBindingDiscrepancies, type FactorBindingDiscrepancy } from '@/lib/factorBindings'
import { useBatchPreview } from '@/lib/resultJobs'
import { executionKeys, useExecutionSummary } from '@/lib/executionQueries'
import { protocolForExperimentQueryKey, protocolGraphQueryKey, toPersistedGraph } from '@/lib/protocolGraph'
import { newRequestKey } from '@/lib/requestKey'
import type { Experiment } from '@/types/experiments'
import type { Protocol } from '@/types/protocols'
import type { BatchMode, BatchSelection, ExecutionFocus, ExecutionReplicate } from '@/types/executionResults'
import { ExecutionBrowser } from './ExecutionBrowser'
import { ResultJobProgress } from './ResultJobProgress'
import { ExperimentDefinitionDownload } from './ExperimentDefinitionDownload'
import { RunConfirmDialog } from './RunConfirmDialog'
import { RunBrowserControls, hasExecutionFilters, statusLabels, useRunBrowserSettings } from './RunBrowserControls'
import type { ResultsSelection } from './ResultsTab'

const ACTIVE_STATUSES = ['pending', 'running', 'finalizing']
const actionLabels: Record<BatchMode, string> = { pending: 'Run pending', retry: 'Retry failed', rerun: 'Re-run' }
const counted = (count: number, noun: string) => `${count} ${noun}${count === 1 ? '' : 's'}`
interface BatchRequest { selection: BatchSelection; label: string; description?: string }
interface RunGuard { protocol: Protocol | undefined; experimentId: string; regenerationRequired: boolean
  unboundFactors: string[]; bindingDiscrepancies: FactorBindingDiscrepancy[] }
interface RunSelection {
  cells: string[]; slots: string[]; replicates: string[]; excluded: string[]; excludedReplicates: string[]; excludedCells: string[]; all: boolean
  slotCells: Record<string, string>; replicateCells: Record<string, string>
  slotReplicates: Record<string, string>; slotRows: Record<string, { replicate_id: string; row_index: number }>
}
const emptySelection: RunSelection = { cells: [], slots: [], replicates: [], excluded: [], excludedReplicates: [], excludedCells: [], all: false, slotCells: {}, replicateCells: {}, slotReplicates: {}, slotRows: {} }

function SelectionCheckbox({ checked, ...props }: Omit<ComponentProps<typeof Checkbox>, 'checked'> & { checked: boolean | 'indeterminate' }) {
  return <Checkbox {...props} checked={checked === true} indeterminate={checked === 'indeterminate'} />
}

function blockedReason(guard: RunGuard) {
  if (!guard.protocol) return 'Create a protocol before running.'
  if (guard.regenerationRequired) return 'Review and regenerate the design before running.'
  if (guard.unboundFactors.length) return `Rebind or remove: ${guard.unboundFactors.join(', ')}.`
  if (guard.bindingDiscrepancies.length) return 'Resolve the disagreement between canvas values and generated factor levels.'
}

function RunBatchDialog({ request, guard, onCancel, onStarted }: {
  request: BatchRequest; guard: RunGuard; onCancel: () => void; onStarted: (jobId: string) => void
}) {
  const { protocol, experimentId } = guard
  const client = useQueryClient()
  const [lock, setLock] = useState(false)
  const [sourceJobId, setSourceJobId] = useState<string>()
  const { settings, update } = useRunBrowserSettings(`${experimentId}:preview`, {
    search: request.selection.search ?? '', status: request.selection.status ?? '', factors: request.selection.factors ?? {},
  }, false)
  const scope = { protocol_id: protocol?.id, protocol_revision_id: protocol?.published_revision_id ?? undefined }
  const selection = { ...request.selection, source_job_id: sourceJobId, search: settings.filters.search,
    status: settings.filters.status || undefined, factors: settings.filters.factors }
  const preview = useBatchPreview(experimentId, scope, selection, true)
  const experiment = useQuery({ queryKey: ['experiments', experimentId], queryFn: () => experimentsApi.get(experimentId) })
  const publication = useQuery({ queryKey: ['protocols', protocol?.id, 'revision', protocol?.published_revision_id],
    queryFn: () => protocolsApi.getRevision(protocol!.id, protocol!.published_revision_id!), enabled: !!protocol?.published_revision_id })
  const run = useMutation({ mutationFn: async () => {
    if (lock && !experiment.data?.locked_at) await experimentsApi.lock(experimentId)
    return experimentsApi.confirmResultJob(experimentId, preview.data!.id)
  }, onSuccess: job => { onStarted(job.id); void client.invalidateQueries({ queryKey: ['experiments', experimentId] }) } })
  const publish = useMutation({ mutationFn: async () => {
    const reviewedSourceId = sourceJobId ?? (preview.data?.status === 'awaiting_confirmation' ? preview.data.id : undefined)
    const live = client.getQueryData<{ nodes: Node[]; edges: Edge[] }>(protocolGraphQueryKey(protocol!.id))
    if (live) await protocolsApi.update(protocol!.id, { graph: toPersistedGraph(live.nodes, live.edges) })
    return { published: await protocolsApi.publish(protocol!.id), reviewedSourceId }
  }, onSuccess: ({ published, reviewedSourceId }) => {
    if (reviewedSourceId) setSourceJobId(reviewedSourceId)
    client.setQueryData(protocolForExperimentQueryKey(experimentId), published)
    void client.invalidateQueries({ queryKey: executionKeys.root(experimentId) })
  } })
  if (!protocol) return null
  const progress = preview.data?.progress
  const ready = preview.data?.status === 'awaiting_confirmation'
  const count = progress?.planned ?? 0
  const cells = progress?.selected_cell_count ?? 0
  const replicates = progress?.selected_replicate_count ?? 0
  const units = counted(count, progress?.unit === 'row' ? 'dataset row' : 'replicate')
  const batchDescription = `${units}${progress?.unit === 'row' ? ` across ${counted(replicates, 'replicate')}` : ''} in ${counted(cells, 'cell')}`
  const title = ready ? `${request.label}: ${batchDescription}?` : `${request.label}?`
  const graph = publication.data?.graph ?? protocol.graph
  return <RunConfirmDialog scope={{ type: 'batch', title }} nodes={graph.nodes as unknown as Node[]}
    edges={graph.edges as Edge[]} queryClient={client} onCancel={onCancel} onConfirm={() => run.mutate()}
    hasUnpublishedChanges={protocol.has_unpublished_changes} publishedRevision={protocol.published_revision}
    onPublishAndRun={() => publish.mutate()} isPublishing={publish.isPending} publishError={publish.error?.message}
    publishDisabled={publish.isPending || run.isPending || (!!protocol.published_revision_id && request.selection.mode !== 'pending' && !ready)} publishLabel="Publish changes & review"
    confirmLabel={ready ? `${request.label} (${count})` : request.label}
    confirmDisabled={!ready || !count || run.isPending || publish.isPending || !!blockedReason(guard) || !publication.data}
    isConfirming={run.isPending} confirmError={run.error?.message}
    truncationNotice={progress?.limited ? `${progress.limited} selected executions hit their limit last time. Review the iteration or budget limit before running again.` : null}
    additionalContent={<div className="max-h-[50vh] space-y-3 overflow-y-auto pr-1">
      {request.description && <p className="text-sm">{request.description}</p>}
      <fieldset disabled={publish.isPending || run.isPending}><RunBrowserControls experimentId={experimentId} scope={scope} settings={settings} onChange={update} /></fieldset>
      {publication.error && <p role="alert" className="text-xs text-destructive">Could not load the published version: {publication.error.message}</p>}
      {progress?.previous_version && <p role="status" className="text-xs text-[color:var(--chart-4)]">These attempts will use the newly published version. The previous version’s results stay in history.</p>}
      {!ready && !preview.error && !preview.data?.error && <p role="status" className="text-xs">Preparing your batch… {progress?.scanned ?? 0} executions checked.</p>}
      {(preview.error || preview.data?.error) && <p role="alert" className="text-xs text-destructive">{preview.error?.message ?? preview.data?.error}</p>}
      {ready && <div className="space-y-1 text-sm" role="status">
        <p>{batchDescription} will be queued.</p>
        <p className="text-xs text-muted-foreground">{Object.entries(progress?.statuses ?? {}).map(([status, total]) => `${total} ${statusLabels[status]?.toLowerCase() ?? status}`).join(' · ')}</p>
        <p className="text-xs text-muted-foreground">{progress?.active_skipped ?? 0} queued/running executions skipped. New attempts start from the beginning; previous attempts remain available.</p>
        {!count && <p>No eligible work matches this action. Change the filters or choose another action.</p>}
      </div>}
      {blockedReason(guard) && <p role="alert" className="text-xs text-destructive">{blockedReason(guard)}</p>}
      {ready && <ExecutionBrowser key={`${preview.data!.id}:${JSON.stringify(settings.filters)}:${settings.sort}:${settings.sortDirection}`}
        experimentId={experimentId} scope={scope} mode="runs" onSelect={() => {}} jobId={preview.data!.id}
        settings={sourceJobId ? { ...settings, filters: { ...settings.filters, status: '' } } : settings} onExpand={expanded => update({ expanded })} />}
      <details className="text-xs"><summary className="cursor-pointer">Experiment options</summary><div className="space-y-2 pt-2">
        {!experiment.data?.locked_at && <label className="flex items-center gap-2"><Checkbox checked={lock} onCheckedChange={checked => setLock(checked === true)} />Lock experiment before running</label>}
        <ExperimentDefinitionDownload experimentId={experimentId} protocol={protocol} />
      </div></details>
    </div>} />
}

/** Also used by the canvas top bar. Every opening starts a fresh pending-only preview. */
export function RunAllCellsButton(props: RunGuard & { label?: string; dialogTitle?: string; compact?: boolean; cellId?: string }) {
  const [open, setOpen] = useState(false)
  const [jobId, setJobId] = useState<string>()
  const label = props.label ?? 'Run all cells'
  const reason = blockedReason(props)
  return <><span title={reason}><Button size={props.compact ? 'xs' : 'sm'} disabled={!!reason} onClick={() => setOpen(true)}>{label}</Button></span>
    <ResultJobProgress experimentId={props.experimentId} jobId={jobId} />
    {open && <RunBatchDialog request={{ selection: { mode: 'pending', cell_id: props.cellId }, label,
      description: 'Start only work that has never been attempted. Use Retry failed or Re-run in the Runs panel for previous attempts.' }}
      guard={props} onCancel={() => setOpen(false)} onStarted={id => { setJobId(id); setOpen(false) }} />}
  </>
}

export function RunsTab({ experimentId, designSpec, protocol, regenerationRequired, unboundFactors, onViewResult, onViewRowResult, onSelectResult, focus, onClearFocus }: {
  experimentId: string; designSpec: Experiment['design_spec']; protocol: Protocol | undefined
  regenerationRequired: boolean; unboundFactors: string[]; onViewResult: (label: string) => void
  onViewRowResult?: (id: string) => void; onSelectResult?: (selection: ResultsSelection) => void
  focus?: ExecutionFocus; onClearFocus?: () => void
}) {
  const discrepancies = factorBindingDiscrepancies(designSpec, protocol?.graph)
  const guard = { experimentId, protocol, regenerationRequired, unboundFactors, bindingDiscrepancies: discrepancies }
  const reason = blockedReason(guard)
  const scope = { protocol_id: protocol?.id, protocol_revision_id: protocol?.published_revision_id ?? undefined }
  const summary = useExecutionSummary(experimentId, scope, !!protocol)
  const { settings, update } = useRunBrowserSettings(experimentId)
  const focusKey = JSON.stringify(focus)
  const appliedFocus = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (!focus) appliedFocus.current = undefined
    if (focus && appliedFocus.current !== focusKey) {
      appliedFocus.current = focusKey
      update({ filters: { search: '', status: '', factors: {} }, expanded: [...new Set([...settings.expanded, focus.cellId])] })
    }
  }, [focus, focusKey, settings.expanded, update])
  const [request, setRequest] = useState<BatchRequest>()
  const [viewedJob, setViewedJob] = useState<string>()
  const [jobIds, setJobIds] = useState<string[]>(() => {
    try { return JSON.parse(sessionStorage.getItem(`asaree:run-batches:${experimentId}`) ?? '[]') } catch { return [] }
  })
  const [selected, setSelected] = useState<RunSelection>(emptySelection)
  const scopeKey = JSON.stringify(scope)
  const previousScope = useRef(scopeKey)
  useEffect(() => {
    if (previousScope.current !== scopeKey) { setSelected(emptySelection); setViewedJob(undefined) }
    previousScope.current = scopeKey
  }, [scopeKey])
  const saveJobIds = (ids: string[]) => {
    setJobIds(ids)
    try { sessionStorage.setItem(`asaree:run-batches:${experimentId}`, JSON.stringify(ids)) } catch { /* optional */ }
  }
  const clearSelection = () => setSelected(emptySelection)
  const filtered = hasExecutionFilters(settings.filters) || !!focus
  const filterSelection = { search: settings.filters.search || undefined, status: settings.filters.status || undefined, factors: settings.filters.factors,
    cell_id: focus?.cellId, replicate_id: focus?.replicateId,
    row_indices: focus?.rowIndex != null ? [focus.rowIndex] : undefined }
  const selectedScope: BatchSelection = { ...filterSelection,
    all_matching: selected.all, cell_ids: selected.cells, slot_ids: selected.slots.filter(id => !id.startsWith('unplanned:')), replicate_ids: selected.replicates,
    row_selections: selected.slots.filter(id => id.startsWith('unplanned:')).map(id => selected.slotRows[id]),
    excluded_rows: selected.excluded.filter(id => id.startsWith('unplanned:')).map(id => selected.slotRows[id]),
    excluded_slot_ids: selected.excluded.filter(id => !id.startsWith('unplanned:')), excluded_replicate_ids: selected.excludedReplicates, excluded_cell_ids: selected.excludedCells }
  const hasSelection = selected.all || !!(selected.cells.length + selected.slots.length + selected.replicates.length)
  const action = (mode: BatchMode, label: string, selection: BatchSelection = {}, unavailable?: string) => <span title={reason ?? unavailable}>
    <Button size="xs" variant={mode === 'rerun' ? 'outline' : 'default'} disabled={!!reason || !!unavailable}
      onClick={() => setRequest({ selection: { ...selection, mode }, label,
        description: mode === 'retry' ? 'Start fresh attempts for execution failures only. Cancelled, limited, and scoring failures are excluded.' :
          mode === 'rerun' ? 'Repeat previously attempted work, including successful attempts. Unstarted and active executions are excluded.' :
            'Start only work that has never been attempted.' })}>{label}</Button>
  </span>
  const toggle = (values: string[], id: string) => values.includes(id) ? values.filter(value => value !== id) : [...values, id]
  const membership = (values: string[], id: string, include: boolean) => include ? [...new Set([...values, id])] : values.filter(value => value !== id)
  const replicateActions = (parent: ExecutionReplicate) => <div className="flex flex-wrap gap-1">
    <SelectionCheckbox aria-label={`Select replicate ${parent.replicate_number}`} checked={
      !selected.excludedReplicates.includes(parent.replicate_id) && ((selected.all && !selected.excludedCells.includes(parent.cell_id)) || selected.cells.includes(parent.cell_id) || selected.replicates.includes(parent.replicate_id))
        ? selected.excluded.some(id => selected.slotReplicates[id] === parent.replicate_id) ? 'indeterminate' : true
        : selected.slots.some(id => selected.slotReplicates[id] === parent.replicate_id) ? 'indeterminate' : false}
      onCheckedChange={checked => setSelected(current => ({ ...current, replicateCells: { ...current.replicateCells, [parent.replicate_id]: parent.cell_id },
        slots: current.slots.filter(id => current.slotReplicates[id] !== parent.replicate_id),
        excluded: current.excluded.filter(id => current.slotReplicates[id] !== parent.replicate_id),
        ...((current.all && !current.excludedCells.includes(parent.cell_id)) || current.cells.includes(parent.cell_id)
          ? { excludedReplicates: membership(current.excludedReplicates, parent.replicate_id, checked !== true) } : { replicates: membership(current.replicates, parent.replicate_id, checked === true) }) }))} />
    {action('pending', 'Run pending', { replicate_ids: [parent.replicate_id] })}
    {action('retry', 'Retry failed', { replicate_ids: [parent.replicate_id] }, !parent.counts.failed ? 'No failed rows.' : undefined)}
    {action('rerun', 'Re-run replicate', { replicate_ids: [parent.replicate_id] })}
  </div>
  return <div className="space-y-3">
    {discrepancies.length > 0 && <div role="alert" className="m-3 flex gap-2 rounded border border-[color:var(--chart-4)]/50 p-3 text-xs"><AlertTriangle className="size-4" /><div><p className="font-medium">Canvas and generated runs disagree</p>{discrepancies.map(issue => <p key={`${issue.nodeId}:${issue.fieldPath}`}>{issue.nodeLabel} · {issue.factorName}: {issue.reason}</p>)}</div></div>}
    {reason && <p className="px-3 pt-2 text-xs text-muted-foreground">{reason}</p>}
    <div className="space-y-2 px-3 pt-3">
      <RunBrowserControls experimentId={experimentId} scope={scope} settings={settings} onChange={patch => { if (patch.filters) clearSelection(); update(patch) }} />
      <div className="flex flex-wrap items-center gap-2">
        <Button size="xs" variant="outline" onClick={() => setSelected({ ...emptySelection, all: true })}>Select all matching</Button>
        {hasSelection && <><span className="text-xs">{selected.all ? 'All matching selected' : `${selected.cells.length} cells · ${selected.replicates.length} replicates · ${selected.slots.length} executions selected`}</span>
          <Button size="xs" variant="ghost" onClick={clearSelection}>Clear selection</Button>
          {(['pending', 'retry', 'rerun'] as BatchMode[]).map(mode => <span key={mode}>{action(mode, `${actionLabels[mode]} selected`, selectedScope)}</span>)}
        </>}
      </div>
      {filtered && <div className="flex flex-wrap gap-2">{action('rerun', 'Re-run matching cells', filterSelection)}</div>}
    </div>
    {jobIds.map(id => <div key={id} className="space-y-1 px-3"><ResultJobProgress experimentId={experimentId} jobId={id} />
      <div className="flex flex-wrap gap-2"><Button size="xs" variant="ghost" onClick={() => { clearSelection(); update({ filters: { search: '', status: '', factors: {} } }); setViewedJob(id) }}>View affected cells</Button>
        <Button size="xs" variant="ghost" onClick={() => saveJobIds(jobIds.filter(jobId => jobId !== id))}>Dismiss batch</Button></div></div>)}
    <ExecutionBrowser key={`${JSON.stringify(scope)}:${JSON.stringify(settings.filters)}:${settings.sort}:${settings.sortDirection}:${viewedJob ?? ''}:${focusKey}`}
      experimentId={experimentId} scope={scope} mode="runs" settings={settings} onExpand={expanded => update({ expanded })}
      jobId={viewedJob} replicateActions={replicateActions} focus={focus} onClearFocus={onClearFocus}
      onSelect={selection => {
        if (onSelectResult) onSelectResult(selection)
        else if (selection.type === 'row') onViewRowResult?.(selection.rowResultId)
        else if (selection.type === 'replicate') onViewResult(selection.replicateLabel)
      }}
      controls={<>{viewedJob && <Button size="xs" variant="ghost" onClick={() => setViewedJob(undefined)}>Show all cells</Button>}
        <StopAllButton protocol={protocol} experimentId={experimentId} active={!!summary.data?.has_active} />
        {action('pending', filtered ? 'Run matching pending' : 'Run pending', filterSelection)}
        {action('retry', filtered ? 'Retry matching failed' : 'Retry failed', filterSelection, summary.data?.counts?.failed === 0 ? 'No failed replicates.' : undefined)}
        {action('rerun', 'Re-run all cells')}
      </>}
      cellActions={cell => <>
        <SelectionCheckbox aria-label={`Select cell ${cell.cell_label}`} checked={
          ((selected.all && !selected.excludedCells.includes(cell.cell_id)) || selected.cells.includes(cell.cell_id))
            ? (selected.excluded.some(id => selected.slotCells[id] === cell.cell_id) || selected.excludedReplicates.some(id => selected.replicateCells[id] === cell.cell_id) ? 'indeterminate' : true)
            : selected.slots.some(id => selected.slotCells[id] === cell.cell_id) || selected.replicates.some(id => selected.replicateCells[id] === cell.cell_id) ? 'indeterminate' : false}
          onCheckedChange={checked => setSelected(current => ({ ...current,
            ...(current.all ? { excludedCells: membership(current.excludedCells, cell.cell_id, checked !== true) } : { cells: membership(current.cells, cell.cell_id, checked === true) }),
            slots: current.slots.filter(id => current.slotCells[id] !== cell.cell_id),
            replicates: current.replicates.filter(id => current.replicateCells[id] !== cell.cell_id),
            excluded: current.excluded.filter(id => current.slotCells[id] !== cell.cell_id),
            excludedReplicates: current.excludedReplicates.filter(id => current.replicateCells[id] !== cell.cell_id),
          }))} />
        {action('pending', filtered ? 'Run matching in cell' : 'Run cell', { ...filterSelection, cell_id: cell.cell_id })}
        {action('retry', filtered ? 'Retry matching in cell' : 'Retry failed in cell', { ...filterSelection, cell_id: cell.cell_id }, !cell.counts?.failed ? 'No failed executions in this cell.' : undefined)}
        {action('rerun', filtered ? 'Re-run matching in cell' : 'Re-run cell', { ...filterSelection, cell_id: cell.cell_id })}
        <StopAllButton protocol={protocol} experimentId={experimentId} cellId={cell.cell_id} active={ACTIVE_STATUSES.some(status => (cell.counts?.[status] ?? 0) > 0)} />
      </>}
      actions={row => {
        const inherited = !selected.excludedReplicates.includes(row.replicate_id) && ((selected.all && !selected.excludedCells.includes(row.cell_id)) || selected.cells.includes(row.cell_id) || selected.replicates.includes(row.replicate_id))
        const checked = inherited ? !selected.excluded.includes(row.slot_id) : selected.slots.includes(row.slot_id)
        const active = ACTIVE_STATUSES.includes(row.status)
        const mode: BatchMode = row.status === 'not_started' ? 'pending' : row.status === 'failed' && !row.result.truncated ? 'retry' : 'rerun'
        const label = row.row_index != null ? (mode === 'retry' ? 'Retry row' : mode === 'pending' ? 'Run row' : 'Re-run row') : mode === 'retry' ? 'Retry replicate' : mode === 'pending' ? 'Run replicate' : 'Re-run replicate'
        return <div className="flex flex-wrap items-center gap-1">
          <SelectionCheckbox aria-label={`Select replicate ${row.replicate_number}${row.row_index != null ? ` row ${row.row_index + 1}` : ''}`} checked={checked}
            disabled={active} onCheckedChange={() => setSelected(current => ({ ...current,
              slotCells: { ...current.slotCells, [row.slot_id]: row.cell_id },
              slotReplicates: { ...current.slotReplicates, [row.slot_id]: row.replicate_id },
              slotRows: row.row_index != null ? { ...current.slotRows, [row.slot_id]: { replicate_id: row.replicate_id, row_index: row.row_index } } : current.slotRows,
              ...(inherited ? { excluded: toggle(current.excluded, row.slot_id) } : { slots: toggle(current.slots, row.slot_id) }) }))} />
          {action(mode, label, { replicate_ids: [row.replicate_id], row_indices: row.row_index != null ? [row.row_index] : undefined }, active ? 'This execution is already queued or running.' : undefined)}
          {row.result.evaluation_state === 'failed' && <span className="text-xs text-muted-foreground">Scoring failed. Inspect the attempt for details; rerunning repeats execution.</span>}
          {active && row.run_id && <StopRunButton protocol={protocol} experimentId={experimentId} runId={row.run_id} />}
        </div>
      }} />
    {request && <RunBatchDialog key={JSON.stringify(request)} request={request} guard={guard} onCancel={() => setRequest(undefined)} onStarted={id => {
      saveJobIds([...jobIds, id])
      setRequest(undefined); clearSelection()
    }} />}
  </div>
}

function StopRunButton({ protocol, experimentId, runId }: { protocol?: Protocol; experimentId: string; runId: string }) {
  const client = useQueryClient()
  const stop = useMutation({ mutationFn: () => protocolsApi.cancelRun(protocol!.id, runId), onSuccess: () => {
    void client.invalidateQueries({ queryKey: executionKeys.root(experimentId) })
  } })
  return <><Button size="xs" variant="destructive" disabled={!protocol || stop.isPending} onClick={() => stop.mutate()}><Square className="size-3" />Stop</Button>{stop.error && <span role="alert" className="text-xs text-destructive">{stop.error.message}</span>}</>
}

function StopAllButton({ protocol, experimentId, cellId, active }: { protocol?: Protocol; experimentId: string; cellId?: string; active: boolean }) {
  const [jobId, setJobId] = useState<string>()
  const stop = useMutation({ mutationFn: () => experimentsApi.createResultJob(experimentId,
    { protocol_id: protocol?.id, protocol_revision_id: protocol?.published_revision_id ?? undefined },
    { kind: 'cancel', cell_id: cellId, request_key: newRequestKey() }), onSuccess: job => setJobId(job.id) })
  if (!active && !jobId && !stop.isPending) return null
  return <><Button variant="destructive" size="xs" disabled={!protocol || stop.isPending} onClick={() => stop.mutate()}><Square className="size-3" />{cellId ? 'Stop cell' : 'Stop all'}</Button>
    <ResultJobProgress experimentId={experimentId} jobId={jobId} />
    {stop.error && <p role="alert" className="text-xs text-destructive">{stop.error.message}</p>}</>
}
