import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ReactFlowProvider, type Edge, type Node } from '@xyflow/react'
import { Square, Target, Trophy, type LucideIcon } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { AppHeader } from '@/components/AppHeader'
import { ExperimentSidePanel } from '@/components/protocol/ExperimentSidePanel'
import { ProtocolCanvas, type ProtocolCanvasHandle } from '@/components/protocol/ProtocolCanvas'
import { RunConfirmDialog } from '@/components/protocol/RunConfirmDialog'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, experimentsApi, protocolsApi } from '@/api/client'
import { bestMetric, cellsStatusAccent, formatMetricLabel, groupReplicatesIntoCells, metricValueSuffix, scaledMetricValue } from '@/lib/experiment'
import { unboundFactorNames } from '@/lib/factorBindings'
import {
  applyExperimentRenameToProtocolCache,
  generatedProtocolName,
  protocolForExperimentQueryKey,
} from '@/lib/protocolGraph'
import { TERMINAL_RUN_STATUSES } from '@/lib/protocolRun'
import type { Cell, Experiment } from '@/types/experiments'
import type { Protocol, ProtocolRun } from '@/types/protocols'

const RUN_POLL_MS = 2000

// Click-to-rename, the pattern for anything created with a placeholder name:
// no gate before creating, edit the name in place once you're looking at what
// you're naming.
function EditableExperimentName({ experiment }: { experiment: Experiment }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(experiment.name)
  const queryClient = useQueryClient()

  const renameMutation = useMutation({
    mutationFn: (name: string) => experimentsApi.update(experiment.id, { name }),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
      applyExperimentRenameToProtocolCache(queryClient, experiment.id, updated.name)
    },
  })

  function commit() {
    setEditing(false)
    const trimmed = value.trim()
    if (trimmed && trimmed !== experiment.name) renameMutation.mutate(trimmed)
    else setValue(experiment.name)
  }

  if (editing) {
    return (
      <Input
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
          if (e.key === 'Escape') {
            setValue(experiment.name)
            setEditing(false)
          }
        }}
        className="h-8 w-72 text-lg font-semibold"
      />
    )
  }

  return (
    <button
      type="button"
      onClick={() => {
        setValue(experiment.name)
        setEditing(true)
      }}
      title="Click to rename"
      className="-ml-1.5 cursor-pointer rounded-md px-1.5 py-0.5 text-lg font-semibold tracking-tight hover:bg-muted"
    >
      {experiment.name}
    </button>
  )
}

// The two aggregates that belong on the top bar rather than inside a tab:
// "how far along is this experiment" and "how good is the best result so
// far". They're the only numbers you want without clicking anything, and
// they're what the (now-deleted) static detail page's stat cards were for.
// Design type deliberately doesn't get one -- the Design tab states it
// directly, so a readout here would just repeat it.
//
// Inline chips, not Cards: the top bar shares a single row with the name and
// the run controls, and a stat CARD in a 40px-tall row isn't a card, it's a
// bordered word. Tint carries the same meaning it does everywhere else --
// cellsStatusAccent for progress (amber unscored / cyan partial / emerald
// done), --chart-3 for a best result.
function TopBarStat({ icon: Icon, value, title, accent }: { icon: LucideIcon; value: string; title: string; accent: string }) {
  return (
    <span
      title={title}
      className="flex items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-xs text-muted-foreground"
      style={{ borderColor: `color-mix(in oklch, ${accent}, transparent 70%)` }}
    >
      <Icon className="size-3.5 shrink-0" style={{ color: accent }} />
      {value}
    </span>
  )
}

// Same design_type gate the Cells tab uses -- cells/metric_values are a
// factorial concept, so a future non-factorial experiment gets no chips
// rather than a nonsensical "0/0 scored".
function TopBarStats({ experiment, cells }: { experiment: Experiment; cells: Cell[] | undefined }) {
  if (experiment.design_type !== 'factorial' || !cells) return null
  const scored = cells.filter((c) => c.metric_values).length
  const cellCount = groupReplicatesIntoCells(cells).length
  const best = bestMetric(experiment, cells)
  return (
    <>
      <TopBarStat
        icon={Target}
        value={`${cellCount} ${cellCount === 1 ? 'cell' : 'cells'} · ${scored}/${cells.length} replicates scored`}
        title="Replicates with a recorded metric, out of every planned replicate in this design"
        accent={cellsStatusAccent(cells)}
      />
      {best && (
        <TopBarStat
          icon={Trophy}
          value={`${scaledMetricValue(best.key, best.value).toFixed(4)}${metricValueSuffix(best.key)}`}
          title={`Best mean ${formatMetricLabel(best.key)} across this experiment's cells`}
          accent="var(--chart-3)"
        />
      )}
    </>
  )
}

