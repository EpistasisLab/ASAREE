import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import { ChevronDown, Download, Lock, Square } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, experimentsApi, protocolsApi } from '@/api/client'
import { displayFactorValue, factorValueKey, groupReplicatesIntoCells, type ExperimentalCell } from '@/lib/experiment'
import { protocolForExperimentQueryKey } from '@/lib/protocolGraph'
import type { ResultCell, ResultReplicate, Trial } from '@/types/experiments'
import type { Protocol } from '@/types/protocols'
import { RunConfirmDialog } from './RunConfirmDialog'
import { WarningBadge } from './nodes/WarningBadge'

function factorEntries(cell: ExperimentalCell): [string, unknown][] {
  // Factor ordering is meaningful when a user declared it, so preserve the
  // object order here for the condition summary. Sorting the cells themselves
  // below still gives a stable list no matter which replicate arrived first.
  return Object.entries(cell.factorValues)
}

function cellSortKey(cell: ExperimentalCell): string {
  return factorEntries(cell)
    .map(([name, value]) => `${name}:${factorValueKey(value)}`)
    .join('|')
}

// Factor names are stored as fully-qualified binding identifiers so two
// similarly named node fields never collide (e.g. Agent:Search:Enabled).
// That identity is useful to the engine, but it is not a readable treatment
// label. Keep the path as context and turn its final field/value pair into a
// sentence: "Agent · Search: Disabled" rather than "Agent:Search:Enabled:
// false".
function displayFactorCondition(name: string, value: unknown): string {
  const parts = name.split(':').map((part) => part.trim()).filter(Boolean)
  const field = parts.pop() ?? name

  if (typeof value === 'boolean' && /enabled$/i.test(field)) {
    const subject = field.replace(/\s*enabled$/i, '').trim()
    return [...parts, subject].filter(Boolean).join(' · ') + `: ${value ? 'Enabled' : 'Disabled'}`
  }

  return [...parts, field].filter(Boolean).join(' · ') + `: ${displayFactorValue(value)}`
}

function trialStatusBadge(status: Trial['status']) {
  switch (status) {
    case 'not_started':
      return { label: 'Not started', className: 'border-transparent bg-muted text-muted-foreground' }
    case 'queued':
      return { label: 'Queued', className: 'border-transparent bg-[color:var(--primary)]/10 text-[color:var(--primary)]' }
    case 'running':
      return { label: 'Running', className: 'border-transparent bg-[color:var(--primary)]/10 text-[color:var(--primary)]' }
    case 'completed':
      return { label: 'Completed', className: 'border-transparent bg-[color:var(--chart-3)]/10 text-[color:var(--chart-3)]' }
    case 'failed':
      return { label: 'Failed', className: 'border-transparent bg-destructive/10 text-destructive' }
    case 'cancelled':
      return { label: 'Cancelled', className: 'border-transparent bg-muted text-muted-foreground' }
  }
}

const OBSOLETE_TRIAL_BADGE = {
  label: 'Obsolete',
  className: 'border-transparent bg-[color:var(--chart-2)]/10 text-[color:var(--chart-2)]',
}

function formatCurrency(value: number | null): string | null {
  if (value === null || !Number.isFinite(value)) return null
  return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(value)
}

function formatNumber(value: number | null): string | null {
  if (value === null || !Number.isFinite(value)) return null
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value)
}

function formatDuration(value: number | null): string | null {
  if (value === null || !Number.isFinite(value)) return null
  if (value < 60) return `${Math.round(value)} sec`
  if (value < 3600) return `${(value / 60).toFixed(1)} min`
  return `${(value / 3600).toFixed(1)} hr`
}

function usageSummary(result: Pick<ResultCell, 'cost_usd' | 'total_tokens' | 'duration_seconds'> | Pick<ResultReplicate, 'cost_usd' | 'total_tokens' | 'duration_seconds'>): string[] {
  const tokens = formatNumber(result.total_tokens)
  return [
    formatCurrency(result.cost_usd),
    tokens ? `${tokens} tokens` : null,
    formatDuration(result.duration_seconds),
  ].filter((value): value is string => !!value)
}

