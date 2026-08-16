import { useEffect, useState, type RefObject } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import { Pencil, Plus, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { experimentsApi, protocolsApi } from '@/api/client'
import { protocolGraphQueryKey } from '@/lib/protocolGraph'
import { unboundBindableFields, type UnboundField } from './bindableFields'
import { computeFactorName, defaultLevelsForType, LEVEL_TYPE_LABELS, levelTypeOf } from './factorLevels'
import { FactorEditorDialog } from './FactorEditorDialog'
import type { ProtocolCanvasHandle } from './ProtocolCanvas'
import {
  COORDINATION_STRATEGY_CATALOG,
  type CoordinationStrategySlug,
  type DesignFactor,
  type DesignMetric,
  type Experiment,
} from '@/types/experiments'

// Creating a factor is the one action here that ISN'T staged into the local
// draft + explicit "Save design" flow the rest of this tab uses -- it's a
// two-sided write (design_spec.factors AND the canvas node's own
// factor_bindings) that has to land atomically, so it mirrors
// FactorBindableField's own existing immediate-save popover instead. The
// canvas write goes through canvasRef (see ProtocolCanvas.tsx's own comment
// on ProtocolCanvasHandle) since DesignTab is a sibling of ProtocolCanvas,
// not a descendant, and has no other way to reach its live node state; the
// list of what's bindable comes from the same shared query cache
// (protocolGraphQueryKey) ProtocolCanvas mirrors its own nodes into on every
// change, so this never lags behind the canvas waiting on autosave.
function AddFactorPopover({
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
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<{ field: UnboundField; factor: DesignFactor } | null>(null)
  const queryClient = useQueryClient()

  const graphQuery = useQuery({
    queryKey: protocolGraphQueryKey(protocolId ?? 'none'),
    queryFn: async () => {
      if (!protocolId) return { nodes: [] as Node[], edges: [] as Edge[] }
      const protocol = await protocolsApi.get(protocolId)
      return { nodes: protocol.graph.nodes as Node[], edges: protocol.graph.edges as Edge[] }
    },
    enabled: !!protocolId && open,
    // Only ever a fallback for before ProtocolCanvas has mounted and mirrored
    // its own live state into this same key -- once it has, this key is only
    // ever updated by that mirror (a pure in-memory write), never a real
    // background refetch racing it with a stale server snapshot.
    staleTime: Infinity,
  })

  const createMutation = useMutation({
    mutationFn: async (next: DesignFactor) => {
      // Fetched fresh (not the local staged draft) -- matches
      // FactorBindableField's own existing save mutation exactly, including
      // its same pre-existing risk of racing a concurrent unsaved edit
      // elsewhere on this tab (see FactorEditorDialog.tsx's design notes).
      const fresh = await experimentsApi.get(experiment.id)
      const nextFactors = [...(fresh.design_spec?.factors ?? []), next]
      await experimentsApi.update(experiment.id, { design_spec: { ...fresh.design_spec, factors: nextFactors } })
      return next
    },
    onSuccess: (next) => {
      if (editing) canvasRef.current?.bindFactor(editing.field.nodeId, editing.field.fieldPath, next.name)
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id] })
      setEditing(null)
    },
  })

  const fields = unboundBindableFields(graphQuery.data?.nodes ?? [])

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          render={
            <Button variant="outline" size="sm">
              <Plus className="size-3.5" /> Add factor
            </Button>
          }
        />
        <PopoverContent className="space-y-1.5">
          <Label>Bind a field on the canvas</Label>
          {graphQuery.isLoading ? (
            <p className="text-xs text-muted-foreground">Loading canvas…</p>
          ) : fields.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              Every bindable field on the canvas is already a factor -- or there's nothing bindable on it yet.
            </p>
          ) : (
            <div className="max-h-64 space-y-0.5 overflow-y-auto">
              {fields.map((field) => (
                <button
                  key={`${field.nodeId}.${field.fieldPath}`}
                  type="button"
                  className="flex w-full cursor-pointer items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
                  onClick={() => {
                    const name = computeFactorName(field.nodeLabel, field.fieldLabel, existingNames)
                    setEditing({ field, factor: { name, levels: defaultLevelsForType(field.levelType), level_type: field.levelType } })
                    setOpen(false)
                  }}
                >
                  <span className="truncate">
                    {field.nodeLabel}: {field.fieldLabel}
                  </span>
                  <Badge variant="outline" className="shrink-0">
                    {LEVEL_TYPE_LABELS[field.levelType]}
                  </Badge>
                </button>
              ))}
            </div>
          )}
        </PopoverContent>
      </Popover>

      {editing && (
        <FactorEditorDialog
          open
          onOpenChange={(open) => {
            if (!open) setEditing(null)
          }}
          factor={editing.factor}
          onSave={(next) => createMutation.mutate(next)}
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
function FactorsEditor({
  experiment,
  protocolId,
  canvasRef,
  factors,
  onChange,
}: {
  experiment: Experiment
  protocolId: string | undefined
  canvasRef: RefObject<ProtocolCanvasHandle | null>
  factors: DesignFactor[]
  onChange: (factors: DesignFactor[]) => void
}) {
  const [editingFactor, setEditingFactor] = useState<{ index: number; draft: DesignFactor } | null>(null)

  function save(next: DesignFactor) {
    if (!editingFactor) return
    onChange(factors.map((f, j) => (j === editingFactor.index ? next : f)))
  }

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
            onClick={() => onChange(factors.filter((_, j) => j !== i))}
          >
            <X className="size-3.5" />
          </Button>
        </div>
      ))}
      <AddFactorPopover experiment={experiment} protocolId={protocolId} canvasRef={canvasRef} existingNames={factors.map((f) => f.name)} />

      {editingFactor && (
        <FactorEditorDialog
          open
          onOpenChange={(open) => {
            if (!open) setEditingFactor(null)
          }}
          factor={editingFactor.draft}
          onSave={save}
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

  const [hypothesis, setHypothesis] = useState(experiment.hypothesis ?? '')
  const [factors, setFactors] = useState<DesignFactor[]>(experiment.design_spec?.factors ?? [])
  const [replicates, setReplicates] = useState(experiment.design_spec?.replicates ?? 1)
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
          replicates,
          randomization_seed: randomizationSeed,
          metrics: metrics.filter((m) => m.name.trim() !== ''),
          coordination_strategy: { slug: coordinationSlug, params: experiment.design_spec?.coordination_strategy?.params ?? {} },
        },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id] })
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
    },
  })

  const generateMutation = useMutation({
    mutationFn: () => experimentsApi.generateDesign(experiment.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id, 'cells'] })
    },
  })

  const validFactors = factors.filter((f) => f.name.trim() !== '' && f.levels.length > 0)
  const combinations = validFactors.reduce((acc, f) => acc * Math.max(f.levels.length, 1), 1)
  const totalTrials = validFactors.length > 0 ? combinations * Math.max(replicates, 1) : 0

  const selectedStrategy = COORDINATION_STRATEGY_CATALOG.find((s) => s.slug === coordinationSlug)

  const isDirty =
    hypothesis !== (experiment.hypothesis ?? '') ||
    JSON.stringify(factors) !== JSON.stringify(experiment.design_spec?.factors ?? []) ||
    replicates !== (experiment.design_spec?.replicates ?? 1) ||
    randomizationSeed !== (experiment.design_spec?.randomization_seed ?? null) ||
    JSON.stringify(metrics) !== JSON.stringify(experiment.design_spec?.metrics ?? []) ||
    coordinationSlug !== (experiment.design_spec?.coordination_strategy?.slug ?? 'sequential')

  return (
    <div className="flex flex-col gap-5 p-3 text-sm">
      <div className="space-y-1.5">
        <Label htmlFor="design-hypothesis">Hypothesis</Label>
        <Textarea
          id="design-hypothesis"
          value={hypothesis}
          onChange={(e) => setHypothesis(e.target.value)}
          placeholder="What are you trying to learn from this experiment?"
          className="min-h-20"
        />
      </div>

      <div className="space-y-1.5">
        <Label>Coordination strategy</Label>
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
        <Label>Factors and levels</Label>
        <FactorsEditor
          experiment={experiment}
          protocolId={protocolId}
          canvasRef={canvasRef}
          factors={factors}
          onChange={setFactors}
        />
      </div>

      <div className="space-y-1.5">
        <Label>Design type</Label>
        <p className="rounded-md border border-dashed px-2.5 py-1.5 text-xs text-muted-foreground capitalize">
          {experiment.design_type}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="design-replicates">Replicates</Label>
          <Input
            id="design-replicates"
            type="number"
            min="1"
            value={replicates}
            onChange={(e) => setReplicates(Math.max(1, Number(e.target.value) || 1))}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="design-seed">Randomization seed</Label>
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
        <Label>Metrics</Label>
        <MetricsEditor metrics={metrics} onChange={setMetrics} />
      </div>

      <div className="space-y-1.5 rounded-md border bg-muted/30 px-3 py-2">
        <p className="font-mono text-xs text-muted-foreground">
          {validFactors.length} factor{validFactors.length === 1 ? '' : 's'} → {combinations} condition
          {combinations === 1 ? '' : 's'} × {Math.max(replicates, 1)} replicate{replicates === 1 ? '' : 's'} = {totalTrials} total
          trial{totalTrials === 1 ? '' : 's'}
        </p>
        <Button size="sm" variant="outline" disabled={generateMutation.isPending || isDirty || validFactors.length === 0} onClick={() => generateMutation.mutate()}>
          {generateMutation.isPending ? 'Generating…' : 'Generate design'}
        </Button>
        {isDirty && <p className="text-xs text-muted-foreground">Save your changes before generating.</p>}
        {generateMutation.data && <p className="text-xs text-muted-foreground">{generateMutation.data.length} cell(s) total</p>}
      </div>

      <Button disabled={!isDirty || saveMutation.isPending} onClick={() => saveMutation.mutate()}>
        {saveMutation.isPending ? 'Saving…' : 'Save design'}
      </Button>
    </div>
  )
}
