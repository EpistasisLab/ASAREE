import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import { AlertTriangle, Square } from 'lucide-react'
import { ApiError, experimentsApi, protocolsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { factorBindingDiscrepancies, type FactorBindingDiscrepancy } from '@/lib/factorBindings'
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

export function RunAllCellsButton({ protocol, experimentId, regenerationRequired, unboundFactors, bindingDiscrepancies, label = 'Run all cells', dialogTitle, compact = false, replicateLabels }: {
  protocol: Protocol | undefined; experimentId: string; regenerationRequired: boolean; unboundFactors: string[]
  bindingDiscrepancies: FactorBindingDiscrepancy[]; label?: string; dialogTitle?: string; compact?: boolean
  replicateLabels?: string[]; hasCompletedRun?: boolean
}) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [lock, setLock] = useState(false)
  const [reruns, setReruns] = useState<Set<string>>(() => new Set())
  const scope = { protocol_id: protocol?.id, protocol_revision_id: protocol?.published_revision_id ?? undefined }
  const summary = useExecutionSummary(experimentId, scope, open && !!protocol)
  const experiment = useQuery({ queryKey: ['experiments', experimentId], queryFn: () => experimentsApi.get(experimentId), enabled: open })
  const run = useMutation({
    mutationFn: async () => {
      if (lock && !experiment.data?.locked_at) await experimentsApi.lock(experimentId)
      return protocolsApi.runCells(protocol!.id, { replicateLabels, rerunReplicateLabels: [...reruns] })
    },
    onSuccess: () => { setOpen(false); void client.invalidateQueries({ queryKey: ['experiments', experimentId] }) },
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
  const pending = (summary.data?.counts?.not_started ?? 0) + (summary.data?.counts?.failed ?? 0) + (summary.data?.counts?.cancelled ?? 0)
  return <><Button size={compact ? 'xs' : 'sm'} disabled={blocked} onClick={() => { setOpen(true); setReruns(new Set()); setLock(false) }}>{label}</Button>
    {open && protocol && <RunConfirmDialog scope={{ type: 'selected-cells', title: dialogTitle,
      cellCount: summary.data?.cell_count ?? 0, replicateCount: summary.data?.replicate_count ?? 0,
      pendingReplicateCount: summary.data?.unit === 'row' ? 0 : pending, rerunReplicateCount: reruns.size }}
      nodes={protocol.graph.nodes as unknown as Node[]} edges={protocol.graph.edges as Edge[]} queryClient={client}
      onCancel={() => setOpen(false)} onConfirm={() => run.mutate()}
      hasUnpublishedChanges={protocol.has_unpublished_changes} publishedRevision={protocol.published_revision}
      onPublishAndRun={() => publish.mutate()} isPublishing={publish.isPending} publishError={publish.error?.message}
      confirmLabel={label} confirmDisabled={!summary.data?.ready || run.isPending || publish.isPending}
      isConfirming={run.isPending} confirmError={run.error?.message}
      additionalContent={<div className="space-y-3">
        {!summary.data?.ready && <p role="status" className="text-xs">Preparing execution counts…</p>}
        {summary.error && <p role="alert" className="text-xs text-destructive">{summary.error.message}</p>}
        {summary.data?.unit === 'row' && <p className="font-mono text-xs">{summary.data.expected ?? '?'} expected row executions across {summary.data.replicate_count ?? 0} replicates.</p>}
        {!experiment.data?.locked_at && <label className="flex items-center gap-2 text-xs"><Checkbox checked={lock} onCheckedChange={checked => setLock(checked === true)} />Lock experiment before running</label>}
        <ExperimentDefinitionDownload experimentId={experimentId} protocol={protocol} />
        <p className="text-xs text-muted-foreground">Completed replicates are skipped unless selected to run again.</p>
        <ExecutionRows key={summary.data?.scope_key} experimentId={experimentId} scope={scope} summary={summary.data} onSelect={() => {}} actions={row => row.status === 'completed' && <Checkbox aria-label={`Run replicate ${row.replicate_number} again`} checked={reruns.has(row.replicate_label)} onCheckedChange={checked => setReruns(current => { const next = new Set(current); if (checked) next.add(row.replicate_label); else next.delete(row.replicate_label); return next })} />} />
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
  const runMutation = useMutation({
    mutationFn: async () => {
      if (row?.row_index != null) await protocolsApi.runCells(protocol!.id, row.row_result_id && ['failed', 'cancelled'].includes(row.status)
        ? { retry_row_result_ids: [row.row_result_id] }
        : { replicateLabels: [replicateLabel], row_indices: [row.row_index], rerunReplicateLabels: hasCompletedRun ? [replicateLabel] : [] })
      else await protocolsApi.run(protocol!.id, replicateLabel)
    },
    onSuccess: () => {
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
      runMutation.mutate()
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
          confirmLabel={`${actionLabel} replicate`}
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
      controls={<RunAllCellsButton protocol={protocol} experimentId={experimentId} regenerationRequired={regenerationRequired} unboundFactors={unboundFactors} bindingDiscrepancies={discrepancies} />}
      actions={row => <RunReplicateButton row={row} protocol={protocol} experimentId={experimentId} replicateLabel={row.replicate_label} replicateNumber={row.replicate_number}
        hasCompletedRun={row.status === 'completed'} truncated={row.result.truncated}
        activeRunId={['pending','running','finalizing'].includes(row.status) ? row.run_id : null}
        regenerationRequired={regenerationRequired} unboundFactors={unboundFactors} bindingDiscrepancies={discrepancies} />}
    />
  </>
}
