import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import { AlertTriangle, Square } from 'lucide-react'
import { ApiError, experimentsApi, protocolsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { factorBindingDiscrepancies, type FactorBindingDiscrepancy } from '@/lib/factorBindings'
import { useBatchPreview } from '@/lib/resultJobs'
import { ResultJobProgress } from './ResultJobProgress'
import { executionKeys, useExecutionSummary } from '@/lib/executionQueries'
import { protocolForExperimentQueryKey, protocolGraphQueryKey, toPersistedGraph } from '@/lib/protocolGraph'
import type { Experiment } from '@/types/experiments'
import type { Protocol } from '@/types/protocols'
import type { ExecutionSlot } from '@/types/executionResults'
import { ExecutionBrowser, ExecutionRows } from './ExecutionBrowser'
import { ExperimentDefinitionDownload } from './ExperimentDefinitionDownload'
import { RunConfirmDialog } from './RunConfirmDialog'
import { ReplicateRunAction } from './CellPresentation'
import type { ResultsSelection } from './ResultsTab'

export function RunAllCellsButton({ protocol, experimentId, regenerationRequired, unboundFactors, bindingDiscrepancies, label = 'Run all cells', dialogTitle, compact = false, cellId }: {
  protocol: Protocol | undefined; experimentId: string; regenerationRequired: boolean; unboundFactors: string[]
  bindingDiscrepancies: FactorBindingDiscrepancy[]; label?: string; dialogTitle?: string; compact?: boolean
  cellId?: string
}) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [lock, setLock] = useState(false)
  const [reruns, setReruns] = useState<Set<string>>(() => new Set())
  const [rerunAll, setRerunAll] = useState(false)
  const scope = { protocol_id: protocol?.id, protocol_revision_id: protocol?.published_revision_id ?? undefined }
  const [confirmedId, setConfirmedId] = useState<string>()
  const preview = useBatchPreview(experimentId, scope, { cell_id: cellId, rerun_slot_ids: [...reruns], rerun_completed: rerunAll }, open)
  const summary = useExecutionSummary(experimentId, scope, open && !!protocol)
  const experiment = useQuery({ queryKey: ['experiments', experimentId], queryFn: () => experimentsApi.get(experimentId), enabled: open })
  const run = useMutation({
    mutationFn: async () => {
      if (lock && !experiment.data?.locked_at) await experimentsApi.lock(experimentId)
      return experimentsApi.confirmResultJob(experimentId, preview.data!.id)
    },
    onSuccess: job => { setConfirmedId(job.id); setOpen(false); void client.invalidateQueries({ queryKey: ['experiments', experimentId] }) },
  })
  const publish = useMutation({ mutationFn: async () => {
    const live = client.getQueryData<{ nodes: Node[]; edges: Edge[] }>(protocolGraphQueryKey(protocol!.id))
    if (live) await protocolsApi.update(protocol!.id, { graph: toPersistedGraph(live.nodes, live.edges) })
    return protocolsApi.publish(protocol!.id)
  }, onSuccess: published => {
    client.setQueryData(protocolForExperimentQueryKey(experimentId), published)
    void client.invalidateQueries({ queryKey: executionKeys.root(experimentId) })
    // A new publication changes the execution scope. Refresh its counts before confirming.
    setReruns(new Set())
  } })
  const blocked = !protocol || regenerationRequired || unboundFactors.length > 0 || bindingDiscrepancies.length > 0
  const pending = preview.data?.progress.planned ?? 0
  return <><Button size={compact ? 'xs' : 'sm'} disabled={blocked} onClick={() => { setOpen(true); setReruns(new Set()); setLock(false) }}>{label}</Button>
    <ResultJobProgress experimentId={experimentId} jobId={confirmedId} />
    {open && protocol && <RunConfirmDialog scope={{ type: 'selected-cells', title: dialogTitle,
      cellCount: preview.data?.progress.cell_count ?? 0, replicateCount: preview.data?.progress.replicate_count ?? 0,
      pendingReplicateCount: summary.data?.unit === 'row' ? 0 : pending, rerunReplicateCount: reruns.size }}
      nodes={protocol.graph.nodes as unknown as Node[]} edges={protocol.graph.edges as Edge[]} queryClient={client}
      onCancel={() => setOpen(false)} onConfirm={() => run.mutate()}
      hasUnpublishedChanges={protocol.has_unpublished_changes} publishedRevision={protocol.published_revision}
      onPublishAndRun={() => publish.mutate()} isPublishing={publish.isPending} publishError={publish.error?.message}
      publishDisabled={publish.isPending || run.isPending} publishLabel="Publish & prepare preview"
      confirmLabel={label} confirmDisabled={preview.data?.status !== 'awaiting_confirmation' || !pending || run.isPending || publish.isPending}
      isConfirming={run.isPending} confirmError={run.error?.message}
      additionalContent={<div className="space-y-3">
        {preview.data?.status !== 'awaiting_confirmation' && <p role="status" className="text-xs">Preparing batch preview… {preview.data?.progress.scanned ?? 0} executions checked.</p>}
        {(preview.error || preview.data?.error) && <p role="alert" className="text-xs text-destructive">{preview.error?.message ?? preview.data?.error}</p>}
        {preview.data?.status === 'awaiting_confirmation' && <p className="font-mono text-xs">{pending} executions will be queued.</p>}
        {summary.error && <p role="alert" className="text-xs text-destructive">{summary.error.message}</p>}
        {summary.data?.unit === 'row' && <p className="font-mono text-xs">{summary.data.expected ?? '?'} expected row executions across {summary.data.replicate_count ?? 0} replicates.</p>}
        {!experiment.data?.locked_at && <label className="flex items-center gap-2 text-xs"><Checkbox checked={lock} onCheckedChange={checked => setLock(checked === true)} />Lock experiment before running</label>}
        <ExperimentDefinitionDownload experimentId={experimentId} protocol={protocol} />
        <p className="text-xs text-muted-foreground">Completed replicates are skipped unless selected to run again.</p>
        <label className="flex items-center gap-2 text-xs"><Checkbox checked={rerunAll} onCheckedChange={checked => setRerunAll(checked === true)} />Run every completed execution again</label>
        <ExecutionRows key={`${summary.data?.scope_key}:${summary.data?.source_row_count}`} experimentId={experimentId} scope={scope} cellId={cellId} summary={summary.data} onSelect={() => {}} actions={row => row.status === 'completed' && <Checkbox aria-label={`Run replicate ${row.replicate_number} again`} checked={reruns.has(row.slot_id)} disabled={rerunAll || !reruns.has(row.slot_id) && reruns.size >= 100} onCheckedChange={checked => setReruns(current => { const next = new Set(current); if (checked) next.add(row.slot_id); else next.delete(row.slot_id); return next })} />} />
      </div>} />}
  </>
}

