import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import { ChevronDown } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, experimentsApi, protocolsApi } from '@/api/client'
import { displayFactorValue, factorValueKey, groupReplicatesIntoCells, type ExperimentalCell } from '@/lib/experiment'
import { protocolForExperimentQueryKey } from '@/lib/protocolGraph'
import type { Trial } from '@/types/experiments'
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

// The panel's run control deliberately keeps the resume behavior of the
// canvas's "Run all cells" action: unscored replicates run automatically.
// This confirmation adds one decision only -- whether a previously scored
// replicate should be deliberately re-run (and therefore re-billed).
export function RunAllCellsButton({
  protocol,
  experimentId,
  regenerationRequired,
  unboundFactors,
  replicateLabels,
  label = 'Run all cells',
  dialogTitle,
  compact = false,
}: {
  protocol: Protocol | undefined
  experimentId: string
  regenerationRequired: boolean
  unboundFactors: string[]
  replicateLabels?: string[]
  label?: string
  dialogTitle?: string
  compact?: boolean
}) {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedReruns, setSelectedReruns] = useState<Set<string>>(() => new Set())
  const [expandedCells, setExpandedCells] = useState<Set<string>>(() => new Set())
  const replicatesQuery = useQuery({
    queryKey: ['experiments', experimentId, 'replicates'],
    queryFn: () => experimentsApi.listReplicates(experimentId),
  })
  const allReplicates = replicatesQuery.data ?? []
  const requestedLabels = replicateLabels ? new Set(replicateLabels) : null
  const replicates = requestedLabels
    ? allReplicates.filter((replicate) => requestedLabels.has(replicate.replicate_label))
    : allReplicates
  const cellCount = groupReplicatesIntoCells(replicates).length
  const pendingReplicateCount = replicates.filter((replicate) => !replicate.metric_values).length
  const scoredCells = groupReplicatesIntoCells(replicates.filter((replicate) => !!replicate.metric_values)).sort((a, b) =>
    cellSortKey(a).localeCompare(cellSortKey(b)),
  )

  const runMutation = useMutation({
    mutationFn: () =>
      protocolsApi.runCells(protocol!.id, {
        replicateLabels,
        rerunReplicateLabels: [...selectedReruns],
      }),
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
    setDialogOpen(true)
  }

  const runBlocked =
    !protocol ||
    replicatesQuery.isLoading ||
    replicates.length === 0 ||
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
          : undefined
  const runnableReplicateCount = pendingReplicateCount + selectedReruns.size
  const errorMessage = runMutation.error instanceof ApiError && typeof runMutation.error.detail === 'string'
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
          {label}
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
          confirmLabel={label}
          confirmDisabled={runnableReplicateCount === 0}
          isConfirming={runMutation.isPending}
          confirmError={errorMessage}
          additionalContent={
            <section aria-labelledby="previous-runs-heading" className="space-y-2">
              <div>
                <h3 id="previous-runs-heading" className="text-sm font-medium">Previously run cells</h3>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Previous results are skipped by default. Check a cell or replicate to run it again.
                </p>
              </div>
              {scoredCells.length === 0 ? (
                <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">No completed replicates to review.</p>
              ) : (
                <div className="max-h-60 space-y-2 overflow-y-auto pr-1">
                  {scoredCells.map((cell) => {
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
                            {cell.replicates.map((replicate) => (
                              <li key={replicate.id} className="flex items-center gap-2 rounded-md bg-background px-2 py-1.5">
                                <Checkbox
                                  checked={selectedReruns.has(replicate.replicate_label)}
                                  onCheckedChange={(checked) => setReplicatesSelected([replicate.replicate_label], checked === true)}
                                  aria-label={`Run replicate ${replicate.replicate_number} again`}
                                />
                                <span className="text-sm">Replicate {replicate.replicate_number}</span>
                              </li>
                            ))}
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
          }
        />
      )}
    </>
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
  regenerationRequired,
  unboundFactors,
}: {
  protocol: Protocol | undefined
  experimentId: string
  replicateLabel: string
  replicateNumber: number
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

  return (
    <>
      <span title={blockedTitle}>
        <Button
          size="xs"
          disabled={runBlocked}
          onClick={() => setDialogOpen(true)}
          aria-label={`Run replicate ${replicateNumber}`}
          title="Run replicate"
          className="h-5 px-1.5 text-[0.65rem]"
        >
          Run
        </Button>
      </span>
      {dialogOpen && protocol && (
        <RunConfirmDialog
          scope={{ type: 'replicate', label: `Replicate ${replicateNumber}` }}
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
          confirmLabel="Run replicate"
          isConfirming={runMutation.isPending}
          confirmError={errorMessage}
        />
      )}
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
}: {
  experimentId: string
  protocol: Protocol | undefined
  regenerationRequired: boolean
  unboundFactors: string[]
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
  const obsoleteCells = cells.filter((cell) => cell.replicates.some((replicate) => trialsByLabel.get(replicate.replicate_label)?.obsolete))
  const obsoleteReplicateCount = cells.reduce(
    (count, cell) => count + cell.replicates.filter((replicate) => trialsByLabel.get(replicate.replicate_label)?.obsolete).length,
    0,
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
        />
        <span className="ml-auto font-mono text-xs text-muted-foreground">{cells.length}</span>
      </div>
      <div className="space-y-2">
        {cells.map((cell) => {
          const entries = factorEntries(cell)
          const summary = entries.slice(0, 2)
          const remaining = entries.slice(2)
          const expanded = expandedCells.has(cell.label)
          const replicateListId = `cell-${cell.label}-replicates`
          const obsoleteCount = cell.replicates.filter((replicate) => trialsByLabel.get(replicate.replicate_label)?.obsolete).length
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
                  </div>
                </button>
                <div className="flex shrink-0 flex-col items-end gap-1.5">
                  <RunAllCellsButton
                    protocol={protocol}
                    experimentId={experimentId}
                    regenerationRequired={regenerationRequired}
                    unboundFactors={unboundFactors}
                    replicateLabels={cell.replicates.map((replicate) => replicate.replicate_label)}
                    label="Run all replicates"
                    dialogTitle="Run all replicates?"
                    compact
                  />
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
                              </div>
                              <div className="flex shrink-0 items-center gap-2">
                                {badge ? <Badge className={badge.className}>{badge.label}</Badge> : <Badge variant="outline">Status unavailable</Badge>}
                                <RunReplicateButton
                                  protocol={protocol}
                                  experimentId={experimentId}
                                  replicateLabel={replicate.replicate_label}
                                  replicateNumber={replicate.replicate_number}
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
