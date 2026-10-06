import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ReactFlowProvider } from '@xyflow/react'
import { AlertTriangle, Lock, Target, Trophy, type LucideIcon } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { AppHeader } from '@/components/AppHeader'
import { ExperimentSidePanel } from '@/components/protocol/ExperimentSidePanel'
import { ExperimentVersionCanvas } from '@/components/protocol/ExperimentVersionView'
import { ExperimentVersionHistory } from '@/components/protocol/ExperimentVersionHistory'
import { ProtocolCanvas, type ProtocolCanvasHandle } from '@/components/protocol/ProtocolCanvas'
import { ResultsInspectorPanel, type ResultsSelection } from '@/components/protocol/ResultsTab'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, experimentsApi, protocolsApi } from '@/api/client'
import { bestMetric, formatMetricLabel, groupReplicatesIntoCells, metricValueSuffix, replicatesStatusAccent, scaledMetricValue } from '@/lib/experiment'
import { unboundFactorNames } from '@/lib/factorBindings'
import {
  applyExperimentRenameToProtocolCache,
  generatedProtocolName,
  protocolForExperimentQueryKey,
  protocolGraphQueryKey,
  toPersistedGraph,
} from '@/lib/protocolGraph'
import type { Experiment, Replicate } from '@/types/experiments'
import type { Protocol } from '@/types/protocols'
import type { Node, Edge } from '@xyflow/react'

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
function TopBarStats({ experiment, cells, obsoleteRunCount = 0, rowSummary }: { experiment: Experiment; cells: Replicate[] | undefined; obsoleteRunCount?: number; rowSummary?: import('@/types/experiments').RowSummary | null }) {
  if (rowSummary) return <TopBarStat icon={Target} value={`${rowSummary.cell_count} cells / ${rowSummary.parent_replicate_count} replicates / ${rowSummary.row_count ?? '?'} rows / ${rowSummary.expected ?? '?'} executions`} title={`${rowSummary.completed} completed · ${rowSummary.missing_reported} missing reported`} accent="var(--primary)" />
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

function ProtocolPublicationControl({ protocol, experimentId, draftBusy }: { protocol: Protocol; experimentId: string; draftBusy: boolean }) {
  const queryClient = useQueryClient()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [versionName, setVersionName] = useState('')
  const [versionNote, setVersionNote] = useState('')
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
    mutationFn: async () => {
      const live = queryClient.getQueryData<{ nodes: Node[]; edges: Edge[] }>(protocolGraphQueryKey(protocol.id))
      if (live) await protocolsApi.update(protocol.id, { graph: toPersistedGraph(live.nodes, live.edges) })
      return protocolsApi.publish(protocol.id, { name: versionName.trim() || null, note: versionNote.trim() || null })
    },
    onSuccess: (published) => {
      queryClient.setQueryData(protocolForExperimentQueryKey(experimentId), published)
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'runs'] })
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'run-results'] })
      queryClient.invalidateQueries({ queryKey: ['protocols', protocol.id, 'revisions'] })
      setConfirmOpen(false)
      setVersionName('')
      setVersionNote('')
    },
  })
  const status = protocol.published_revision
    ? protocol.has_unpublished_changes
      ? `Draft changes · production uses v${protocol.published_revision}`
      : `Published v${protocol.published_revision}`
    : 'Experiment draft · not published'
  const error = publishMutation.error instanceof ApiError && typeof publishMutation.error.detail === 'string' ? publishMutation.error.detail : null
  function requestPublish() {
    publishMutation.reset()
    setConfirmOpen(true)
  }
  return (
    <>
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground" title="Production runs use the published experiment’s canvas and design settings together.">
          {status}
        </span>
        {error && <span className="max-w-56 truncate text-xs text-destructive" title={error}>{error}</span>}
        {(protocol.has_unpublished_changes || draftBusy) && (
          <Button
            size="sm"
            variant="outline"
            disabled={draftBusy || publishMutation.isPending || trialsQuery.isLoading}
            title={draftBusy ? 'Save or generate the pending Design changes before publishing.' : 'Publish the saved canvas and Design together. An unchanged experiment keeps its existing version.'}
            onClick={requestPublish}
          >
            {publishMutation.isPending ? 'Publishing…' : 'Publish experiment'}
          </Button>
        )}
      </div>
      <Dialog open={confirmOpen} onOpenChange={open => { if (!publishMutation.isPending) setConfirmOpen(open) }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Publish a new experiment version?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Publish the canvas and design settings together as a new experiment version. Future runs will use it.
          </p>
          <p className="text-xs text-muted-foreground">Existing runs and results remain available under their earlier experiment version.</p>
          {affectedReplicateCount > 0 && <p className="text-xs text-muted-foreground">{affectedReplicateCount} existing replicate runs will remain under the earlier version.</p>}
          <label className="space-y-1 text-sm">Version name (optional)<Input maxLength={120} placeholder="Higher critic threshold" value={versionName} onChange={event => setVersionName(event.target.value)} /></label>
          <label className="space-y-1 text-sm">Version note (optional)<Textarea maxLength={4000} placeholder="What changed, and why?" value={versionNote} onChange={event => setVersionNote(event.target.value)} /></label>
          {!protocol.has_unpublished_changes && <p className="text-xs text-muted-foreground">An unchanged experiment keeps its version and existing name and note. Edit those in version history.</p>}
          {publishMutation.isError && <p role="alert" className="text-xs text-destructive">{error || 'Could not publish the experiment. Try again.'}</p>}
          <DialogFooter>
            <Button variant="outline" disabled={publishMutation.isPending} onClick={() => setConfirmOpen(false)}>Cancel</Button>
            <Button onClick={() => publishMutation.mutate()} disabled={publishMutation.isPending}>
              {publishMutation.isPending ? 'Publishing…' : 'Publish experiment'}
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
  const [versionId, setVersionId] = useState('')
  const [versionBrowserOpen, setVersionBrowserOpen] = useState(false)
  const [versionSearch, setVersionSearch] = useState('')
  const [draftBusy, setDraftBusy] = useState(false)
  useEffect(() => { setVersionId(''); setResultSelection(null); setVersionBrowserOpen(false); setVersionSearch('') }, [experimentId])

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
      // POST /experiments now creates this shell transactionally. Keep this
      // fallback for experiments created before that behavior shipped.
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

  const runResultsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'run-results', protocolQuery.data?.id, protocolQuery.data?.published_revision_id],
    queryFn: () => experimentsApi.getRunResults(experimentId!, { protocol_id: protocolQuery.data?.id }),
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
  const versionsQuery = useQuery({ queryKey: ['protocols', protocolQuery.data?.id, 'revisions'], queryFn: () => protocolsApi.listRevisions(protocolQuery.data!.id), enabled: !!protocolQuery.data?.id, refetchInterval: versionBrowserOpen ? 5000 : false })
  const experimentVersions = versionsQuery.data?.filter(item => item.experiment_snapshot != null).sort((a, b) => b.revision - a.revision)
  const hasChanges = !!protocolQuery.data?.has_unpublished_changes || draftBusy
  const publishedVersion = protocolQuery.data?.published_revision
  const version = experimentVersions?.find(item => item.id === versionId)
  const displayedExperiment = experimentQuery.data && version?.experiment_snapshot
    ? { ...experimentQuery.data, ...version.experiment_snapshot } : experimentQuery.data
  const resultsVersion = version ?? versionsQuery.data?.find(item => item.id === protocolQuery.data?.published_revision_id)
  const resultsExperiment = experimentQuery.data && resultsVersion?.experiment_snapshot
    ? { ...experimentQuery.data, ...resultsVersion.experiment_snapshot } : displayedExperiment
  const resultReplicates: Replicate[] | undefined = runResultsQuery.data?.replicates.map(row => ({
    id: row.replicate_label, cell_id: row.cell_label, cell_label: row.cell_label,
    replicate_label: row.replicate_label, replicate_number: row.replicate_number,
    design_revision_id: resultsVersion?.design_revision_id ?? '',
    factor_values: row.factor_values, metric_values: Object.keys(row.metric_values).length ? row.metric_values : null,
    run_id: row.run_id, workspace_id: null, artifacts: null,
    created_at: row.updated_at, updated_at: row.updated_at,
  }))

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
          {experimentQuery.data && (versionId ? <span className="text-lg font-semibold">{experimentQuery.data.name}</span> : <EditableExperimentName experiment={experimentQuery.data} />)}
          {experimentQuery.data?.locked_at && <span className="inline-flex items-center gap-1 rounded-md border border-primary/40 bg-primary/10 px-2 py-1 text-xs font-medium text-primary"><Lock className="size-3" /> Locked</span>}
          {!versionId && resultsExperiment && <TopBarStats experiment={resultsExperiment} cells={resultReplicates} rowSummary={runResultsQuery.data?.row_summary} obsoleteRunCount={runResultsQuery.data?.overview.obsolete_replicates ?? 0} />}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-3 rounded-md border bg-card px-3 py-2 text-xs" aria-label="Experiment version controls">
          <Button variant="ghost" size="sm" disabled={!experimentVersions?.length} onClick={() => { setVersionSearch(''); setVersionBrowserOpen(true) }}>Version history</Button>
          <p className="text-muted-foreground">{versionId ? 'Canvas, Design, Runs, and Results show this saved version · read-only.'
            : !publishedVersion ? 'Publish the experiment to create its first version.'
              : hasChanges ? `Changes are not published. Runs and Results use version ${publishedVersion}.`
                : 'Canvas, Design, Runs, and Results use this published version.'}</p>
          {versionsQuery.isError && <p role="alert" className="text-destructive">Could not load experiment versions.</p>}
          {protocolQuery.data && experimentId && !versionId && (
            <div className="ml-auto">
              <ProtocolPublicationControl protocol={protocolQuery.data} experimentId={experimentId} draftBusy={draftBusy} />
            </div>
          )}
          {versionId && <Button className="ml-auto" variant="outline" size="sm" onClick={() => { setVersionId(''); setResultSelection(null) }}>Return to experiment</Button>}
        </div>
        <Dialog open={versionBrowserOpen} onOpenChange={setVersionBrowserOpen}>
          <DialogContent className="sm:max-w-xl">
            <DialogHeader><DialogTitle>Browse experiment versions</DialogTitle></DialogHeader>
            {protocolQuery.data && <ExperimentVersionHistory versions={experimentVersions ?? []} protocolId={protocolQuery.data.id} publishedId={protocolQuery.data.published_revision_id} selectedId={versionId} search={versionSearch} onSearch={setVersionSearch} onSelect={id => { setVersionId(id); setResultSelection(null); setVersionBrowserOpen(false) }} />}
          </DialogContent>
        </Dialog>

        <div className="flex min-h-0 flex-1 gap-3 overflow-hidden">
          <ExperimentSidePanel
            experiment={displayedExperiment}
            draftExperiment={experimentQuery.data}
            resultsExperiment={resultsExperiment}
            version={version}
            viewingHistory={!!versionId}
            onDraftBusyChange={setDraftBusy}
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
              <div className={versionId ? 'hidden' : 'h-full'}>
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
              </div>
              {versionId && <ReactFlowProvider>{version ? <ExperimentVersionCanvas key={version.id} version={version} /> : <p role="status" className="p-3 text-sm text-muted-foreground">{versionsQuery.isError ? 'Could not load this experiment version. Return to draft to continue.' : 'Loading experiment version…'}</p>}</ReactFlowProvider>}
              {experimentId && resultsExperiment && <ResultsInspectorPanel experimentId={experimentId} experiment={resultsExperiment} selection={resultSelection} onClose={() => setResultSelection(null)} />}
            </Card>
          )}
        </div>
      </main>
    </div>
  )
}
