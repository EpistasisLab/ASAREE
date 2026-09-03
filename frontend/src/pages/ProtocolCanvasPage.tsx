import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ReactFlowProvider } from '@xyflow/react'
import { AlertTriangle, Lock, Target, Trophy, type LucideIcon } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { AppHeader } from '@/components/AppHeader'
import { ExperimentSidePanel } from '@/components/protocol/ExperimentSidePanel'
import { ProtocolCanvas, type ProtocolCanvasHandle } from '@/components/protocol/ProtocolCanvas'
import { ResultsInspectorPanel, type ResultsSelection } from '@/components/protocol/ResultsTab'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, experimentsApi, protocolsApi } from '@/api/client'
import { bestMetric, formatMetricLabel, groupReplicatesIntoCells, metricValueSuffix, replicatesStatusAccent, scaledMetricValue } from '@/lib/experiment'
import { unboundFactorNames } from '@/lib/factorBindings'
import {
  applyExperimentRenameToProtocolCache,
  generatedProtocolName,
  protocolForExperimentQueryKey,
} from '@/lib/protocolGraph'
import type { Experiment, Replicate } from '@/types/experiments'
import type { Protocol } from '@/types/protocols'

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
// replicatesStatusAccent for progress (amber unscored / cyan partial / emerald
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
function TopBarStats({ experiment, cells, obsoleteRunCount = 0 }: { experiment: Experiment; cells: Replicate[] | undefined; obsoleteRunCount?: number }) {
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
        accent={replicatesStatusAccent(cells)}
      />
      {obsoleteRunCount > 0 && (
        <span className="flex items-center gap-1.5 rounded-md border border-[color:var(--chart-4)]/50 bg-[color:var(--chart-4)]/10 px-2 py-1 text-xs font-medium text-[color:var(--chart-4)]" title="Runs against older canvas versions are excluded from current results.">
          <AlertTriangle className="size-3.5" /> {obsoleteRunCount} obsolete run{obsoleteRunCount === 1 ? '' : 's'}
        </span>
      )}
      {best && (
        <TopBarStat
          icon={Trophy}
          value={`${formatMetricLabel(best.key)} · ${best.valueType === 'boolean' ? `${Math.round(best.value * 100)}%` : `${scaledMetricValue(best.key, best.value).toFixed(4)}${metricValueSuffix(best.key)}`}`}
          title={`Best mean ${formatMetricLabel(best.key)} across this experiment's cells, using the declared primary metric`}
          accent="var(--chart-3)"
        />
      )}
    </>
  )
}