// The panel's run control resumes only replicates without a current completed
// result. A completed run is meaningful even when it deliberately produced no
// score metrics, so it stays out of a later batch unless the user explicitly
// elects to re-run it (and therefore re-bill it). Obsolete results are current
// work again because they belong to an older canvas version.
export function RunAllCellsButton({
  protocol,
  experimentId,
  regenerationRequired,
  unboundFactors,
  replicateLabels,
  label = 'Run all cells',
  dialogTitle,
  compact = false,
  hasCompletedRun = false,
}: {
  protocol: Protocol | undefined
  experimentId: string
  regenerationRequired: boolean
  unboundFactors: string[]
  replicateLabels?: string[]
  label?: string
  dialogTitle?: string
  compact?: boolean
  hasCompletedRun?: boolean
}) {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedReruns, setSelectedReruns] = useState<Set<string>>(() => new Set())
  const [expandedCells, setExpandedCells] = useState<Set<string>>(() => new Set())
  const [lockBeforeRun, setLockBeforeRun] = useState(false)
  const [downloadingDefinition, setDownloadingDefinition] = useState(false)
  const [definitionDownloadError, setDefinitionDownloadError] = useState<string | null>(null)
  const experimentQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId),
  })
  const trialsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'runs'],
    queryFn: () => experimentsApi.listTrials(experimentId),
    refetchInterval: dialogOpen ? 3000 : false,
  })
  const replicatesQuery = useQuery({
    queryKey: ['experiments', experimentId, 'replicates'],
    queryFn: () => experimentsApi.listReplicates(experimentId),
  })
  const allReplicates = replicatesQuery.data ?? []
  const requestedLabels = replicateLabels ? new Set(replicateLabels) : null
  const replicates = requestedLabels
    ? allReplicates.filter((replicate) => requestedLabels.has(replicate.replicate_label))
    : allReplicates
  const activeReplicateLabels = new Set(
    (trialsQuery.data ?? [])
      .filter((trial) => trial.status === 'queued' || trial.status === 'running')
      .map((trial) => trial.replicate_label),
  )
  const completedReplicateLabels = new Set(
    (trialsQuery.data ?? [])
      .filter((trial) => trial.status === 'completed' && !trial.obsolete)
      .map((trial) => trial.replicate_label),
  )
  const obsoleteReplicateLabels = new Set(
    (trialsQuery.data ?? [])
      .filter((trial) => trial.obsolete)
      .map((trial) => trial.replicate_label),
  )
  const eligibleReplicateLabels = replicates
    .filter((replicate) => !activeReplicateLabels.has(replicate.replicate_label))
    .map((replicate) => replicate.replicate_label)
  const cellCount = groupReplicatesIntoCells(replicates).length
  const previouslyRunReplicateLabels = new Set(
    replicates
      .filter(
        (replicate) =>
          !obsoleteReplicateLabels.has(replicate.replicate_label) &&
          (!!replicate.metric_values || completedReplicateLabels.has(replicate.replicate_label)),
      )
      .map((replicate) => replicate.replicate_label),
  )
  const previouslyRunReplicates = replicates.filter((replicate) => previouslyRunReplicateLabels.has(replicate.replicate_label))
  const pendingReplicateCount = replicates.filter(
    (replicate) =>
      !previouslyRunReplicateLabels.has(replicate.replicate_label) && !activeReplicateLabels.has(replicate.replicate_label),
  ).length
  const previouslyRunCells = groupReplicatesIntoCells(
    previouslyRunReplicates.filter((replicate) => !activeReplicateLabels.has(replicate.replicate_label)),
  ).sort((a, b) =>
    cellSortKey(a).localeCompare(cellSortKey(b)),
  )

  const runMutation = useMutation({
    mutationFn: () =>
      protocolsApi.runCells(protocol!.id, {
        // Do not launch a second concurrent attempt for an in-flight
        // replicate. The Stop controls let users finish that attempt first.
        replicateLabels: eligibleReplicateLabels,
        rerunReplicateLabels: [...selectedReruns],
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'replicates'] })
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'runs'] })
      queryClient.invalidateQueries({ queryKey: ['protocols', protocol!.id, 'runs'] })
      setDialogOpen(false)
    },
  })
  const lockMutation = useMutation({
    mutationFn: () => experimentsApi.lock(experimentId),
    onSuccess: (locked) => {
      queryClient.setQueryData(['experiments', experimentId], locked)
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
      runMutation.mutate()
    },
  })

  function beginRun() {
    if (lockBeforeRun && !experimentQuery.data?.locked_at) {
      lockMutation.mutate()
      return
    }
    runMutation.mutate()
  }

  const publishAndRunMutation = useMutation({
    mutationFn: () => protocolsApi.publish(protocol!.id),
    onSuccess: (published) => {
      queryClient.setQueryData(protocolForExperimentQueryKey(experimentId), published)
      beginRun()
    },
  })

  function setReplicatesSelected(replicateLabels: string[], selected: boolean) {
    setSelectedReruns((current) => {
      const next = new Set(current)
      for (const label of replicateLabels) {
        if (selected) next.add(label)
        else next.delete(label)
      }
      return next
    })
  }

  function toggleExpanded(cellLabel: string) {
    setExpandedCells((current) => {
      const next = new Set(current)
      if (next.has(cellLabel)) next.delete(cellLabel)
      else next.add(cellLabel)
      return next
    })
  }

  function openDialog() {
    // Defaulting all previous work to unchecked makes the safe (existing)
    // choice explicit: previous results are skipped unless chosen again.
    setSelectedReruns(new Set())
    setExpandedCells(new Set())
    setLockBeforeRun(false)
    setDialogOpen(true)
  }

  async function downloadDefinition() {
    if (!protocol) return
    setDownloadingDefinition(true)
    setDefinitionDownloadError(null)
    try {
      // Read fresh rather than exporting whichever React Query snapshot
      // happens to be on screen. A portable definition should describe one
      // coherent point in time, including the currently published canvas.
      const [experiment, canvas, designRevisions] = await Promise.all([
        experimentsApi.get(experimentId),
        protocolsApi.get(protocol.id),
        experimentsApi.listDesignRevisions(experimentId),
      ])
      const revisionIds = [...new Set([canvas.published_revision_id, experiment.locked_protocol_revision_id].filter((id): id is string => !!id))]
      const revisions = await Promise.all(revisionIds.map((revisionId) => protocolsApi.getRevision(canvas.id, revisionId)))
      const revisionById = new Map(revisions.map((revision) => [revision.id, revision]))
      const publishedCanvas = canvas.published_revision_id ? revisionById.get(canvas.published_revision_id) ?? null : null
      const lockedCanvas = experiment.locked_protocol_revision_id ? revisionById.get(experiment.locked_protocol_revision_id) ?? null : null
      const payload = {
        format: 'asaree.experiment-definition',
        schema_version: 1,
        exported_at: new Date().toISOString(),
        // The existing canvas importer consumes these two top-level fields.
        // Keep them as the current draft while the richer envelope below
        // carries immutable published/locked snapshots and provenance.
        name: experiment.name,
        description: canvas.description,
        graph: canvas.graph,
        design_spec: experiment.design_spec,
        experiment: {
          source_id: experiment.id,
          name: experiment.name,
          description: experiment.description,
          hypothesis: experiment.hypothesis,
          design_type: experiment.design_type,
          task_brief: experiment.task_brief,
          design_spec: experiment.design_spec,
          dataset_ids: experiment.dataset_ids,
          archived_at: experiment.archived_at,
          created_at: experiment.created_at,
          updated_at: experiment.updated_at,
          lock: experiment.locked_at ? {
            locked_at: experiment.locked_at,
            source_protocol_revision_id: experiment.locked_protocol_revision_id,
            design_spec: experiment.locked_design_spec,
            canvas_revision: lockedCanvas ? {
              source_id: lockedCanvas.id,
              revision: lockedCanvas.revision,
              published_at: lockedCanvas.published_at,
              graph: lockedCanvas.graph,
            } : null,
          } : null,
        },
        canvas: {
          source_protocol_id: canvas.id,
          name: canvas.name,
          description: canvas.description,
          draft: { graph: canvas.graph, updated_at: canvas.updated_at },
          published: publishedCanvas ? {
            source_id: publishedCanvas.id,
            revision: publishedCanvas.revision,
            published_at: publishedCanvas.published_at,
            graph: publishedCanvas.graph,
          } : null,
          has_unpublished_changes: canvas.has_unpublished_changes,
        },
        design_revisions: designRevisions.map((revision) => ({
          source_id: revision.id,
          revision: revision.revision,
          superseded_at: revision.superseded_at,
          design_spec: revision.design_spec,
          cell_count: revision.cell_count,
          replicate_count: revision.replicate_count,
          scored_replicate_count: revision.scored_replicate_count,
          created_at: revision.created_at,
        })),
        portability: {
          excluded: ['provider credential secrets', 'replicate results and run history', 'dataset file contents', 'knowledge bundle/document contents', 'skill file contents'],
          note: 'All canvas nodes and their configuration are included. Dataset, Knowledge, and Skill nodes retain their labels and resource IDs as references; recreate or map their external artifacts before importing into another workspace.',
        },
      }
      const name = experiment.name || canvas.name
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${name.trim().replace(/[^a-z0-9]+/gi, '-').replace(/(^-|-$)/g, '') || 'experiment'}.json`
      link.click()
      URL.revokeObjectURL(url)
    } catch {
      setDefinitionDownloadError('Could not prepare the experiment definition. Please try again.')
    } finally {
      setDownloadingDefinition(false)
    }
  }

  const runBlocked =
    !protocol ||
    replicatesQuery.isLoading ||
    replicates.length === 0 ||
    (pendingReplicateCount === 0 && previouslyRunCells.length === 0) ||
    regenerationRequired ||
    unboundFactors.length > 0 ||
    !protocol.published_revision_id
  const blockedTitle = regenerationRequired
    ? 'Design changed — review and regenerate before running the experiment.'
    : unboundFactors.length > 0
      ? `Rebind or remove: ${unboundFactors.join(', ')}.`
      : !protocol?.published_revision_id
        ? 'Publish a valid canvas before running cells.'
        : replicates.length === 0
          ? 'Generate design first — there are no cells to run yet.'
          : pendingReplicateCount === 0 && previouslyRunCells.length === 0
            ? 'All selected replicates are already running.'
          : undefined
  const runnableReplicateCount = pendingReplicateCount + selectedReruns.size
  const actionLabel = label ?? (hasCompletedRun ? 'Re-run all cells' : 'Run all cells')
  const errorMessage = lockMutation.error instanceof ApiError && typeof lockMutation.error.detail === 'string'
    ? lockMutation.error.detail
    : lockMutation.isError
      ? 'Could not lock the experiment before starting it.'
      : runMutation.error instanceof ApiError && typeof runMutation.error.detail === 'string'
    ? runMutation.error.detail
    : runMutation.isError
      ? 'Could not start the experiment.'
      : null

  return (
    <>
      <span title={blockedTitle}>
        <Button
          size={compact ? 'xs' : 'sm'}
          disabled={runBlocked}
          onClick={openDialog}
          className={compact ? 'bg-[color:var(--chart-3)] text-primary-foreground hover:bg-[color:var(--chart-3)]/80' : undefined}
        >
          {actionLabel}
        </Button>
      </span>
      {dialogOpen && protocol && (
        <RunConfirmDialog
          scope={{
            type: 'selected-cells',
            cellCount,
            replicateCount: replicates.length,
            pendingReplicateCount,
            rerunReplicateCount: selectedReruns.size,
            title: dialogTitle,
          }}
          nodes={protocol.graph.nodes as unknown as Node[]}
          edges={protocol.graph.edges as Edge[]}
          queryClient={queryClient}
          onCancel={() => setDialogOpen(false)}
          onConfirm={beginRun}
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
          confirmLabel={actionLabel}
          confirmDisabled={runnableReplicateCount === 0}
          isConfirming={runMutation.isPending || lockMutation.isPending}
          confirmError={errorMessage}
          additionalContent={
            <div className="space-y-4">
              <section className="space-y-2 rounded-md border border-primary/25 bg-primary/5 px-3 py-2.5">
                {!experimentQuery.data?.locked_at && (
                  <div className="flex items-start gap-2">
                    <Checkbox
                      id="lock-before-run"
                      checked={lockBeforeRun}
                      onCheckedChange={(checked) => setLockBeforeRun(checked === true)}
                      className="mt-0.5"
                    />
                    <label htmlFor="lock-before-run" className="cursor-pointer text-sm">
                      <span className="flex items-center gap-1.5 font-medium"><Lock className="size-3.5" /> Lock experiment before running</span>
                      <span className="mt-0.5 block text-xs text-muted-foreground">Locks the published canvas and design so these results stay reproducible. Replicate count remains adjustable.</span>
                    </label>
                  </div>
                )}
                <Button type="button" variant="outline" size="xs" onClick={downloadDefinition} disabled={downloadingDefinition}>
                  <Download className="size-3.5" /> {downloadingDefinition ? 'Preparing definition…' : 'Download experiment definition'}
                </Button>
                {definitionDownloadError && <p className="text-xs text-destructive">{definitionDownloadError}</p>}
              </section>
              <section aria-labelledby="previous-runs-heading" className="space-y-2">
                <div>
                  <h3 id="previous-runs-heading" className="text-sm font-medium">Previously run cells</h3>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Completed replicates are skipped by default. Check a cell or replicate to run it again.
                </p>
              </div>
              {previouslyRunCells.length === 0 ? (
                <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">No completed replicates to review.</p>
              ) : (
                <div className="max-h-60 space-y-2 overflow-y-auto pr-1">
                  {previouslyRunCells.map((cell) => {
                    const labels = cell.replicates.map((replicate) => replicate.replicate_label)
                    const selectedCount = labels.filter((label) => selectedReruns.has(label)).length
                    const expanded = expandedCells.has(cell.label)
                    const summary = factorEntries(cell).map(([name, value]) => displayFactorCondition(name, value)).join(' · ') || 'Cell'
                    const listId = `rerun-cell-${cell.label}`
                    return (
                      <div key={cell.label} className="overflow-hidden rounded-md border">
                        <div className="flex items-start gap-2 px-2.5 py-2">
                          <Checkbox
                            checked={selectedCount === labels.length}
                            onCheckedChange={(checked) => setReplicatesSelected(labels, checked === true)}
                            aria-label={`Run all replicates in ${summary} again`}
                            className="mt-0.5"
                          />
                          <button
                            type="button"
                            onClick={() => toggleExpanded(cell.label)}
                            aria-expanded={expanded}
                            aria-controls={listId}
                            className="flex min-w-0 flex-1 cursor-pointer items-start gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            <ChevronDown className={`mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform ${expanded ? '' : '-rotate-90'}`} />
                            <span className="min-w-0 flex-1 truncate text-sm font-medium" title={summary}>{summary}</span>
                            <span className="shrink-0 text-xs text-muted-foreground">
                              {selectedCount}/{labels.length} selected
                            </span>
                          </button>
                        </div>
                        {expanded && (
                          <ul id={listId} className="space-y-1 border-t bg-muted/20 px-2.5 py-2" aria-label={`Previously run replicates for ${summary}`}>
                            {cell.replicates.map((replicate) => {
                              const scored = !!replicate.metric_values
                              return (
                                <li key={replicate.id} className="flex items-center gap-2 rounded-md bg-background px-2 py-1.5">
                                  <Checkbox
                                    checked={selectedReruns.has(replicate.replicate_label)}
                                    onCheckedChange={(checked) => setReplicatesSelected([replicate.replicate_label], checked === true)}
                                    aria-label={`Run replicate ${replicate.replicate_number} again`}
                                  />
                                  <span className="text-sm">Replicate {replicate.replicate_number}</span>
                                  <span className="ml-auto text-xs text-muted-foreground">
                                    {scored ? 'Scored' : 'Completed without metrics'}
                                  </span>
                                </li>
                              )
                            })}
                          </ul>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
              {runnableReplicateCount === 0 && (
                <p className="text-xs text-muted-foreground">Select at least one previously run replicate to run again.</p>
              )}
              </section>
            </div>
          }
        />
      )}
    </>
  )
}

// A protocol run is the actual cancellable unit. A cell/experiment stop is
// simply this same operation applied to each of its active replicate runs.
// The API only raises a durable cancellation flag; polling below keeps the
// visible status in sync until the worker reaches a safe interruption point.
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
  activeRunId,
  regenerationRequired,
  unboundFactors,
}: {
  protocol: Protocol | undefined
  experimentId: string
  replicateLabel: string
  replicateNumber: number
  hasCompletedRun: boolean
  activeRunId: string | null
  regenerationRequired: boolean
  unboundFactors: string[]
}) {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const runMutation = useMutation({
    mutationFn: () => protocolsApi.run(protocol!.id, replicateLabel),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'replicates'] })
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'runs'] })
      queryClient.invalidateQueries({ queryKey: ['protocols', protocol!.id, 'runs'] })
      setDialogOpen(false)
    },
  })
  const publishAndRunMutation = useMutation({
    mutationFn: () => protocolsApi.publish(protocol!.id),
    onSuccess: (published) => {
      queryClient.setQueryData(protocolForExperimentQueryKey(experimentId), published)
      runMutation.mutate()
    },
  })
  const runBlocked = !protocol || regenerationRequired || unboundFactors.length > 0 || !protocol.published_revision_id
  const blockedTitle = regenerationRequired
    ? 'Design changed — review and regenerate before running this replicate.'
    : unboundFactors.length > 0
      ? `Rebind or remove: ${unboundFactors.join(', ')}.`
      : !protocol?.published_revision_id
        ? 'Publish a valid canvas before running replicates.'
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
        <Button
          size="xs"
          disabled={runBlocked || !!activeRunId}
          onClick={() => setDialogOpen(true)}
          aria-label={`${actionLabel} replicate ${replicateNumber}`}
          title={activeRunId ? 'This replicate is already running.' : `${actionLabel} replicate`}
          className="h-5 px-1.5 text-[0.65rem]"
        >
          {actionLabel}
        </Button>
      </span>
      {dialogOpen && protocol && (
        <RunConfirmDialog
          scope={{ type: 'replicate', label: `Replicate ${replicateNumber}`, title: `${actionLabel} replicate?` }}
          nodes={protocol.graph.nodes as unknown as Node[]}
          edges={protocol.graph.edges as Edge[]}
          queryClient={queryClient}
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

// This view deliberately begins at the design's natural unit: a cell is one
// unique factor combination, regardless of how many independently-run
// replicates it contains. A cell expands in place to reveal those replicates,
// preserving the high-level overview instead of replacing it with a dialog.
export function RunsTab({
  experimentId,
  protocol,
  regenerationRequired,
  unboundFactors,
  onViewResult,
}: {
  experimentId: string
  protocol: Protocol | undefined
  regenerationRequired: boolean
  unboundFactors: string[]
  onViewResult: (replicateLabel: string) => void
}) {
  const [expandedCells, setExpandedCells] = useState<Set<string>>(() => new Set())
  const replicatesQuery = useQuery({
    queryKey: ['experiments', experimentId, 'replicates'],
    queryFn: () => experimentsApi.listReplicates(experimentId),
  })
  const trialsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'runs'],
    queryFn: () => experimentsApi.listTrials(experimentId),
    // Obsolescence is surfaced at the Cells and cell-header levels, not only
    // inside an expanded replicate list, so load this while Runs is visible.
    enabled: true,
    refetchInterval: 3000,
  })
  const resultsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'run-results'],
    queryFn: () => experimentsApi.getRunResults(experimentId),
    refetchInterval: 5000,
  })

  if (replicatesQuery.isLoading) {
    return (
      <div className="space-y-3 p-3">
        <Skeleton className="h-5 w-20" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    )
  }

  if (replicatesQuery.isError || !replicatesQuery.data) {
    return <p className="p-3 text-sm text-muted-foreground">Could not load this experiment’s cells.</p>
  }

  const cells = groupReplicatesIntoCells(replicatesQuery.data).sort((a, b) => cellSortKey(a).localeCompare(cellSortKey(b)))
  const trialsByLabel = new Map((trialsQuery.data ?? []).map((trial) => [trial.replicate_label, trial]))
  const cellResultsByLabel = new Map((resultsQuery.data?.cells ?? []).map((cell) => [cell.cell_label, cell]))
  const replicateResultsByLabel = new Map((resultsQuery.data?.replicates ?? []).map((replicate) => [replicate.replicate_label, replicate]))
  const obsoleteCells = cells.filter((cell) => cell.replicates.some((replicate) => trialsByLabel.get(replicate.replicate_label)?.obsolete))
  const obsoleteReplicateCount = cells.reduce(
    (count, cell) => count + cell.replicates.filter((replicate) => trialsByLabel.get(replicate.replicate_label)?.obsolete).length,
    0,
  )
  // Runs stays operational rather than becoming a second Results dashboard:
  // this one compact line answers whether there is work in flight, while
  // comparison metrics, spend, and outputs stay in the Results rail item.
  const trials = [...trialsByLabel.values()]
  const currentTrialCount = trials.filter((trial) => !trial.obsolete).length
  const completedCount = trials.filter((trial) => trial.status === 'completed' && !trial.obsolete).length
  const runningCount = trials.filter((trial) => trial.status === 'running').length
  const queuedCount = trials.filter((trial) => trial.status === 'queued').length
  const failedCount = trials.filter((trial) => trial.status === 'failed' || trial.status === 'cancelled').length
  const overviewUsage = resultsQuery.data ? usageSummary({
    cost_usd: resultsQuery.data.overview.total_cost_usd,
    total_tokens: resultsQuery.data.overview.total_tokens,
    duration_seconds: resultsQuery.data.overview.total_duration_seconds,
  }) : []
  const isActiveTrial = (trial: Trial | undefined): trial is Trial =>
    !!trial?.run_id && (trial.status === 'queued' || trial.status === 'running')
  const activeExperimentRunIds = trials.filter(isActiveTrial).map((trial) => trial.run_id)
  // Completion is the user-visible truth here. A result can be completed
  // from persisted metrics even when it has no ProtocolRun provenance (for
  // example, imported or notebook-scored results), so run_id is not a valid
  // test for whether this scope has already been run.
  const hasFinishedRun = (trial: Trial | undefined) =>
    !!trial && trial.status !== 'not_started' && !isActiveTrial(trial)
  const experimentHasCompletedRun = cells.length > 0 && cells.every((cell) =>
    cell.replicates.every((replicate) => hasFinishedRun(trialsByLabel.get(replicate.replicate_label))),
  )

  function toggleCell(cellLabel: string) {
    setExpandedCells((current) => {
      const next = new Set(current)
      if (next.has(cellLabel)) next.delete(cellLabel)
      else next.add(cellLabel)
      return next
    })
  }

  if (cells.length === 0) {
    return <p className="p-3 text-sm text-muted-foreground">No cells yet — generate this experiment’s design first.</p>
  }

  return (
    <section className="space-y-1.5 p-3" aria-labelledby="run-cells-heading">
      <div className="space-y-0.5">
        <div className="flex items-center gap-2">
          <h2 id="run-cells-heading" className="text-sm font-medium">Cells</h2>
          {obsoleteReplicateCount > 0 && (
            <WarningBadge
              issues={`${obsoleteReplicateCount} replicate${obsoleteReplicateCount === 1 ? '' : 's'} across ${obsoleteCells.length} cell${obsoleteCells.length === 1 ? '' : 's'} ran against an older published canvas version.`}
              className="flex size-4 shrink-0 items-center justify-center rounded-full bg-card ring-1 ring-[color:var(--chart-4)]/40"
            />
          )}
          <RunAllCellsButton
            protocol={protocol}
            experimentId={experimentId}
            regenerationRequired={regenerationRequired}
            unboundFactors={unboundFactors}
            hasCompletedRun={experimentHasCompletedRun}
            dialogTitle={experimentHasCompletedRun ? 'Re-run all cells?' : 'Run all cells?'}
          />
          <StopRunsButton protocol={protocol} experimentId={experimentId} runIds={activeExperimentRunIds} all />
          <span className="ml-auto font-mono text-xs text-muted-foreground">{cells.length}</span>
        </div>
        <p className="text-xs text-muted-foreground" aria-live="polite">
          {completedCount}/{currentTrialCount || (trials.length === 0 ? replicatesQuery.data.length : 0)} current complete
          {runningCount > 0 && ` · ${runningCount} running`}
          {queuedCount > 0 && ` · ${queuedCount} queued`}
          {failedCount > 0 && ` · ${failedCount} failed`}
        </p>
        {overviewUsage.length > 0 && (
          <p className="flex flex-wrap gap-x-2 text-xs text-muted-foreground" title="Reported usage across current, non-obsolete replicate results.">
            <span>Current usage</span>
            {overviewUsage.map((value) => <span key={value}>{value}</span>)}
          </p>
        )}
      </div>
      <div className="space-y-2">
        {cells.map((cell) => {
          const entries = factorEntries(cell)
          const summary = entries.slice(0, 2)
          const remaining = entries.slice(2)
          const expanded = expandedCells.has(cell.label)
          const replicateListId = `cell-${cell.label}-replicates`
          const obsoleteCount = cell.replicates.filter((replicate) => trialsByLabel.get(replicate.replicate_label)?.obsolete).length
          const cellResult = cellResultsByLabel.get(cell.label)
          const cellUsage = cellResult ? usageSummary(cellResult) : []
          const activeCellRunIds = cell.replicates
            .map((replicate) => trialsByLabel.get(replicate.replicate_label))
            .filter(isActiveTrial)
            .map((trial) => trial.run_id)
          const cellHasCompletedRun = cell.replicates.every((replicate) =>
            hasFinishedRun(trialsByLabel.get(replicate.replicate_label)),
          )
          return (
            <div key={cell.label} className="overflow-hidden rounded-md border">
              <div className="flex items-start gap-2 px-3 py-2.5">
                <button
                  type="button"
                  onClick={() => toggleCell(cell.label)}
                  aria-expanded={expanded}
                  aria-controls={replicateListId}
                  className="flex min-w-0 flex-1 cursor-pointer items-start gap-3 text-left transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <ChevronDown className={`mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform ${expanded ? '' : '-rotate-90'}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="min-w-0 flex-1 truncate text-sm font-medium" title={summary.map(([name, value]) => displayFactorCondition(name, value)).join(' · ')}>
                        {summary.length > 0
                          ? summary.map(([name, value]) => displayFactorCondition(name, value)).join(' · ')
                          : 'Cell'}
                      </p>
                      <Badge variant="outline" className="shrink-0 border-[color:var(--chart-2)] text-[color:var(--chart-2)]">
                        {cell.replicates.length} {cell.replicates.length === 1 ? 'replicate' : 'replicates'}
                      </Badge>
                      {obsoleteCount > 0 && (
                        <WarningBadge
                          issues={`${obsoleteCount} replicate${obsoleteCount === 1 ? '' : 's'} ran against an older published canvas version.`}
                          className="flex size-4 shrink-0 items-center justify-center rounded-full bg-card ring-1 ring-[color:var(--chart-4)]/40"
                        />
                      )}
                    </div>
                    {remaining.length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {remaining.map(([name, value]) => (
                          <Badge key={name} variant="outline" className="max-w-full font-mono text-[0.65rem] font-normal">
                            <span className="truncate">{displayFactorCondition(name, value)}</span>
                          </Badge>
                        ))}
                      </div>
                    )}
                    <p className="mt-1 font-mono text-[0.65rem] text-muted-foreground" title={cell.label}>Cell ID: {cell.label}</p>
                    {cellResult && (
                      <p className="mt-1 flex flex-wrap gap-x-2 text-xs text-muted-foreground">
                        <span>{cellResult.current_completed_count}/{cellResult.replicate_count} current complete</span>
                        {cellUsage.map((value) => <span key={value}>{value}</span>)}
                      </p>
                    )}
                  </div>
                </button>
                <div className="flex shrink-0 flex-col items-end gap-1.5">
                  <RunAllCellsButton
                    protocol={protocol}
                    experimentId={experimentId}
                    regenerationRequired={regenerationRequired}
                    unboundFactors={unboundFactors}
                    replicateLabels={cell.replicates.map((replicate) => replicate.replicate_label)}
                    label={cellHasCompletedRun ? 'Re-run all replicates' : 'Run all replicates'}
                    dialogTitle={cellHasCompletedRun ? 'Re-run all replicates?' : 'Run all replicates?'}
                    compact
                  />
                  <StopRunsButton protocol={protocol} experimentId={experimentId} runIds={activeCellRunIds} all />
                </div>
              </div>

              {expanded && (
                <div id={replicateListId} className="border-t bg-muted/20 px-3 py-2.5">
                  {trialsQuery.isLoading ? (
                    <div className="space-y-2">
                      <Skeleton className="h-12 w-full" />
                      <Skeleton className="h-12 w-full" />
                    </div>
                  ) : (
                    <ul className="space-y-1.5" aria-label={`Replicates for ${cell.label}`}>
                      {cell.replicates.map((replicate) => {
                        const trial = trialsByLabel.get(replicate.replicate_label)
                        const replicateResult = replicateResultsByLabel.get(replicate.replicate_label)
                        const replicateUsage = replicateResult ? usageSummary(replicateResult) : []
                        const badge = trial ? (trial.obsolete ? OBSOLETE_TRIAL_BADGE : trialStatusBadge(trial.status)) : null
                        return (
                          <li key={replicate.id} className="rounded-md border bg-background px-2.5 py-2">
                            <div className="flex items-center justify-between gap-3">
                              <div className="min-w-0">
                                <div className="flex items-center gap-1.5">
                                  <p className="text-sm font-medium">Replicate {replicate.replicate_number}</p>
                                  {trial?.obsolete && (
                                    <WarningBadge
                                      issues="This replicate ran against an older published canvas version. Run it again to produce a current result."
                                      className="flex size-4 shrink-0 items-center justify-center rounded-full bg-card ring-1 ring-[color:var(--chart-4)]/40"
                                    />
                                  )}
                                </div>
                                {replicateUsage.length > 0 && <p className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-muted-foreground">{replicateUsage.map((value) => <span key={value}>{value}</span>)}</p>}
                              </div>
                              <div className="flex shrink-0 items-center gap-2">
                                {badge ? <Badge className={badge.className}>{badge.label}</Badge> : <Badge variant="outline">Status unavailable</Badge>}
                                {trial?.run_id && (
                                  <Button
                                    variant="outline"
                                    size="xs"
                                    className="h-5 px-1.5 text-[0.65rem]"
                                    onClick={() => onViewResult(replicate.replicate_label)}
                                  >
                                    View result
                                  </Button>
                                )}
                                <RunReplicateButton
                                  protocol={protocol}
                                  experimentId={experimentId}
                                  replicateLabel={replicate.replicate_label}
                                  replicateNumber={replicate.replicate_number}
                                  hasCompletedRun={hasFinishedRun(trial)}
                                  activeRunId={isActiveTrial(trial) ? trial.run_id : null}
                                  regenerationRequired={regenerationRequired}
                                  unboundFactors={unboundFactors}
                                />
                              </div>
                            </div>
                            {trial?.error && <p className="mt-2 rounded bg-destructive/10 px-2 py-1.5 text-xs text-destructive">{trial.error}</p>}
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}
