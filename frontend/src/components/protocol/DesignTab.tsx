import { useEffect, useState, type RefObject } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import { Pencil, Plus, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { experimentsApi, protocolsApi } from '@/api/client'
import { unboundFactorNames } from '@/lib/factorBindings'
import { protocolGraphQueryKey } from '@/lib/protocolGraph'
import { revealsHiddenMcpServers, toolFactorServerId, unboundBindableFields, type UnboundField } from './bindableFields'
import { LEVEL_TYPE_LABELS, levelTypeOf } from './factorLevels'
import { FactorEditorDialog } from './FactorEditorDialog'
import { InfoTooltip } from './InfoTooltip'
import type { ProtocolCanvasHandle } from './ProtocolCanvas'
import type { ProtocolGraph } from '@/types/protocols'
import {
  COORDINATION_STRATEGY_CATALOG,
  type CoordinationStrategySlug,
  type DesignFactor,
  type DesignMetric,
  type Experiment,
} from '@/types/experiments'

// Creating, renaming, and deleting a factor (this function and
// FactorsEditor's own edit/delete mutations below) are the one set of
// actions here that ISN'T staged into the local draft + explicit "Save
// design" flow the rest of this tab uses -- each is a two-sided write
// (design_spec.factors AND every canvas node's own factor_bindings that
// reference this factor by name) that has to land atomically, so all three
// mirror FactorBindableField's own existing immediate-save popover instead.
// Staging a rename/delete until "Save design" would leave a bound node
// pointing at a factor name that no longer resolves in the meantime -- its
// FactorBindableField badge keeps showing the stale name, and
// unboundBindableFields still treats the field as bound, so it can't be
// re-bound to anything else either. The canvas write goes through canvasRef
// (see ProtocolCanvas.tsx's own comment on ProtocolCanvasHandle) since
// DesignTab is a sibling of ProtocolCanvas, not a descendant, and has no
// other way to reach its live node state; the list of what's bindable comes
// from the same shared query cache (protocolGraphQueryKey) ProtocolCanvas
// mirrors its own nodes into on every change, so this never lags behind the
// canvas waiting on autosave.
//
// The field picker itself lives inside FactorEditorDialog (not a separate
// popover before it) -- a canvas can realistically have a large number of
// bindable fields to search through, and the dialog already has the room a
// cramped popover wouldn't.
// The canvas's live nodes/edges, read from the same shared query cache
// ProtocolCanvas mirrors its own state into on every change. Used by both
// halves of the factors editor: AddFactorButton needs the list of bindable
// fields, FactorsEditor needs to resolve a tool_names factor's pinned MCP
// server (toolFactorServerId) before opening its level editor.
function useProtocolGraph(protocolId: string | undefined) {
  return useQuery({
    queryKey: protocolGraphQueryKey(protocolId ?? 'none'),
    queryFn: async () => {
      if (!protocolId) return { nodes: [] as Node[], edges: [] as Edge[] }
      const protocol = await protocolsApi.get(protocolId)
      return { nodes: protocol.graph.nodes as Node[], edges: protocol.graph.edges as Edge[] }
    },
    enabled: !!protocolId,
    // Only ever a fallback for before ProtocolCanvas has mounted and mirrored
    // its own live state into this same key -- once it has, this key is only
    // ever updated by that mirror (a pure in-memory write), never a real
    // background refetch racing it with a stale server snapshot.
    staleTime: Infinity,
  })
}

function AddFactorButton({
  experiment,
  protocolId,
  canvasRef,
  existingNames,
}: {
  experiment: Experiment
  protocolId: string | undefined
  canvasRef: RefObject<ProtocolCanvasHandle | null>
  existingNames: string[]
}) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const queryClient = useQueryClient()

  const graphQuery = useProtocolGraph(protocolId)

  const createMutation = useMutation({
    mutationFn: async ({ factor, field }: { factor: DesignFactor; field: UnboundField }) => {
      // Fetched fresh (not the local staged draft) -- matches
      // FactorBindableField's own existing save mutation exactly, including
      // its same pre-existing risk of racing a concurrent unsaved edit
      // elsewhere on this tab (see FactorEditorDialog.tsx's design notes).
      const fresh = await experimentsApi.get(experiment.id)
      const nextFactors = [...(fresh.design_spec?.factors ?? []), factor]
      await experimentsApi.update(experiment.id, { design_spec: { ...fresh.design_spec, factors: nextFactors } })
      return { factor, field }
    },
    onSuccess: ({ factor, field }) => {
      canvasRef.current?.bindFactor(field.nodeId, field.fieldPath, factor.name)
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id] })
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id, 'design-impact'] })
      setDialogOpen(false)
    },
  })

  const fields = unboundBindableFields(graphQuery.data?.nodes ?? [], graphQuery.data?.edges ?? [])

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setDialogOpen(true)}>
        <Plus className="size-3.5" /> Add factor
      </Button>

      {dialogOpen && (
        <FactorEditorDialog
          open
          onOpenChange={setDialogOpen}
          factor={{ name: '', levels: [], level_type: 'string' }}
          revealHiddenServers={revealsHiddenMcpServers(graphQuery.data?.nodes ?? [])}
          pickableFields={fields}
          existingNames={existingNames}
          onSave={(factor, field) => {
            if (field) createMutation.mutate({ factor, field })
          }}
        />
      )}
    </>
  )
}