// Triggers POST /protocols/{id}/cell-runs (one ProtocolRun per not-yet-scored
// replicate, with its cell's factor_values substituted at execution time -- see
// services.protocol_execution.plan_cell_runs) and polls the existing
// GET /protocols/{id}/runs, filtered to just the runs this click created,
// until every one is terminal -- reusing protocolsApi.listRuns rather than
// adding a new aggregate polling endpoint. Disabled once there are 0 cells
// yet (nothing generated to run against).
function RunAllCellsButton({
  protocol,
  experimentId,
  cellCount,
  replicateCount,
  pendingReplicateCount,
  regenerationRequired,
  unboundFactors,
}: {
  protocol: Protocol
  experimentId: string
  cellCount: number
  replicateCount: number
  pendingReplicateCount: number
  regenerationRequired: boolean
  unboundFactors: string[]
}) {
  const protocolId = protocol.id
  const queryClient = useQueryClient()
  const [triggeredIds, setTriggeredIds] = useState<string[] | null>(null)
  const [batchRevision, setBatchRevision] = useState<number | null>(null)
  const [stopRequested, setStopRequested] = useState(false)
  const [confirmRunAll, setConfirmRunAll] = useState(false)

  const triggerMutation = useMutation({
    mutationFn: () => protocolsApi.runCells(protocolId),
    onSuccess: (batch) => {
      setTriggeredIds(batch.protocol_run_ids)
      setBatchRevision(batch.protocol_revision)
      setStopRequested(false)
    },
  })
  const publishAndRunMutation = useMutation({
    mutationFn: () => protocolsApi.publish(protocolId),
    onSuccess: (published) => {
      queryClient.setQueryData(protocolForExperimentQueryKey(experimentId), published)
      setConfirmRunAll(false)
      triggerMutation.mutate()
    },
  })

  const runsQuery = useQuery({
    queryKey: ['protocols', protocolId, 'runs'],
    queryFn: () => protocolsApi.listRuns(protocolId),
    enabled: !!triggeredIds,
    refetchInterval: (query) => {
      const runs = query.state.data
      if (!runs || !triggeredIds) return RUN_POLL_MS
      const triggered = runs.filter((r) => triggeredIds.includes(r.id))
      const allTerminal = triggered.length === triggeredIds.length && triggered.every((r) => TERMINAL_RUN_STATUSES.has(r.status))
      if (allTerminal) {
        queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'cells'] })
        return false
      }
      return RUN_POLL_MS
    },
  })

  const triggered: ProtocolRun[] = triggeredIds ? (runsQuery.data ?? []).filter((r) => triggeredIds.includes(r.id)) : []
  const doneCount = triggered.filter((r) => TERMINAL_RUN_STATUSES.has(r.status)).length
  const failedCount = triggered.filter((r) => r.status === 'failed').length
  const cancelledCount = triggered.filter((r) => r.status === 'cancelled').length
  const isRunning = triggerMutation.isPending || (!!triggeredIds && doneCount < triggeredIds.length)

  // "Stop all" only raises cancel_requested_at on each still-running run in
  // the batch (same fire-and-poll shape as the single-run Stop button in
  // ProtocolCanvas.tsx) -- run_protocol's own node loop is what actually
  // honors it, per run, between nodes. Already-terminal runs are left
  // alone; there's nothing to cancel there.
  const cancelAllMutation = useMutation({
    mutationFn: async () => {
      const stillRunning = triggered.filter((r) => !TERMINAL_RUN_STATUSES.has(r.status))
      await Promise.all(stillRunning.map((r) => protocolsApi.cancelRun(protocolId, r.id)))
    },
    onSuccess: () => setStopRequested(true),
  })

  let statusLabel: string | null = null
  if (triggerMutation.isPending) statusLabel = 'Starting…'
  else if (triggeredIds && isRunning) {
    statusLabel = `${stopRequested ? 'Stopping' : 'Running'} ${doneCount}/${triggeredIds.length} replicates${batchRevision ? ` · canvas v${batchRevision}` : ''}…`
  }
  else if (triggeredIds) {
    const parts = [`${triggeredIds.length - failedCount - cancelledCount} replicates done`]
    if (failedCount) parts.push(`${failedCount} failed`)
    if (cancelledCount) parts.push(`${cancelledCount} cancelled`)
    statusLabel = parts.join(', ')
  }

  const errorMessage = triggerMutation.isError
    ? triggerMutation.error instanceof ApiError && typeof triggerMutation.error.detail === 'string'
      ? triggerMutation.error.detail
      : 'Could not start the run.'
    : null
  const runBlocked = replicateCount === 0 || pendingReplicateCount === 0 || regenerationRequired || unboundFactors.length > 0 || !protocol.published_revision_id
  const blockedLabel = regenerationRequired
    ? 'Update design'
    : unboundFactors.length > 0
      ? 'Resolve factors'
      : !protocol.published_revision_id
        ? 'Publish protocol'
        : protocol.has_unpublished_changes
          ? `Run published v${protocol.published_revision}`
          : 'Run all cells'
  const blockedTitle = regenerationRequired
    ? 'Design changed — review and regenerate before running all cells.'
    : unboundFactors.length > 0
      ? `Rebind or remove: ${unboundFactors.join(', ')}.`
      : !protocol.published_revision_id
        ? 'Publish a valid canvas before running cells.'
        : replicateCount === 0
          ? 'Generate design first — there are no cells to run yet.'
          : pendingReplicateCount === 0
            ? 'Every planned replicate has already been scored.'
          : undefined

  return (
    <div className="flex items-center gap-2">
      {errorMessage && (
        <span className="max-w-64 truncate rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive" title={errorMessage}>
          {errorMessage}
        </span>
      )}
      {statusLabel && <span className="font-mono text-xs text-muted-foreground">{statusLabel}</span>}
      {isRunning && !triggerMutation.isPending && (
        <Button
          size="sm"
          variant="outline"
          disabled={stopRequested || cancelAllMutation.isPending}
          onClick={() => cancelAllMutation.mutate()}
        >
          <Square className="size-4" />
          {stopRequested ? 'Stopping…' : 'Stop all'}
        </Button>
      )}
      <span title={blockedTitle}>
        <Button
          size="sm"
          variant="outline"
          disabled={isRunning || runBlocked}
          onClick={() => setConfirmRunAll(true)}
        >
          {isRunning ? 'Running…' : blockedLabel}
        </Button>
      </span>
      {confirmRunAll && (
        <RunConfirmDialog
          scope={{ type: 'all-cells', cellCount, replicateCount, pendingReplicateCount }}
          nodes={protocol.graph.nodes as unknown as Node[]}
          edges={protocol.graph.edges as Edge[]}
          queryClient={queryClient}
          onCancel={() => setConfirmRunAll(false)}
          onConfirm={() => {
            setConfirmRunAll(false)
            triggerMutation.mutate()
          }}
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
        />
      )}
    </div>
  )
}