function StopRunsButton({
  protocol,
  experimentId,
  runIds,
  all = false,
}: {
  protocol: Protocol | undefined
  experimentId: string
  runIds: Array<string | null | undefined>
  all?: boolean
}) {
  const queryClient = useQueryClient()
  const uniqueRunIds = [...new Set(runIds.filter((id): id is string => !!id))]
  const stopMutation = useMutation({
    mutationFn: () => Promise.all(uniqueRunIds.map((runId) => protocolsApi.cancelRun(protocol!.id, runId))),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'runs'] })
      queryClient.invalidateQueries({ queryKey: ['protocols', protocol!.id, 'runs'] })
    },
  })

  if (!protocol || uniqueRunIds.length === 0) return null
  const label = all || uniqueRunIds.length > 1 ? 'Stop all' : 'Stop'
  return (
    <Button
      variant="outline"
      size="xs"
      className="h-5 border-destructive/40 px-1.5 text-[0.65rem] text-destructive hover:bg-destructive/10 hover:text-destructive"
      disabled={stopMutation.isPending}
      onClick={() => stopMutation.mutate()}
    >
      <Square className="size-3" /> {stopMutation.isPending ? 'Stopping…' : label}
    </Button>
  )
}

// A deliberate one-replicate run is different from the batch's resume
// behavior: selecting Run here means run this exact replicate, even if it
// already has a result. The confirmation makes that cost-bearing choice
// explicit before the request reaches the executor.
function RunReplicateButton({
  protocol,
  experimentId,
  replicateLabel,
  replicateNumber,
  hasCompletedRun,
  truncated = false,
  activeRunId,
  regenerationRequired,
  unboundFactors,
  bindingDiscrepancies,
  row,
}: {
  protocol: Protocol | undefined
  experimentId: string
  replicateLabel: string
  replicateNumber: number
  hasCompletedRun: boolean
  // This replicate's last attempt ran out of iterations -- worth saying before
  // a re-run at the unchanged cap repeats it. See RunAllCellsButton's own.
  truncated?: boolean
  activeRunId: string | null
  regenerationRequired: boolean
  unboundFactors: string[]
  bindingDiscrepancies: FactorBindingDiscrepancy[]
  row?: ExecutionSlot
}) {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [confirmedId, setConfirmedId] = useState<string>()
  const preview = useBatchPreview(experimentId,
    { protocol_id: protocol?.id, protocol_revision_id: protocol?.published_revision_id ?? undefined },
    { replicate_ids: row ? [row.replicate_id] : undefined,
      row_indices: row?.row_index != null ? [row.row_index] : undefined,
      rerun_slot_ids: hasCompletedRun && row ? [row.slot_id] : [] }, dialogOpen && !!row)
  const runMutation = useMutation({
    mutationFn: async () => {
      if (row) return experimentsApi.confirmResultJob(experimentId, preview.data!.id)
      await protocolsApi.run(protocol!.id, replicateLabel)
    },
    onSuccess: job => {
      if (job) setConfirmedId(job.id)
      queryClient.invalidateQueries({ queryKey: executionKeys.root(experimentId) })
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'replicates'] })
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'runs'] })
      queryClient.invalidateQueries({ queryKey: ['protocols', protocol!.id, 'runs'] })
      setDialogOpen(false)
    },
  })
  const publishAndRunMutation = useMutation({
    mutationFn: async () => {
      const live = queryClient.getQueryData<{ nodes: Node[]; edges: Edge[] }>(protocolGraphQueryKey(protocol!.id))
      if (live) await protocolsApi.update(protocol!.id, { graph: toPersistedGraph(live.nodes, live.edges) })
      return protocolsApi.publish(protocol!.id)
    },
    onSuccess: (published) => {
      queryClient.invalidateQueries({ queryKey: ['protocols', protocol!.id, 'revisions'] })
      queryClient.setQueryData(protocolForExperimentQueryKey(experimentId), published)
    },
  })
  const runBlocked = !protocol || regenerationRequired || unboundFactors.length > 0 || bindingDiscrepancies.length > 0
  const blockedTitle = regenerationRequired
    ? 'Design changed — review and regenerate before running this replicate.'
    : unboundFactors.length > 0
      ? `Rebind or remove: ${unboundFactors.join(', ')}.`
      : bindingDiscrepancies.length > 0
        ? 'Canvas values and generated factor levels disagree. Resolve the warning in Runs before starting.'
        : undefined
  const errorMessage = runMutation.error instanceof ApiError && typeof runMutation.error.detail === 'string'
    ? runMutation.error.detail
    : runMutation.isError
      ? 'Could not start this replicate.'
      : null
  const actionLabel = hasCompletedRun ? 'Re-run' : 'Run'

  return (
    <>
      <span title={blockedTitle}>
        <ReplicateRunAction
          disabled={runBlocked || !!activeRunId}
          onClick={() => setDialogOpen(true)}
          label={`${actionLabel} replicate ${replicateNumber}`}
          title={activeRunId ? 'This replicate is already running.' : `${actionLabel} replicate`}
        >
          {actionLabel}
        </ReplicateRunAction>
      </span>
      <ResultJobProgress experimentId={experimentId} jobId={confirmedId} />
      {dialogOpen && protocol && (
        <RunConfirmDialog
          scope={{ type: 'replicate', label: `Replicate ${replicateNumber}`, title: `${actionLabel} replicate?` }}
          nodes={protocol.graph.nodes as unknown as Node[]}
          edges={protocol.graph.edges as Edge[]}
          queryClient={queryClient}
          truncationNotice={
            truncated
              ? 'This replicate stopped at the iteration limit and came back unscored. Unless you raise Max iterations on the Reason + Act node first, this run will hit the same limit.'
              : null
          }
          onCancel={() => setDialogOpen(false)}
          onConfirm={() => runMutation.mutate()}
          hasUnpublishedChanges={protocol.has_unpublished_changes}
          publishedRevision={protocol.published_revision}
          isPublishing={publishAndRunMutation.isPending}
          publishError={
            publishAndRunMutation.error instanceof ApiError && typeof publishAndRunMutation.error.detail === 'string'
              ? publishAndRunMutation.error.detail
              : publishAndRunMutation.isError
                ? 'Could not publish the latest canvas.'
                : null
          }
          onPublishAndRun={() => publishAndRunMutation.mutate()}
          publishDisabled={publishAndRunMutation.isPending || runMutation.isPending}
          publishLabel={row ? 'Publish & prepare preview' : undefined}
          confirmLabel={`${actionLabel} replicate`}
          confirmDisabled={!!row && (preview.data?.status !== 'awaiting_confirmation' || !preview.data.progress.planned)}
          additionalContent={row && <p className="text-xs">{preview.data?.status === 'awaiting_confirmation' ? `${preview.data.progress.planned ?? 0} executions will be queued.` : preview.data?.error ?? preview.error?.message ?? 'Preparing batch preview…'}</p>}
          isConfirming={runMutation.isPending}
          confirmError={errorMessage}
        />
      )}
      {activeRunId && <StopRunsButton protocol={protocol} experimentId={experimentId} runIds={[activeRunId]} />}
    </>
  )
}