// The Design tab's own factors editor -- the canonical place to view/edit
// every declared factor at once, regardless of how it was originally
// created. Distinct from FactorBindableField's own per-inspector-field "+"
// popover (still used to quick-create a factor scoped to binding one
// specific node field) -- both read/write the same design_spec.factors
// array, they just serve different entry points. Each row is a compact
// summary (name, level type, level count) with an Edit button opening
// FactorEditorDialog for real room -- levels used to be crammed into one
// comma-separated text Input, which broke for any value containing a comma
// and was unusable for a long-form level (e.g. a factor whose levels are
// several different full system prompts).
//
// Rename and Delete are immediate, atomic two-sided writes, same as
// AddFactorButton's own createMutation above -- NOT folded into this tab's
// staged-draft-then-Save flow the way hypothesis/replicates/metrics are.
// A factor can be bound from one or more canvas nodes' own factor_bindings
// (by name, a plain string), so removing or renaming one has to also sweep
// those bindings via canvasRef in the same action; staging the removal
// until a later "Save design" click would leave every bound node silently
// pointing at a factor name that no longer resolves in the meantime (its
// FactorBindableField badge still reads "Factor: {name}," and
// unboundBindableFields still treats the field as bound, so it can never be
// re-bound to a different factor either) -- this is the exact desync this
// component used to have.
function FactorsEditor({
  experiment,
  protocolId,
  canvasRef,
  factors,
}: {
  experiment: Experiment
  protocolId: string | undefined
  canvasRef: RefObject<ProtocolCanvasHandle | null>
  factors: DesignFactor[]
}) {
  const [editingFactor, setEditingFactor] = useState<{ index: number; draft: DesignFactor } | null>(null)
  const queryClient = useQueryClient()
  const graphQuery = useProtocolGraph(protocolId)

  const deleteMutation = useMutation({
    mutationFn: async (name: string) => {
      const fresh = await experimentsApi.get(experiment.id)
      const nextFactors = (fresh.design_spec?.factors ?? []).filter((f) => f.name !== name)
      await experimentsApi.update(experiment.id, { design_spec: { ...fresh.design_spec, factors: nextFactors } })
      return name
    },
    onSuccess: (name) => {
      canvasRef.current?.removeFactorBindings(name)
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id] })
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id, 'design-impact'] })
    },
  })

  const editMutation = useMutation({
    mutationFn: async ({ oldName, next }: { oldName: string; next: DesignFactor }) => {
      const fresh = await experimentsApi.get(experiment.id)
      const nextFactors = (fresh.design_spec?.factors ?? []).map((f) => (f.name === oldName ? next : f))
      await experimentsApi.update(experiment.id, { design_spec: { ...fresh.design_spec, factors: nextFactors } })
      return { oldName, next }
    },
    onSuccess: ({ oldName, next }) => {
      if (next.name !== oldName) canvasRef.current?.renameFactorBindings(oldName, next.name)
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id] })
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id, 'design-impact'] })
      setEditingFactor(null)
    },
  })

  return (
    <div className="space-y-2">
      {factors.map((factor, i) => (
        <div key={i} className="flex items-center gap-1.5 rounded-md border px-2.5 py-1.5">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium" title={factor.name}>
              {factor.name || '(unnamed factor)'}
            </p>
            <p className="text-xs text-muted-foreground">
              {factor.levels.length} level{factor.levels.length === 1 ? '' : 's'}
            </p>
          </div>
          <Badge variant="outline">{LEVEL_TYPE_LABELS[levelTypeOf(factor)]}</Badge>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Edit factor"
            onClick={() => setEditingFactor({ index: i, draft: factor })}
          >
            <Pencil className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Remove factor"
            disabled={deleteMutation.isPending}
            onClick={() => deleteMutation.mutate(factor.name)}
          >
            <X className="size-3.5" />
          </Button>
        </div>
      ))}
      <AddFactorButton experiment={experiment} protocolId={protocolId} canvasRef={canvasRef} existingNames={factors.map((f) => f.name)} />

      {editingFactor && (
        <FactorEditorDialog
          open
          onOpenChange={(open) => {
            if (!open) setEditingFactor(null)
          }}
          factor={editingFactor.draft}
          // Only read for a tool_names factor -- a level there is a bare
          // allow-list, so the editor needs the bound node to know whose
          // tools to offer (see bindableFields.ts's toolFactorServerId).
          toolServerId={toolFactorServerId(graphQuery.data?.nodes ?? [], editingFactor.draft.name)}
          revealHiddenServers={revealsHiddenMcpServers(graphQuery.data?.nodes ?? [])}
          onSave={(next) => editMutation.mutate({ oldName: editingFactor.draft.name, next })}
        />
      )}
    </div>
  )
}