function ProtocolPublicationControl({ protocol, experimentId }: { protocol: Protocol; experimentId: string }) {
  const queryClient = useQueryClient()
  const publishMutation = useMutation({
    mutationFn: () => protocolsApi.publish(protocol.id),
    onSuccess: (published) => {
      queryClient.setQueryData(protocolForExperimentQueryKey(experimentId), published)
    },
  })
  const status = protocol.published_revision
    ? protocol.has_unpublished_changes
      ? `Draft changes · production uses v${protocol.published_revision}`
      : `Published v${protocol.published_revision}`
    : 'No published canvas'
  const error = publishMutation.error instanceof ApiError && typeof publishMutation.error.detail === 'string' ? publishMutation.error.detail : null
  return (
    <div className="flex items-center gap-2">
      <span className="font-mono text-xs text-muted-foreground" title="Production runs use the immutable published canvas revision.">
        {status}
      </span>
      {error && <span className="max-w-56 truncate text-xs text-destructive" title={error}>{error}</span>}
      <Button size="sm" variant="outline" disabled={!protocol.has_unpublished_changes || publishMutation.isPending} onClick={() => publishMutation.mutate()}>
        {publishMutation.isPending ? 'Publishing…' : 'Publish canvas'}
      </Button>
    </div>
  )
}