export function RunsTab({ experimentId, designSpec, protocol, regenerationRequired, unboundFactors, onViewResult, onViewRowResult, onSelectResult }: {
  experimentId: string; designSpec: Experiment['design_spec']; protocol: Protocol | undefined
  regenerationRequired: boolean; unboundFactors: string[]; onViewResult: (label: string) => void
  onViewRowResult?: (id: string) => void; onSelectResult?: (selection: ResultsSelection) => void
}) {
  const discrepancies = factorBindingDiscrepancies(designSpec, protocol?.graph)
  const scope = { protocol_id: protocol?.id, protocol_revision_id: protocol?.published_revision_id ?? undefined }
  return <>
    {discrepancies.length > 0 && <div role="alert" className="m-3 flex gap-2 rounded border border-[color:var(--chart-4)]/50 p-3 text-xs"><AlertTriangle className="size-4" /><div><p className="font-medium">Canvas and generated runs disagree</p>{discrepancies.map(issue => <p key={`${issue.nodeId}:${issue.fieldPath}`}>{issue.nodeLabel} · {issue.factorName}: {issue.reason}</p>)}</div></div>}
    <ExecutionBrowser key={JSON.stringify(scope)} experimentId={experimentId} scope={scope} mode="runs"
      onSelect={selection => {
        if (onSelectResult) onSelectResult(selection)
        else if (selection.type === 'row') onViewRowResult?.(selection.rowResultId)
        else if (selection.type === 'replicate') onViewResult(selection.replicateLabel)
      }}
      controls={<><StopAllButton protocol={protocol} experimentId={experimentId} /><RunAllCellsButton protocol={protocol} experimentId={experimentId} regenerationRequired={regenerationRequired} unboundFactors={unboundFactors} bindingDiscrepancies={discrepancies} /></>}
      cellActions={cell => <><RunAllCellsButton protocol={protocol} experimentId={experimentId} cellId={cell.cell_id} label="Run cell" compact regenerationRequired={regenerationRequired} unboundFactors={unboundFactors} bindingDiscrepancies={discrepancies} /><StopAllButton protocol={protocol} experimentId={experimentId} cellId={cell.cell_id} /></>}
      actions={row => <RunReplicateButton row={row} protocol={protocol} experimentId={experimentId} replicateLabel={row.replicate_label} replicateNumber={row.replicate_number}
        hasCompletedRun={row.status === 'completed'} truncated={row.result.truncated}
        activeRunId={['pending','running','finalizing'].includes(row.status) ? row.run_id : null}
        regenerationRequired={regenerationRequired} unboundFactors={unboundFactors} bindingDiscrepancies={discrepancies} />}
    />
  </>
}

function StopAllButton({ protocol, experimentId, cellId }: { protocol?: Protocol; experimentId: string; cellId?: string }) {
  const [jobId, setJobId] = useState<string>()
  const stop = useMutation({ mutationFn: () => experimentsApi.createResultJob(experimentId,
    { protocol_id: protocol?.id, protocol_revision_id: protocol?.published_revision_id ?? undefined },
    { kind: 'cancel', cell_id: cellId, request_key: crypto.randomUUID() }), onSuccess: job => setJobId(job.id) })
  return <><Button variant="destructive" size="xs" disabled={!protocol || stop.isPending} onClick={() => stop.mutate()}><Square className="size-3" />{cellId ? 'Stop cell' : 'Stop all'}</Button>
    <ResultJobProgress experimentId={experimentId} jobId={jobId} />
    {stop.error && <p role="alert" className="text-xs text-destructive">{stop.error.message}</p>}</>
}
