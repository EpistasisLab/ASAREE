import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ReactFlowProvider } from '@xyflow/react'
import { Square, Target, Trophy, type LucideIcon } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { AppHeader } from '@/components/AppHeader'
import { ExperimentSidePanel } from '@/components/protocol/ExperimentSidePanel'
import { ProtocolCanvas, type ProtocolCanvasHandle } from '@/components/protocol/ProtocolCanvas'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, experimentsApi, protocolsApi } from '@/api/client'
import { bestMetric, cellsStatusAccent, formatMetricLabel, metricValueSuffix, scaledMetricValue } from '@/lib/experiment'
import { TERMINAL_RUN_STATUSES } from '@/lib/protocolRun'
import type { Cell, Experiment } from '@/types/experiments'
import type { ProtocolRun } from '@/types/protocols'

const RUN_POLL_MS = 2000

// Click-to-rename, n8n's own pattern for a workflow created with a
// placeholder name: no gate before creating, edit the name in place once
// you're looking at what you're naming.
function EditableExperimentName({ experiment }: { experiment: Experiment }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(experiment.name)
  const queryClient = useQueryClient()

  const renameMutation = useMutation({
    mutationFn: (name: string) => experimentsApi.update(experiment.id, { name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
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
  const best = bestMetric(experiment, cells)
  return (
    <>
      <TopBarStat
        icon={Target}
        value={`${scored}/${cells.length} scored`}
        title="Cells with a recorded metric, out of every cell in this design"
        accent={cellsStatusAccent(cells)}
      />
      {best && (
        <TopBarStat
          icon={Trophy}
          value={`${scaledMetricValue(best.key, best.value).toFixed(4)}${metricValueSuffix(best.key)}`}
          title={`Best ${formatMetricLabel(best.key)} across this experiment's scored cells`}
          accent="var(--chart-3)"
        />
      )}
    </>
  )
}

// Triggers POST /protocols/{id}/cell-runs (one ProtocolRun per not-yet-scored
// cell, factor_values substituted at execution time -- see
// services.protocol_execution.plan_cell_runs) and polls the existing
// GET /protocols/{id}/runs, filtered to just the runs this click created,
// until every one is terminal -- reusing protocolsApi.listRuns rather than
// adding a new aggregate polling endpoint. Disabled once there are 0 cells
// yet (nothing generated to run against).
function RunAllCellsButton({ protocolId, experimentId, cellCount }: { protocolId: string; experimentId: string; cellCount: number }) {
  const queryClient = useQueryClient()
  const [triggeredIds, setTriggeredIds] = useState<string[] | null>(null)
  const [stopRequested, setStopRequested] = useState(false)

  const triggerMutation = useMutation({
    mutationFn: () => protocolsApi.runCells(protocolId),
    onSuccess: (batch) => {
      setTriggeredIds(batch.protocol_run_ids)
      setStopRequested(false)
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
  else if (triggeredIds && isRunning) statusLabel = `${stopRequested ? 'Stopping' : 'Running'} ${doneCount}/${triggeredIds.length} cells…`
  else if (triggeredIds) {
    const parts = [`${triggeredIds.length - failedCount - cancelledCount} done`]
    if (failedCount) parts.push(`${failedCount} failed`)
    if (cancelledCount) parts.push(`${cancelledCount} cancelled`)
    statusLabel = parts.join(', ')
  }

  const errorMessage = triggerMutation.isError
    ? triggerMutation.error instanceof ApiError && typeof triggerMutation.error.detail === 'string'
      ? triggerMutation.error.detail
      : 'Could not start the run.'
    : null

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
      <span title={cellCount === 0 ? 'Generate design first -- there are no cells to run yet.' : undefined}>
        <Button size="sm" variant="outline" disabled={isRunning || cellCount === 0} onClick={() => triggerMutation.mutate()}>
          {isRunning ? 'Running…' : 'Run all cells'}
        </Button>
      </span>
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
    queryKey: ['protocols', 'for-experiment', experimentId],
    queryFn: async () => {
      const existing = await protocolsApi.list(experimentId!)
      if (existing.length > 0) return existing[0]
      const experiment = await experimentsApi.get(experimentId!)
      // Protocol names are unique per owner (uq_protocols_owner_name) --
      // two experiments sharing a name (trivially true for the "Untitled
      // Experiment" default every new one starts with) would otherwise
      // collide here and 409 forever, since a plain-name retry hits the
      // exact same conflict every time. The experiment's own id is unique
      // by construction, so suffixing it (matching the "[shortid]"
      // disambiguation convention already used elsewhere, e.g. a real
      // protocol named "...Benchmark [079976db]") makes this create call
      // collision-proof.
      return protocolsApi.create({
        name: `Protocol: ${experiment.name} [${experimentId!.slice(0, 8)}]`,
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
            <RunAllCellsButton
              protocolId={protocolQuery.data.id}
              experimentId={experimentId}
              cellCount={cellsQuery.data?.length ?? 0}
            />
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
                />
              </ReactFlowProvider>
            </Card>
          )}
        </div>
      </main>
    </div>
  )
}