// One protocol per experiment is a V1 UX convention enforced here (find the
// first protocol tagged with this experiment, or lazily create one), not a
// schema constraint -- Protocol.experiment_id is nullable and a protocol is
// a standalone reusable object, so a future protocol-library page can
// change this without a migration.
export function ProtocolCanvasPage() {
  const { experimentId } = useParams<{ experimentId: string }>()
  // Lets ExperimentSidePanel/DesignTab (a sibling of ProtocolCanvas, not a
  // descendant) write a factor binding onto a live canvas node -- see
  // ProtocolCanvas.tsx's own comment on ProtocolCanvasHandle for why this
  // needs to be imperative rather than a plain prop.
  const canvasRef = useRef<ProtocolCanvasHandle>(null)

  const experimentQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId!),
    enabled: !!experimentId,
  })

  const protocolQuery = useQuery({
    queryKey: protocolForExperimentQueryKey(experimentId!),
    // This canvas is the only writer of a protocol's graph in the app, and
    // its autosave writes every save straight back into this cache entry
    // (see ProtocolCanvas.tsx) -- so refetching the instant you navigate
    // back only risks racing a still-in-flight save and re-seeding the
    // canvas from the pre-edit graph. A short staleTime lets the
    // navigate-away-and-back loop read the cache we know is current, while
    // still picking up outside changes (another tab, the API) after a beat.
    staleTime: 30_000,
    queryFn: async () => {
      const existing = await protocolsApi.list(experimentId!)
      if (existing.length > 0) return existing[0]
      const experiment = await experimentsApi.get(experimentId!)
      // See generatedProtocolName for why the name is suffixed with the
      // experiment's shortid. Renaming the experiment later re-syncs this
      // name server-side, so it stays a live label rather than a snapshot.
      return protocolsApi.create({
        name: generatedProtocolName(experiment.name, experimentId!),
        experiment_id: experimentId!,
      })
    },
    enabled: !!experimentId,
  })

  const cellsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'cells'],
    queryFn: () => experimentsApi.listCells(experimentId!),
    enabled: !!experimentId,
  })
  const impactQuery = useQuery({
    queryKey: ['experiments', experimentId, 'design-impact'],
    queryFn: () => experimentsApi.getDesignImpact(experimentId!),
    enabled: !!experimentId,
  })
  const unboundFactors = experimentQuery.data && protocolQuery.data
    ? unboundFactorNames(experimentQuery.data.design_spec, protocolQuery.data.graph)
    : []

  return (
    <div className="flex h-svh flex-col bg-muted/30">
      <AppHeader />

      <main className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden px-6 py-6">
        <div className="flex shrink-0 items-center gap-3">
          {/* Straight back to the list -- this canvas IS the experiment view
              now, so there's no intermediate detail page left to go up to. */}
          <Link to="/experiments" className="text-sm text-muted-foreground hover:underline">
            ← Experiments
          </Link>
          {experimentQuery.data && <EditableExperimentName experiment={experimentQuery.data} />}
          {experimentQuery.data && <TopBarStats experiment={experimentQuery.data} cells={cellsQuery.data} />}
          <div className="flex-1" />
          {protocolQuery.data && experimentId && (
            <>
              <ProtocolPublicationControl protocol={protocolQuery.data} experimentId={experimentId} />
              <RunAllCellsButton
                protocol={protocolQuery.data}
                experimentId={experimentId}
                cellCount={groupReplicatesIntoCells(cellsQuery.data ?? []).length}
                replicateCount={cellsQuery.data?.length ?? 0}
                pendingReplicateCount={cellsQuery.data?.filter((replicate) => !replicate.metric_values).length ?? 0}
                regenerationRequired={impactQuery.data?.regeneration_required ?? false}
                unboundFactors={unboundFactors}
              />
            </>
          )}
        </div>

        <div className="flex min-h-0 flex-1 gap-3 overflow-hidden">
          <ExperimentSidePanel
            experiment={experimentQuery.data}
            protocolId={protocolQuery.data?.id}
            canvasRef={canvasRef}
            isLoading={experimentQuery.isLoading}
          />

          {protocolQuery.isLoading ? (
            <Skeleton className="flex-1" />
          ) : protocolQuery.isError || !protocolQuery.data ? (
            <p className="text-sm text-muted-foreground">Could not load this experiment's protocol.</p>
          ) : (
            <Card className="flex-1 overflow-hidden p-0">
              <ReactFlowProvider>
                <ProtocolCanvas
                  key={protocolQuery.data.id}
                  ref={canvasRef}
                  protocolId={protocolQuery.data.id}
                  experimentId={protocolQuery.data.experiment_id}
                  initialGraph={protocolQuery.data.graph}
                  hasUnpublishedChanges={protocolQuery.data.has_unpublished_changes}
                  publishedRevision={protocolQuery.data.published_revision}
                />
              </ReactFlowProvider>
            </Card>
          )}
        </div>
      </main>
    </div>
  )
}
