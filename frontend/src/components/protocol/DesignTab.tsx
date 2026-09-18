import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import { Pencil, Plus, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { experimentsApi, protocolsApi } from '@/api/client'
import { coordinationStrategyIssues } from '@/lib/coordinationStrategy'
import { unboundFactorNames } from '@/lib/factorBindings'
import { promptReferenceScope } from '@/lib/promptReferences'
import { protocolGraphQueryKey } from '@/lib/protocolGraph'
import {
  factorBoundField,
  revealsHiddenMcpServers,
  toolFactorServerId,
  unboundBindableFields,
  type UnboundField,
} from './bindableFields'
import { LEVEL_TYPE_LABELS, levelTypeOf } from './factorLevels'
import { FactorEditorDialog } from './FactorEditorDialog'
import { InfoTooltip } from './InfoTooltip'
import { MetricsEditor } from './MetricsEditor'
export { MetricsEditor } from './MetricsEditor'
import { normalizeDesignMetrics } from '@/lib/metricCatalog'
import type { ProtocolCanvasHandle } from './ProtocolCanvas'
import type { ProtocolEdge, ProtocolGraph, ProtocolNode } from '@/types/protocols'
import {
  COORDINATION_STRATEGY_CATALOG,
  type CoordinationStrategySlug,
  type DesignFactor,
  type DesignMetric,
  type Experiment,
  type MeasurementPlan,
} from '@/types/experiments'

const AUTOSAVE_DELAY_MS = 800

const REGENERATION_REASON_LABELS: Record<string, string> = {
  no_design_generated: 'No cells have been generated for this design yet.',
  coordination_strategy_changed: 'The coordination strategy changed, so every cell would run differently.',
  design_matrix_changed: 'The factors or replicate count changed.',
  cells_drifted: 'Some existing cells no longer match this design.',
}

// Autosaved because none of it changes which cells exist. The coordination
// strategy is here despite changing how every cell EXECUTES, because losing a
// user's selection is worse than saving it early: the design REVISION keeps
// the strategy its cells were generated under, so the moment this saves,
// `get_design_impact` compares the two and the tab says the cells are out of
// date. The demand for a regenerate comes from the materialized data rather
// than from a local draft diff -- which is also what makes it work on an
// experiment with no factorial matrix at all.
type MetadataDraft = {
  hypothesis: string
  randomizationSeed: number | null
  metrics: DesignMetric[]
  measurementPlan: MeasurementPlan | null
  coordinationSlug: CoordinationStrategySlug
}

type MetadataSave = {
  draft: MetadataDraft
  metadataKey: string
  matrixKey: string
  key: string
}

// Creating, renaming, and deleting a factor (this function and
// FactorsEditor's own edit/delete mutations below) are the one set of
// actions here that ISN'T staged into the local draft + explicit update flow
// the rest of this tab uses -- each is a two-sided write
// (design_spec.factors AND every canvas node's own factor_bindings that
// reference this factor by name) that has to land atomically, so all three
// mirror FactorBindableField's own existing immediate-save popover instead.
// Staging a rename/delete until an explicit update would leave a bound node
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

// What a prompt factor's levels may reference, for any node on the canvas.
// Both dialog entry points below need it: "Add factor" because the field is
// picked inside the dialog, "Edit factor" because the binding it resolves
// points at a node this tab never rendered.
function usePromptScopeFor(protocolId: string | undefined) {
  const graphQuery = useProtocolGraph(protocolId)
  const graph = graphQuery.data
  return useCallback(
    (nodeId: string) =>
      promptReferenceScope(
        (graph?.nodes ?? []) as unknown as ProtocolNode[],
        (graph?.edges ?? []) as unknown as ProtocolEdge[],
        nodeId,
      ),
    [graph],
  )
}

function AddFactorButton({
  experiment,
  protocolId,
  canvasRef,
  existingNames,
  disabled = false,
}: {
  experiment: Experiment
  protocolId: string | undefined
  canvasRef: RefObject<ProtocolCanvasHandle | null>
  existingNames: string[]
  disabled?: boolean
}) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const queryClient = useQueryClient()

  const graphQuery = useProtocolGraph(protocolId)
  const promptScopeFor = usePromptScopeFor(protocolId)

  const createMutation = useMutation({
    mutationFn: async ({ factor, field }: { factor: DesignFactor; field: UnboundField }) => {
      // Fetched fresh (not the local staged draft) -- matches
      // FactorBindableField's own existing save mutation exactly, including
      // its same pre-existing risk of racing a concurrent unsaved edit
      // elsewhere on this tab (see FactorEditorDialog.tsx's design notes).
      const fresh = await experimentsApi.get(experiment.id)
      const nextFactors = [...(fresh.design_spec?.factors ?? []).filter((candidate) => candidate.name !== factor.name), factor]
      await experimentsApi.update(experiment.id, { design_spec: { ...fresh.design_spec, factors: nextFactors } })
      return { factor, field }
    },
    onSuccess: ({ factor, field }) => {
      canvasRef.current?.bindFactor(field.nodeId, field.fieldPath, factor.name)
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id] })
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id, 'design-impact'] })
    },
  })

  const fields = unboundBindableFields(graphQuery.data?.nodes ?? [], graphQuery.data?.edges ?? [])

  return (
    <>
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => setDialogOpen(true)}>
        <Plus className="size-3.5" /> Add factor
      </Button>

      {dialogOpen && (
        <FactorEditorDialog
          open
          onOpenChange={setDialogOpen}
          factor={{ name: '', levels: [], level_type: 'string' }}
          revealHiddenServers={revealsHiddenMcpServers(graphQuery.data?.nodes ?? [])}
          promptScopeFor={promptScopeFor}
          pickableFields={fields}
          existingNames={existingNames}
          onSave={(factor, field) => {
            if (field) return createMutation.mutateAsync({ factor, field })
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
// staged-draft-then-update flow the way hypothesis/replicates/metrics are.
// A factor can be bound from one or more canvas nodes' own factor_bindings
// (by name, a plain string), so removing or renaming one has to also sweep
// those bindings via canvasRef in the same action; staging the removal
// until a later update would leave every bound node silently
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
  disabled = false,
}: {
  experiment: Experiment
  protocolId: string | undefined
  canvasRef: RefObject<ProtocolCanvasHandle | null>
  factors: DesignFactor[]
  disabled?: boolean
}) {
  const [editingFactor, setEditingFactor] = useState<{ index: number; draft: DesignFactor } | null>(null)
  const queryClient = useQueryClient()
  const graphQuery = useProtocolGraph(protocolId)
  const promptScopeFor = usePromptScopeFor(protocolId)

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
            disabled={disabled}
            onClick={() => setEditingFactor({ index: i, draft: factor })}
          >
            <Pencil className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Remove factor"
            disabled={disabled || deleteMutation.isPending}
            onClick={() => deleteMutation.mutate(factor.name)}
          >
            <X className="size-3.5" />
          </Button>
        </div>
      ))}
      <AddFactorButton experiment={experiment} protocolId={protocolId} canvasRef={canvasRef} existingNames={factors.map((f) => f.name)} disabled={disabled} />

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
          // Same idea one step further along: a factor edited from here was
          // bound somewhere else entirely, so which node its levels belong to
          // has to be recovered from the canvas's own factor_bindings.
          boundField={factorBoundField(graphQuery.data?.nodes ?? [], editingFactor.draft.name)}
          promptScopeFor={promptScopeFor}
          onSave={(next) => editMutation.mutateAsync({ oldName: editingFactor.draft.name, next })}
        />
      )}
    </div>
  )
}