function ProtocolPublicationControl({ protocol, experimentId }: { protocol: Protocol; experimentId: string }) {
  const queryClient = useQueryClient()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const trialsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'runs'],
    queryFn: () => experimentsApi.listTrials(experimentId),
    enabled: protocol.has_unpublished_changes,
  })
  // Obsolescence is based on immutable protocol-run provenance. Results that
  // were scored directly without a ProtocolRun have no canvas version to
  // become stale against, so do not overstate the impact in this warning.
  const affectedReplicateCount = (trialsQuery.data ?? []).filter((trial) => !!trial.run_id && !trial.obsolete).length
  const publishMutation = useMutation({
    mutationFn: () => protocolsApi.publish(protocol.id),
    onSuccess: (published) => {
      queryClient.setQueryData(protocolForExperimentQueryKey(experimentId), published)
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'runs'] })
      setConfirmOpen(false)
    },
  })
  const status = protocol.published_revision
    ? protocol.has_unpublished_changes
      ? `Draft changes · production uses v${protocol.published_revision}`
      : `Published v${protocol.published_revision}`
    : 'No published canvas'
  const error = publishMutation.error instanceof ApiError && typeof publishMutation.error.detail === 'string' ? publishMutation.error.detail : null
  function requestPublish() {
    if (affectedReplicateCount > 0) setConfirmOpen(true)
    else publishMutation.mutate()
  }
  return (
    <>
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground" title="Production runs use the immutable published canvas revision.">
          {status}
        </span>
        {error && <span className="max-w-56 truncate text-xs text-destructive" title={error}>{error}</span>}
        <Button
          size="sm"
          variant="outline"
          disabled={!protocol.has_unpublished_changes || publishMutation.isPending || trialsQuery.isLoading}
          onClick={requestPublish}
        >
          {publishMutation.isPending ? 'Publishing…' : 'Publish canvas'}
        </Button>
      </div>
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Publish a new canvas version?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Publishing this draft changes the canvas version used by future runs. {affectedReplicateCount}{' '}
            previously run replicate{affectedReplicateCount === 1 ? '' : 's'} will be marked obsolete because{affectedReplicateCount === 1 ? ' it was' : ' they were'} run against the current version.
          </p>
          <p className="text-xs text-muted-foreground">The results remain available for comparison; they are not deleted.</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>Cancel</Button>
            <Button onClick={() => publishMutation.mutate()} disabled={publishMutation.isPending}>
              {publishMutation.isPending ? 'Publishing…' : 'Publish and mark obsolete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
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
  const [resultSelection, setResultSelection] = useState<ResultsSelection | null>(null)

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

  const replicatesQuery = useQuery({
    queryKey: ['experiments', experimentId, 'replicates'],
    queryFn: () => experimentsApi.listReplicates(experimentId!),
    enabled: !!experimentId,
  })
  const runResultsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'run-results'],
    queryFn: () => experimentsApi.getRunResults(experimentId!),
    enabled: !!experimentId,
    refetchInterval: 5000,
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
          {experimentQuery.data?.locked_at && <span className="inline-flex items-center gap-1 rounded-md border border-primary/40 bg-primary/10 px-2 py-1 text-xs font-medium text-primary"><Lock className="size-3" /> Locked</span>}
          {experimentQuery.data && <TopBarStats experiment={experimentQuery.data} cells={replicatesQuery.data} obsoleteRunCount={runResultsQuery.data?.overview.obsolete_replicates ?? 0} />}
          <div className="flex-1" />
          {protocolQuery.data && experimentId && (
            <>
              <ProtocolPublicationControl protocol={protocolQuery.data} experimentId={experimentId} />
            </>
          )}
        </div>

        <div className="flex min-h-0 flex-1 gap-3 overflow-hidden">
          <ExperimentSidePanel
            experiment={experimentQuery.data}
            protocolId={protocolQuery.data?.id}
            protocol={protocolQuery.data}
            canvasRef={canvasRef}
            isLoading={experimentQuery.isLoading}
            needsInitialGeneration={impactQuery.data?.has_generated_design === false && impactQuery.data.proposed_cell_count > 0}
            regenerationRequired={impactQuery.data?.regeneration_required ?? false}
            unboundFactors={unboundFactors}
            onResultSelection={setResultSelection}
          />

          {protocolQuery.isLoading ? (
            <Skeleton className="flex-1" />
          ) : protocolQuery.isError || !protocolQuery.data ? (
            <p className="text-sm text-muted-foreground">Could not load this experiment's protocol.</p>
          ) : (
            <Card className="relative flex-1 overflow-hidden p-0">
              <ReactFlowProvider>
                <ProtocolCanvas
                  key={protocolQuery.data.id}
                  ref={canvasRef}
                  protocolId={protocolQuery.data.id}
                  experimentId={protocolQuery.data.experiment_id}
                  initialGraph={protocolQuery.data.graph}
                  hasUnpublishedChanges={protocolQuery.data.has_unpublished_changes}
                  publishedRevision={protocolQuery.data.published_revision}
                  experimentLocked={!!experimentQuery.data?.locked_at}
                />
              </ReactFlowProvider>
              {experimentId && <ResultsInspectorPanel experimentId={experimentId} selection={resultSelection} onClose={() => setResultSelection(null)} />}
            </Card>
          )}
        </div>
      </main>
    </div>
  )
}