function MetricsEditor({ metrics, onChange }: { metrics: DesignMetric[]; onChange: (metrics: DesignMetric[]) => void }) {
  return (
    <div className="space-y-2">
      {metrics.map((metric, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <Input
            value={metric.name}
            placeholder="Metric name"
            className="flex-1"
            onChange={(e) => onChange(metrics.map((m, j) => (j === i ? { ...m, name: e.target.value } : m)))}
          />
          <label className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground" title="Primary metric">
            <input
              type="radio"
              name="primary-metric"
              checked={metric.primary}
              onChange={() => onChange(metrics.map((m, j) => ({ ...m, primary: j === i })))}
            />
            Primary
          </label>
          <Select
            value={metric.direction}
            onValueChange={(value) => {
              if (value) onChange(metrics.map((m, j) => (j === i ? { ...m, direction: value as DesignMetric['direction'] } : m)))
            }}
          >
            <SelectTrigger className="w-32 shrink-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="maximize">Maximize</SelectItem>
              <SelectItem value="minimize">Minimize</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="ghost" size="icon-sm" aria-label="Remove metric" onClick={() => onChange(metrics.filter((_, j) => j !== i))}>
            <X className="size-3.5" />
          </Button>
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        onClick={() => onChange([...metrics, { name: '', primary: metrics.length === 0, direction: 'maximize' }])}
      >
        <Plus className="size-3.5" /> Add metric
      </Button>
    </div>
  )
}

// The experiment's full design declaration -- consolidates what used to be
// scattered across the canvas toolbar's DesignPreview and had no UI at all
// (hypothesis, replicates, randomization seed, metrics, coordination
// strategy). Edited as one local draft, persisted with a single explicit
// Save (matching FactorBindableField's own explicit-Save convention) rather
// than autosaving every keystroke across this many fields.
export function DesignTab({
  experiment,
  protocolId,
  canvasRef,
}: {
  experiment: Experiment
  protocolId: string | undefined
  canvasRef: RefObject<ProtocolCanvasHandle | null>
}) {
  const queryClient = useQueryClient()
  const graphQuery = useProtocolGraph(protocolId)
  const impactQuery = useQuery({
    queryKey: ['experiments', experiment.id, 'design-impact'],
    queryFn: () => experimentsApi.getDesignImpact(experiment.id),
  })

  const [hypothesis, setHypothesis] = useState(experiment.hypothesis ?? '')
  const [factors, setFactors] = useState<DesignFactor[]>(experiment.design_spec?.factors ?? [])
  // Nullable so the Input below can be backspaced to empty and retyped
  // without every keystroke snapping to 1 (matches the node inspectors'
  // convention) -- null only ever exists transiently while editing; it's
  // never sent to the server (see saveMutation's `replicates ?? 1`) and
  // every other read of this state falls back to `?? 1` too.
  const [replicates, setReplicates] = useState<number | null>(experiment.design_spec?.replicates ?? 1)
  const [randomizationSeed, setRandomizationSeed] = useState<number | null>(experiment.design_spec?.randomization_seed ?? null)
  const [metrics, setMetrics] = useState<DesignMetric[]>(experiment.design_spec?.metrics ?? [])
  const [coordinationSlug, setCoordinationSlug] = useState<CoordinationStrategySlug>(
    experiment.design_spec?.coordination_strategy?.slug ?? 'sequential',
  )

  // Re-seed local draft when a different experiment loads (or after a
  // successful save round-trips fresh server data back down).
  useEffect(() => {
    setHypothesis(experiment.hypothesis ?? '')
    setFactors(experiment.design_spec?.factors ?? [])
    setReplicates(experiment.design_spec?.replicates ?? 1)
    setRandomizationSeed(experiment.design_spec?.randomization_seed ?? null)
    setMetrics(experiment.design_spec?.metrics ?? [])
    setCoordinationSlug(experiment.design_spec?.coordination_strategy?.slug ?? 'sequential')
  }, [experiment])

  const saveMutation = useMutation({
    mutationFn: () =>
      experimentsApi.update(experiment.id, {
        hypothesis: hypothesis.trim() || null,
        design_spec: {
          ...experiment.design_spec,
          factors: factors.filter((f) => f.name.trim() !== ''),
          replicates: replicates ?? 1,
          randomization_seed: randomizationSeed,
          metrics: metrics.filter((m) => m.name.trim() !== ''),
          coordination_strategy: { slug: coordinationSlug, params: experiment.design_spec?.coordination_strategy?.params ?? {} },
        },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id] })
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id, 'design-impact'] })
    },
  })

  const generateMutation = useMutation({
    mutationFn: () => experimentsApi.generateDesign(experiment.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id, 'replicates'] })
      // A generate that changes the design's shape supersedes the current
      // revision and opens a new one, so the Cells tab's history list is stale
      // too -- not just the cell rows.
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id, 'design-revisions'] })
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id, 'design-impact'] })
    },
  })

  const validFactors = factors.filter((f) => f.name.trim() !== '' && f.levels.length > 0)
  const combinations = validFactors.reduce((acc, f) => acc * Math.max(f.levels.length, 1), 1)
  const totalTrials = validFactors.length > 0 ? combinations * Math.max(replicates ?? 1, 1) : 0

  const selectedStrategy = COORDINATION_STRATEGY_CATALOG.find((s) => s.slug === coordinationSlug)
  const unboundFactors = unboundFactorNames(
    experiment.design_spec,
    graphQuery.data
      ? ({ nodes: graphQuery.data.nodes, edges: graphQuery.data.edges } as unknown as ProtocolGraph)
      : undefined,
  )
  const impact = impactQuery.data

  const isDirty =
    hypothesis !== (experiment.hypothesis ?? '') ||
    JSON.stringify(factors) !== JSON.stringify(experiment.design_spec?.factors ?? []) ||
    replicates !== (experiment.design_spec?.replicates ?? 1) ||
    randomizationSeed !== (experiment.design_spec?.randomization_seed ?? null) ||
    JSON.stringify(metrics) !== JSON.stringify(experiment.design_spec?.metrics ?? []) ||
    coordinationSlug !== (experiment.design_spec?.coordination_strategy?.slug ?? 'sequential')
  const matrixDraftChanged =
    JSON.stringify(factors) !== JSON.stringify(experiment.design_spec?.factors ?? []) ||
    replicates !== (experiment.design_spec?.replicates ?? 1)

  return (
    <div className="flex flex-col gap-5 p-3 text-sm">
      <div className="space-y-1.5">
        <Label htmlFor="design-hypothesis" className="flex items-center gap-1.5">
          Hypothesis
          <InfoTooltip>Free-text notes on what you expect this experiment to show. Documentation only -- never read by any code.</InfoTooltip>
        </Label>
        <Textarea
          id="design-hypothesis"
          value={hypothesis}
          onChange={(e) => setHypothesis(e.target.value)}
          placeholder="What are you trying to learn from this experiment?"
          className="min-h-20"
        />
      </div>

      <div className="space-y-1.5">
        <Label className="flex items-center gap-1.5">
          Coordination strategy
          <InfoTooltip>
            Declares how the agents in this protocol work together as a multi-agent system -- separate from the
            canvas graph itself, which only wires connections. The graph must actually match whatever you pick here
            (e.g. "Critic Gate" requires a real Critic Gate node wired in) or running the protocol is rejected.
          </InfoTooltip>
        </Label>
        <Select value={coordinationSlug} onValueChange={(value) => value && setCoordinationSlug(value as CoordinationStrategySlug)}>
          <SelectTrigger className="w-full">
            <SelectValue>{() => selectedStrategy?.label ?? coordinationSlug}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {COORDINATION_STRATEGY_CATALOG.map((s) => (
              <SelectItem key={s.slug} value={s.slug}>
                {s.label}
                {!s.implemented && ' (coming soon)'}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {selectedStrategy && <p className="text-xs text-muted-foreground">{selectedStrategy.description}</p>}
        {selectedStrategy && !selectedStrategy.implemented && (
          <p className="text-xs text-[color:var(--chart-4)]">
            Not yet implemented -- coming with the ARES pattern migration. Saving this choice declares intent, but running
            this protocol will be rejected until it's backed.
          </p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label className="flex items-center gap-1.5">
          Experimental factors and levels
          <InfoTooltip>
            A factor is an independent variable you vary across cells (e.g. which model, whether a stage is enabled).
            Its levels are the specific values it can take. Every combination of levels across all factors becomes
            one cell.
          </InfoTooltip>
        </Label>
        <FactorsEditor experiment={experiment} protocolId={protocolId} canvasRef={canvasRef} factors={factors} />
      </div>

      <div className="space-y-1.5">
        <Label className="flex items-center gap-1.5">
          Design type
          <InfoTooltip>
            How cells get generated -- "factorial" means every combination of every factor's levels becomes its own
            cell. Set when the experiment was created, not editable here.
          </InfoTooltip>
        </Label>
        <p className="rounded-md border border-dashed px-2.5 py-1.5 text-xs text-muted-foreground capitalize">
          {experiment.design_type}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="design-replicates" className="flex items-center gap-1.5">
            Replicates
            <InfoTooltip>
              How many times each combination of factor levels is independently repeated (e.g. re-run with a
              different sample) to average out run-to-run noise.
            </InfoTooltip>
          </Label>
          <Input
            id="design-replicates"
            type="number"
            min="1"
            value={replicates ?? ''}
            onChange={(e) => setReplicates(e.target.value === '' ? null : Math.max(1, Number(e.target.value)))}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="design-seed" className="flex items-center gap-1.5">
            Randomization seed
            <InfoTooltip>
              Only shuffles the order planned replicates are generated in -- never which cells exist or their labels.
              Set one to make that shuffle reproducible; leave blank to keep replicates in their natural order.
            </InfoTooltip>
          </Label>
          <Input
            id="design-seed"
            type="number"
            placeholder="(none)"
            value={randomizationSeed ?? ''}
            onChange={(e) => setRandomizationSeed(e.target.value === '' ? null : Number(e.target.value))}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label className="flex items-center gap-1.5">
          Metrics
          <InfoTooltip>
            What this experiment records for each replicate. Primary marks the metric used for significance analysis;
            Maximize/Minimize says which direction is better.
          </InfoTooltip>
        </Label>
        <MetricsEditor metrics={metrics} onChange={setMetrics} />
      </div>

      <div className="space-y-1.5 rounded-md border bg-muted/30 px-3 py-2">
        <p className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
          {validFactors.length} factor{validFactors.length === 1 ? '' : 's'} → {combinations} cell
          {combinations === 1 ? '' : 's'} × {Math.max(replicates ?? 1, 1)} replicate{(replicates ?? 1) === 1 ? '' : 's'} = {totalTrials} total
          trial{totalTrials === 1 ? '' : 's'}
          <InfoTooltip>
            A "cell" here is one specific combination of factor levels (e.g. Model=A × Effort=medium) -- same term
            as the Cells tab, before replication. 2 factors with 2 levels each = 4 cells; × replicates = total
            trials.
          </InfoTooltip>
        </p>
        {matrixDraftChanged && isDirty && (
          <div className="rounded-md border border-[color:var(--chart-4)]/40 bg-[color:var(--chart-4)]/10 px-2.5 py-2 text-xs text-muted-foreground">
            <p className="font-medium text-foreground">Design changed — regeneration required.</p>
            <p className="mt-1">Save these changes to review their impact and regenerate the current design.</p>
          </div>
        )}
        {impact?.regeneration_required && !isDirty && (
          <div className="rounded-md border border-[color:var(--chart-4)]/40 bg-[color:var(--chart-4)]/10 px-2.5 py-2 text-xs text-muted-foreground">
            <p className="font-medium text-foreground">Design changed — regeneration required.</p>
            <p className="mt-1">
              {impact.current_cell_count} → {impact.proposed_cell_count} cells; {impact.current_replicate_count} →{' '}
              {impact.proposed_replicate_count} replicates. Replicates: {impact.added_replicate_count} added,{' '}
              {impact.retained_replicate_count} retained
              {impact.removed_replicate_count ? `, ${impact.removed_replicate_count} moved to history` : ''}.
            </p>
          </div>
        )}
        {unboundFactors.length > 0 && (
          <p className="rounded-md border border-destructive/40 bg-destructive/10 px-2.5 py-2 text-xs text-destructive">
            Unbound factor{unboundFactors.length === 1 ? '' : 's'}: {unboundFactors.join(', ')}. Rebind on the canvas or remove from this design.
          </p>
        )}
        <Button
          size="sm"
          variant="outline"
          disabled={
            generateMutation.isPending ||
            isDirty ||
            (validFactors.length === 0 && !impact?.regeneration_required) ||
            unboundFactors.length > 0
          }
          onClick={() => generateMutation.mutate()}
        >
          {generateMutation.isPending ? 'Generating…' : impact?.regeneration_required ? 'Update design' : 'Generate design'}
        </Button>
        {isDirty && <p className="text-xs text-muted-foreground">Save your changes before generating.</p>}
        {generateMutation.data && (
          <p className="text-xs text-muted-foreground">
            {combinations} {combinations === 1 ? 'cell' : 'cells'} · {generateMutation.data.length}{' '}
            {generateMutation.data.length === 1 ? 'replicate' : 'replicates'} total
          </p>
        )}
      </div>

      <Button disabled={!isDirty || saveMutation.isPending} onClick={() => saveMutation.mutate()}>
        {saveMutation.isPending ? 'Saving…' : 'Save design'}
      </Button>
    </div>
  )
}