// autosave after a short pause, matching the canvas and node inspectors.
export function DesignTab({
  experiment,
  protocolId,
  canvasRef,
  onDesignUpdatePendingChange,
}: {
  experiment: Experiment
  protocolId: string | undefined
  canvasRef: RefObject<ProtocolCanvasHandle | null>
  onDesignUpdatePendingChange: (pending: boolean) => void
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
  const [metrics, setMetrics] = useState<DesignMetric[]>(() => normalizeDesignMetrics(experiment.design_spec?.metrics))
  const [measurementPlan, setMeasurementPlan] = useState<MeasurementPlan | null>(experiment.measurement_plan)
  const [coordinationSlug, setCoordinationSlug] = useState<CoordinationStrategySlug>(
    experiment.design_spec?.coordination_strategy?.slug ?? 'sequential',
  )
  // Held rather than applied while the confirm dialog is open -- see chooseStrategy.
  const [pendingStrategy, setPendingStrategy] = useState<{ slug: CoordinationStrategySlug; issues: string[] } | null>(
    null,
  )
  const metadataDraft: MetadataDraft = { hypothesis, randomizationSeed, metrics, measurementPlan, coordinationSlug }
  const metadataDraftKey = JSON.stringify(metadataDraft)
  const matrixDraftKey = JSON.stringify({ factors, replicates })
  const latestMetadataDraftKey = useRef(metadataDraftKey)
  const latestMatrixDraftKey = useRef(matrixDraftKey)
  const pendingMetadataSaveRef = useRef<MetadataSave | null>(null)
  const lastScheduledMetadataSaveKeyRef = useRef<string | null>(null)
  const metadataSaveQueueRef = useRef(Promise.resolve())
  latestMetadataDraftKey.current = metadataDraftKey
  latestMatrixDraftKey.current = matrixDraftKey

  function persistMetadataDraft(draft: MetadataDraft) {
    // Every metadata write shares one queue because each PATCH replaces nested
    // design fields. A later transaction must read only after the preceding
    // write settles or an older response can restore stale metric selections.
    const save = metadataSaveQueueRef.current.then(async () => {
      const fresh = await experimentsApi.get(experiment.id)
      return experimentsApi.update(experiment.id, {
        hypothesis: draft.hypothesis.trim() || null,
        measurement_plan: draft.measurementPlan,
        measurement_validation_protocol_id: protocolId ?? null,
        design_spec: {
          ...fresh.design_spec,
          randomization_seed: draft.randomizationSeed,
          metrics: draft.metrics.filter((metric) => metric.name.trim() !== ''),
          coordination_strategy: {
            slug: draft.coordinationSlug,
            params: fresh.design_spec?.coordination_strategy?.params ?? {},
          },
        },
      })
    })
    metadataSaveQueueRef.current = save.then(() => undefined, () => undefined)
    return save
  }

  // Re-seed local draft when a different experiment loads (or after a
  // successful save round-trips fresh server data back down).
  useEffect(() => {
    setHypothesis(experiment.hypothesis ?? '')
    setFactors(experiment.design_spec?.factors ?? [])
    setReplicates(experiment.design_spec?.replicates ?? 1)
    setRandomizationSeed(experiment.design_spec?.randomization_seed ?? null)
    setMetrics(normalizeDesignMetrics(experiment.design_spec?.metrics))
    setMeasurementPlan(experiment.measurement_plan)
    setCoordinationSlug(experiment.design_spec?.coordination_strategy?.slug ?? 'sequential')
  }, [experiment])

  function designDraft() {
    if (experiment.locked_at) {
      // The lock deliberately permits only this one extension. Avoid sending
      // untouched metadata in the same request: the API correctly treats a
      // metadata field in a locked mutation as an attempt to edit the design.
      return { design_spec: { ...experiment.design_spec, replicates: replicates ?? 1 } }
    }
    return {
      hypothesis: hypothesis.trim() || null,
      measurement_plan: measurementPlan,
      measurement_validation_protocol_id: protocolId ?? null,
      design_spec: {
        ...experiment.design_spec,
        factors: factors.filter((f) => f.name.trim() !== ''),
        replicates: replicates ?? 1,
        randomization_seed: randomizationSeed,
        metrics: metrics.filter((m) => m.name.trim() !== ''),
        coordination_strategy: { slug: coordinationSlug, params: experiment.design_spec?.coordination_strategy?.params ?? {} },
      },
    }
  }

  // Metadata does not change which cells exist, so it follows the node
  // inspector convention: debounce a patch rather than asking for a separate
  // save click. Read the experiment fresh before patching so this cannot put a
  // locally pending factors/replicates change onto the server early.
  const metadataSaveMutation = useMutation({
    mutationFn: ({ draft }: MetadataSave) => persistMetadataDraft(draft),
    onSuccess: (updated, saved) => {
      // An older response must not reset a newer local edit (including a
      // pending factors/replicates update). The next quiet pause will persist
      // that newer metadata snapshot instead.
      if (latestMetadataDraftKey.current !== saved.metadataKey || latestMatrixDraftKey.current !== saved.matrixKey) return
      queryClient.setQueryData(['experiments', experiment.id], updated)
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
      // A saved coordination strategy is now compared against the one the
      // current design revision's cells were generated under, so the
      // out-of-date verdict changes even though no cell did.
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id, 'design-impact'] })
    },
  })
  const { mutate: autosaveMetadata, isPending: isAutosavingMetadata } = metadataSaveMutation

  const generateMutation = useMutation({
    // Submit the pending declaration with the generation request. This is
    // deliberately one API operation: the first click cannot generate from a
    // pre-save database snapshot and leave the changed cells pending.
    mutationFn: () => experimentsApi.generateDesign(experiment.id, isDirty ? designDraft() : undefined),
    onSuccess: () => {
      // The local draft is compared with this experiment query. Refresh it
      // along with the generated rows; otherwise the old declaration remains
      // in cache and the UI keeps claiming that the just-applied update is
      // still pending.
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id] })
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
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
  const draftGraph = graphQuery.data
    ? ({ nodes: graphQuery.data.nodes, edges: graphQuery.data.edges } as unknown as ProtocolGraph)
    : undefined
  const unboundFactors = unboundFactorNames(experiment.design_spec, draftGraph)
  const impact = impactQuery.data

  // A design-time mirror of the backend's own strategy validation, so an
  // incompatible pick says so under the picker rather than being rejected at
  // publish or run time. Advisory on purpose -- the strategy has to be
  // selectable before the canvas matches it, or the design loop deadlocks.
  const strategyIssues = coordinationStrategyIssues(coordinationSlug, draftGraph)

  function chooseStrategy(next: CoordinationStrategySlug) {
    if (next === coordinationSlug) return
    const nextIssues = coordinationStrategyIssues(next, draftGraph)
    // Confirm only when the switch BREAKS a canvas that currently works.
    // Moving between two already-incompatible states needs no ceremony -- the
    // inline notice under the picker already says what is wrong, and a dialog
    // on every pick would train the user to dismiss it.
    if (nextIssues.length > 0 && strategyIssues.length === 0) {
      setPendingStrategy({ slug: next, issues: nextIssues })
      return
    }
    setCoordinationSlug(next)
  }

  const isDirty =
    hypothesis !== (experiment.hypothesis ?? '') ||
    JSON.stringify(factors) !== JSON.stringify(experiment.design_spec?.factors ?? []) ||
    replicates !== (experiment.design_spec?.replicates ?? 1) ||
    randomizationSeed !== (experiment.design_spec?.randomization_seed ?? null) ||
    JSON.stringify(metrics) !== JSON.stringify(experiment.design_spec?.metrics ?? []) ||
    JSON.stringify(measurementPlan) !== JSON.stringify(experiment.measurement_plan) ||
    coordinationSlug !== (experiment.design_spec?.coordination_strategy?.slug ?? 'sequential')
  const matrixDraftChanged =
    JSON.stringify(factors) !== JSON.stringify(experiment.design_spec?.factors ?? []) ||
    replicates !== (experiment.design_spec?.replicates ?? 1)
  const metadataDraftChanged =
    hypothesis !== (experiment.hypothesis ?? '') ||
    randomizationSeed !== (experiment.design_spec?.randomization_seed ?? null) ||
    JSON.stringify(metrics) !== JSON.stringify(experiment.design_spec?.metrics ?? []) ||
    JSON.stringify(measurementPlan) !== JSON.stringify(experiment.measurement_plan) ||
    coordinationSlug !== (experiment.design_spec?.coordination_strategy?.slug ?? 'sequential')
  const canGenerate = validFactors.length > 0 || impact?.regeneration_required === true
  const needsDesignUpdate = matrixDraftChanged || impact?.regeneration_required === true
  const metadataSaveKey = `${metadataDraftKey}:${matrixDraftKey}`
  const isLocked = !!experiment.locked_at
  async function applyMetrics(
    nextMetrics: DesignMetric[],
    nextPlan: MeasurementPlan | null,
  ) {
    // Cancel a not-yet-started metadata debounce and enqueue this transaction
    // after any write already in flight. Otherwise an older autosave response
    // could land last and restore the previous measurement plan.
    const interruptedMetadataSave = pendingMetadataSaveRef.current
    pendingMetadataSaveRef.current = null
    const save = persistMetadataDraft({
      hypothesis,
      randomizationSeed,
      metrics: nextMetrics,
      measurementPlan: nextPlan,
      coordinationSlug,
    })
    let updated: Experiment
    try {
      updated = await save
    } catch (error) {
      // A failed metric autosave must not swallow unrelated metadata that was
      // waiting for its debounce. Put that exact snapshot back through the
      // same queue while the metric draft remains available for retry.
      if (interruptedMetadataSave) autosaveMetadata(interruptedMetadataSave)
      throw error
    }

    // This transaction owns this snapshot. Mark it as already saved
    // before updating local state so the surrounding metadata autosave cannot
    // emit a duplicate PATCH while the refreshed experiment propagates.
    const nextMetadataKey = JSON.stringify({ hypothesis, randomizationSeed, metrics: nextMetrics, measurementPlan: nextPlan, coordinationSlug })
    lastScheduledMetadataSaveKeyRef.current = `${nextMetadataKey}:${matrixDraftKey}`
    setMetrics(nextMetrics)
    setMeasurementPlan(nextPlan)
    queryClient.setQueryData(['experiments', experiment.id], updated)
    queryClient.invalidateQueries({ queryKey: ['experiments'] })
    queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id, 'design-impact'] })
  }

  // The amber border belongs to the surrounding sidebar, while the evidence
  // for it (an unsaved local matrix edit or a stale materialized design) lives
  // here. Keep the two in sync, including when this panel is closed.
  useEffect(() => {
    onDesignUpdatePendingChange(needsDesignUpdate)
    return () => onDesignUpdatePendingChange(false)
  }, [needsDesignUpdate, onDesignUpdatePendingChange])

  // Keep the latest delayed write in a ref so unmounting (navigating away or
  // switching to Runs) can flush it immediately. Once a snapshot has been
  // handed to the mutation, do not schedule the identical snapshot again.
  if (metadataDraftChanged && !matrixDraftChanged && lastScheduledMetadataSaveKeyRef.current !== metadataSaveKey) {
    pendingMetadataSaveRef.current = { draft: metadataDraft, metadataKey: metadataDraftKey, matrixKey: matrixDraftKey, key: metadataSaveKey }
  } else if (!metadataDraftChanged || matrixDraftChanged) {
    pendingMetadataSaveRef.current = null
  }

  useEffect(() => {
    // When a cell-changing draft is pending, Apply changes to cells owns the
    // complete declaration. Do not let an autosave race ahead with only its
    // metadata.
    const saved = pendingMetadataSaveRef.current
    if (!saved) return
    const timer = setTimeout(() => {
      // A later keystroke may already have replaced this snapshot while this
      // timer was waiting.
      if (pendingMetadataSaveRef.current?.key !== saved.key) return
      lastScheduledMetadataSaveKeyRef.current = saved.key
      pendingMetadataSaveRef.current = null
      autosaveMetadata(saved)
    }, AUTOSAVE_DELAY_MS)
    return () => clearTimeout(timer)
  }, [autosaveMetadata, metadataSaveKey])

  // Same unmount flush the protocol canvas uses for its debounced autosave:
  // leaving this view must not make the final edit disappear just because its
  // 800 ms quiet period had not elapsed yet.
  useEffect(() => {
    return () => {
      const saved = pendingMetadataSaveRef.current
      if (!saved) return
      lastScheduledMetadataSaveKeyRef.current = saved.key
      pendingMetadataSaveRef.current = null
      autosaveMetadata(saved)
    }
  }, [autosaveMetadata])

  return (
    <div className="flex flex-col gap-5 p-3 text-sm">
      {isLocked && (
        <div className="rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-xs text-muted-foreground">
          <p className="font-medium text-foreground">Experiment locked</p>
          <p className="mt-1">The canvas and design are fixed for reproducible runs. You can still change the replicate count and apply that change to the cells.</p>
        </div>
      )}
      <div className="space-y-1.5">
        <Label htmlFor="design-hypothesis" className="flex items-center gap-1.5">
          Hypothesis
          <InfoTooltip>Free-text notes on what you expect this experiment to show. Documentation only -- never read by any code.</InfoTooltip>
        </Label>
        <Textarea
          id="design-hypothesis"
          value={hypothesis}
          disabled={isLocked}
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
            ("Critic Gate" requires a real Critic Gate node wired in; "Peer Collaboration" requires at least two
            connected Agent nodes, with one of them left unfed to lead; "Supervisor" requires one agent handing off to
            the workers, plus at most one further agent to review them) or running the protocol is rejected.
          </InfoTooltip>
        </Label>
        <Select value={coordinationSlug} disabled={isLocked} onValueChange={(value) => value && chooseStrategy(value as CoordinationStrategySlug)}>
          <SelectTrigger className="w-full" disabled={isLocked}>
            <SelectValue>{() => selectedStrategy?.label ?? coordinationSlug}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {COORDINATION_STRATEGY_CATALOG.map((s) => (
              <SelectItem key={s.slug} value={s.slug}>
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {selectedStrategy && <p className="text-xs text-muted-foreground">{selectedStrategy.description}</p>}
        {/* A live mirror of the backend's own check, so the mismatch shows up
            here instead of as a rejected run. Amber, not destructive: the
            selection is saved either way, and wiring the canvas to match is a
            normal next step rather than an error to undo. */}
        {strategyIssues.length > 0 && (
          <div className="rounded-md border border-[color:var(--chart-4)]/40 bg-[color:var(--chart-4)]/10 px-2.5 py-2 text-xs text-muted-foreground">
            <p className="font-medium text-foreground">
              The canvas doesn't match {selectedStrategy?.label ?? coordinationSlug} yet.
            </p>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">
              {strategyIssues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
            <p className="mt-1">Running or publishing this protocol is rejected until the wiring matches.</p>
          </div>
        )}
      </div>

      <Dialog open={!!pendingStrategy} onOpenChange={(open) => !open && setPendingStrategy(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Switch to {COORDINATION_STRATEGY_CATALOG.find((s) => s.slug === pendingStrategy?.slug)?.label}?</DialogTitle>
            <DialogDescription>
              This canvas currently runs under {selectedStrategy?.label ?? coordinationSlug}. Under the new strategy it
              doesn't, so the protocol can't run until you rewire it.
            </DialogDescription>
          </DialogHeader>
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
            {pendingStrategy?.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">
            Nothing on the canvas is deleted, and switching back restores this state. Cells already generated stay
            readable under the design revision that produced them, but they'll need regenerating before the new
            strategy's results are comparable.
          </p>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setPendingStrategy(null)}>
              Keep {selectedStrategy?.label ?? coordinationSlug}
            </Button>
            <Button
              size="sm"
              onClick={() => {
                if (pendingStrategy) setCoordinationSlug(pendingStrategy.slug)
                setPendingStrategy(null)
              }}
            >
              Switch anyway
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <div className="space-y-1.5">
        <Label className="flex items-center gap-1.5">
          Experimental factors and levels
          <InfoTooltip>
            A factor is an independent variable you vary across cells (e.g. which model, whether a stage is enabled).
            Its levels are the specific values it can take. Every combination of levels across all factors becomes
            one cell.
          </InfoTooltip>
        </Label>
        <FactorsEditor experiment={experiment} protocolId={protocolId} canvasRef={canvasRef} factors={factors} disabled={isLocked} />
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
            disabled={isLocked}
            onChange={(e) => setRandomizationSeed(e.target.value === '' ? null : Number(e.target.value))}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label className="flex items-center gap-1.5">
          Metrics
          <InfoTooltip>
            Select which built-in metrics belong in this experiment's analysis and manage custom metrics
            created from Script or MCP Tool nodes.
          </InfoTooltip>
        </Label>
        <MetricsEditor experimentId={experiment.id} metrics={metrics} measurementPlan={measurementPlan} graph={graphQuery.data as ProtocolGraph | undefined} onChange={(nextMetrics, nextPlan) => { setMetrics(nextMetrics); setMeasurementPlan(nextPlan) }} onApplyMetrics={applyMetrics} disabled={isLocked} />
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
            <p className="font-medium text-foreground">Design updates pending.</p>
            <p className="mt-1">Select Apply changes to cells to apply these changes and regenerate the cells.</p>
          </div>
        )}
        {impact?.regeneration_required && !isDirty && (
          <div className="rounded-md border border-[color:var(--chart-4)]/40 bg-[color:var(--chart-4)]/10 px-2.5 py-2 text-xs text-muted-foreground">
            <p className="font-medium text-foreground">Cells are out of date.</p>
            {/* The counts alone can read as "nothing changed" -- a coordination
                strategy switch regenerates every cell while adding and removing
                none of them -- so name the reason before showing them. */}
            {impact.regeneration_reasons.length > 0 && (
              <p className="mt-1">
                {impact.regeneration_reasons.map((reason) => REGENERATION_REASON_LABELS[reason] ?? reason).join(' ')}
              </p>
            )}
            <p className="mt-1">
              {impact.current_cell_count} → {impact.proposed_cell_count} cells; {impact.current_replicate_count} →{' '}
              {impact.proposed_replicate_count} replicates. Replicates: {impact.added_replicate_count} added,{' '}
              {impact.retained_replicate_count} retained
              {impact.removed_replicate_count ? `, ${impact.removed_replicate_count} moved to history` : ''}. Select Apply changes to cells to regenerate them.
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
          disabled={
            generateMutation.isPending ||
            isAutosavingMetadata ||
            !canGenerate ||
            unboundFactors.length > 0
          }
          onClick={() => generateMutation.mutate()}
        >
          {generateMutation.isPending ? 'Generating cells…' : needsDesignUpdate ? 'Apply changes to cells' : 'Generate cells'}
        </Button>
        {generateMutation.data && (
          <p className="text-xs text-muted-foreground">
            {combinations} {combinations === 1 ? 'cell' : 'cells'} · {generateMutation.data.length}{' '}
            {generateMutation.data.length === 1 ? 'replicate' : 'replicates'} total
          </p>
        )}
      </div>
    </div>
  )
}
