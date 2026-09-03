import { useEffect, useRef, useState, type RefObject } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import { Info, Pencil, Plus, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { experimentsApi, llmSettingsApi, protocolsApi } from '@/api/client'
import { unboundFactorNames } from '@/lib/factorBindings'
import { protocolGraphQueryKey } from '@/lib/protocolGraph'
import { revealsHiddenMcpServers, toolFactorServerId, unboundBindableFields, type UnboundField } from './bindableFields'
import { LEVEL_TYPE_LABELS, levelTypeOf } from './factorLevels'
import { FactorEditorDialog } from './FactorEditorDialog'
import { InfoTooltip } from './InfoTooltip'
import { ModelField } from './ModelField'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useProviderModels } from './useProviderModels'
import {
  METRIC_CATALOG,
  makeCatalogMetric,
  makeCustomMetric,
  metricCatalogEntry,
  normalizeDesignMetrics,
  type MetricDirection,
  type MetricValueType,
} from '@/lib/metricCatalog'
import type { ProtocolCanvasHandle } from './ProtocolCanvas'
import type { ProtocolGraph } from '@/types/protocols'
import {
  COORDINATION_STRATEGY_CATALOG,
  type CoordinationStrategySlug,
  type DesignFactor,
  type DesignMetric,
  type Experiment,
  type MetricScoringConfig,
} from '@/types/experiments'
import { LLM_PROVIDER_CATALOG, LLM_PROVIDER_LABELS, type LLMProvider } from '@/types/llmSettings'

const AUTOSAVE_DELAY_MS = 800

type MetadataDraft = {
  hypothesis: string
  randomizationSeed: number | null
  metrics: DesignMetric[]
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
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => setDialogOpen(true)}>
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
          onSave={(next) => editMutation.mutate({ oldName: editingFactor.draft.name, next })}
        />
      )}
    </div>
  )
}

function withPrimary(metrics: DesignMetric[]): DesignMetric[] {
  if (!metrics.length) return []
  const selected = metrics.findIndex((metric) => metric.primary)
  return metrics.map((metric, index) => ({ ...metric, primary: index === (selected < 0 ? 0 : selected) }))
}

function MetricInfo({ metric }: { metric: DesignMetric }) {
  const catalog = metricCatalogEntry(metric.catalogKey)
  return (
    <TooltipProvider delay={200}>
      <Tooltip>
        <TooltipTrigger render={<button type="button" className="shrink-0 text-muted-foreground hover:text-foreground" aria-label={`About ${metric.name}`} />}>
          <Info className="size-3.5" />
        </TooltipTrigger>
        <TooltipContent className="max-w-72 flex-col items-start gap-1 text-left">
          <span>{metric.description || catalog?.shortDescription || 'Metric definition unavailable.'}</span>
          <span className="text-background/70">{catalog ? `${catalog.kind.replace(/_/g, ' ')} metric` : `${metric.kind ?? 'custom'} metric`}</span>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

function CustomMetricDialog({
  metric,
  existingNames,
  onCancel,
  onSave,
}: {
  metric?: DesignMetric
  existingNames: string[]
  onCancel: () => void
  onSave: (metric: { name: string; description: string; direction: MetricDirection; valueType: MetricValueType; unit?: string; scoring?: MetricScoringConfig }) => void
}) {
  const [name, setName] = useState(metric?.name ?? '')
  const [description, setDescription] = useState(metric?.description ?? '')
  const [direction, setDirection] = useState<MetricDirection>(metric?.direction ?? 'maximize')
  const [valueType, setValueType] = useState<MetricValueType>(metric?.valueType ?? 'number')
  const [unit, setUnit] = useState(metric?.unit ?? '')
  const [judgeEnabled, setJudgeEnabled] = useState(metric?.scoring?.method === 'model_judge')
  const [rubric, setRubric] = useState(metric?.scoring?.rubric ?? '')
  const [reference, setReference] = useState(metric?.scoring?.reference ?? '')
  const [minimum, setMinimum] = useState(metric?.scoring?.min?.toString() ?? '0')
  const [maximum, setMaximum] = useState(metric?.scoring?.max?.toString() ?? '100')
  const [judgeProvider, setJudgeProvider] = useState<LLMProvider | ''>(metric?.scoring?.judge?.provider ?? '')
  const [judgeModel, setJudgeModel] = useState(metric?.scoring?.judge?.model ?? '')
  const credentialsQuery = useQuery({ queryKey: ['llm-settings'], queryFn: () => llmSettingsApi.list(), staleTime: 10 * 60 * 1000 })
  const { modelsQuery, models } = useProviderModels(judgeProvider || undefined)
  const registeredProviders = (credentialsQuery.data ?? []).map((credential) => credential.provider)
  const normalizedName = name.trim().toLocaleLowerCase()
  const duplicate = !!normalizedName && existingNames.some((existing) => existing.trim().toLocaleLowerCase() === normalizedName && existing !== metric?.name)
  const parsedMinimum = minimum.trim() === '' ? undefined : Number(minimum)
  const parsedMaximum = maximum.trim() === '' ? undefined : Number(maximum)
  const invalidRange = judgeEnabled && (
    !rubric.trim()
    || (parsedMinimum !== undefined && !Number.isFinite(parsedMinimum))
    || (parsedMaximum !== undefined && !Number.isFinite(parsedMaximum))
    || (parsedMinimum !== undefined && parsedMaximum !== undefined && parsedMinimum > parsedMaximum)
  )
  const unavailableJudgeCredential = !!judgeProvider && !credentialsQuery.isLoading && !registeredProviders.includes(judgeProvider)
  const invalidJudge = judgeEnabled && (!judgeProvider || !judgeModel.trim() || unavailableJudgeCredential)
  const invalid = !name.trim() || !description.trim() || duplicate || invalidRange || invalidJudge
  return (
    <Dialog open onOpenChange={(open) => !open && onCancel()}>
      <DialogContent showCloseButton={false} className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{metric ? 'Edit custom metric' : 'Custom metric'}</DialogTitle>
          <DialogDescription>Custom metrics are reporting targets by default. Enable an LLM judge below to score each completed output automatically.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5"><Label htmlFor="custom-metric-name">Name</Label><Input id="custom-metric-name" autoFocus value={name} onChange={(event) => setName(event.target.value)} /></div>
          <div className="space-y-1.5"><Label htmlFor="custom-metric-description">Definition</Label><Textarea id="custom-metric-description" rows={3} value={description} onChange={(event) => setDescription(event.target.value)} /></div>
          {duplicate && <p className="text-xs text-destructive">A custom metric with this name already exists.</p>}
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1.5"><Label>Direction</Label><Select value={direction} onValueChange={(value) => value && setDirection(value as MetricDirection)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="maximize">Maximize</SelectItem><SelectItem value="minimize">Minimize</SelectItem></SelectContent></Select></div>
            <div className="space-y-1.5"><Label>Value type</Label><Select value={valueType} onValueChange={(value) => value && setValueType(value as MetricValueType)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="number">Number</SelectItem><SelectItem value="boolean">Boolean</SelectItem><SelectItem value="string">Text</SelectItem></SelectContent></Select></div>
            <div className="space-y-1.5"><Label htmlFor="custom-metric-unit">Unit</Label><Input id="custom-metric-unit" value={unit} onChange={(event) => setUnit(event.target.value)} placeholder="Optional" /></div>
          </div>
          <div className="rounded-md border bg-muted/20 p-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <Label htmlFor="custom-metric-judge" className="text-sm">Evaluate automatically with an LLM judge</Label>
                <p className="mt-1 text-xs text-muted-foreground">Runs after the task completes against the final output. It never asks the task Agent to score itself.</p>
              </div>
              <Checkbox id="custom-metric-judge" checked={judgeEnabled} onCheckedChange={(checked) => setJudgeEnabled(checked === true)} />
            </div>
            {judgeEnabled && <div className="mt-3 space-y-3 border-t pt-3">
              <div className="space-y-1.5"><Label htmlFor="custom-metric-rubric">Judge rubric</Label><Textarea id="custom-metric-rubric" rows={4} value={rubric} onChange={(event) => setRubric(event.target.value)} placeholder="Explain exactly how the score should be assigned." /></div>
              <div className="space-y-1.5"><Label htmlFor="custom-metric-reference">Reference material — Optional</Label><Textarea id="custom-metric-reference" rows={3} value={reference} onChange={(event) => setReference(event.target.value)} placeholder="Paste a reference answer, relevant source excerpts, or acceptance criteria." /><p className="text-xs text-muted-foreground">The judge only receives this text and the final output; attached knowledge tools are not exposed to it.</p></div>
              <div className="grid grid-cols-2 gap-3 border-t pt-3">
                <div className="space-y-1.5">
                  <Label>Judge provider</Label>
                  <Select value={judgeProvider || '__select__'} onValueChange={(provider) => {
                    if (!provider || provider === '__select__') return
                    setJudgeProvider(provider as LLMProvider)
                    setJudgeModel('')
                  }}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__select__" disabled>Select a saved credential…</SelectItem>
                      {LLM_PROVIDER_CATALOG.filter((provider) => registeredProviders.includes(provider.id)).map((provider) => (
                        <SelectItem key={provider.id} value={provider.id}>{provider.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="custom-metric-judge-model">Judge model</Label>
                  {judgeProvider
                    ? <ModelField id="custom-metric-judge-model" value={judgeModel} models={models} isLoading={modelsQuery.isLoading} onChange={setJudgeModel} />
                    : <Input disabled placeholder="Select a provider first" />}
                </div>
              </div>
              {credentialsQuery.isLoading ? <p className="text-xs text-muted-foreground">Loading saved credentials…</p>
                : registeredProviders.length === 0 ? <p className="text-xs text-destructive">Add a provider credential before enabling an LLM judge.</p>
                  : judgeProvider && !registeredProviders.includes(judgeProvider) ? <p className="text-xs text-destructive">The saved {LLM_PROVIDER_LABELS[judgeProvider as LLMProvider]} credential is no longer available. Select another provider.</p>
                    : <p className="text-xs text-muted-foreground">This provider and model are saved with the metric, independent of the task Agent’s model.</p>}
              <div className="grid grid-cols-2 gap-3"><div className="space-y-1.5"><Label htmlFor="custom-metric-minimum">Minimum score</Label><Input id="custom-metric-minimum" type="number" value={minimum} onChange={(event) => setMinimum(event.target.value)} /></div><div className="space-y-1.5"><Label htmlFor="custom-metric-maximum">Maximum score</Label><Input id="custom-metric-maximum" type="number" value={maximum} onChange={(event) => setMaximum(event.target.value)} /></div></div>
              {(invalidRange || invalidJudge) && <p className="text-xs text-destructive">Provide a rubric, a saved provider and model, and valid numeric bounds (minimum cannot exceed maximum).</p>}
            </div>}
          </div>
        </div>
        <DialogFooter><Button variant="outline" onClick={onCancel}>Cancel</Button><Button disabled={invalid} onClick={() => onSave({ name, description, direction, valueType: judgeEnabled ? 'number' : valueType, unit, scoring: judgeEnabled ? { method: 'model_judge', rubric, reference, min: parsedMinimum, max: parsedMaximum, judge: { provider: judgeProvider as LLMProvider, model: judgeModel.trim() } } : undefined })}>{metric ? 'Save metric' : 'Add metric'}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function MetricsEditor({ metrics, onChange, disabled = false }: { metrics: DesignMetric[]; onChange: (metrics: DesignMetric[]) => void; disabled?: boolean }) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [customEditing, setCustomEditing] = useState<DesignMetric | undefined>()
  const pickerRef = useRef<HTMLInputElement>(null)
  const normalized = normalizeDesignMetrics(metrics)
  const selectedCatalogKeys = new Set(normalized.map((metric) => metric.catalogKey).filter(Boolean))
  const available = METRIC_CATALOG.filter((entry) => !selectedCatalogKeys.has(entry.key)).filter((entry) => `${entry.name} ${entry.shortDescription}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()))

  useEffect(() => { if (pickerOpen) pickerRef.current?.focus() }, [pickerOpen])

  function update(next: DesignMetric[]) { onChange(withPrimary(next)) }
  function remove(index: number) { update(normalized.filter((_, itemIndex) => itemIndex !== index)) }
  function dismissPicker() { setPickerOpen(false); setQuery('') }

  return (
    <div className="space-y-2">
      {normalized.length === 0 && !pickerOpen && <p className="rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground">No metrics selected yet.</p>}
      {normalized.map((metric, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <div className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md border bg-muted/20 px-2.5 py-2 text-sm"><span className="truncate">{metric.name}</span><MetricInfo metric={metric} /></div>
          {(metric.kind === 'custom' || metric.kind === 'model_judge') && <Button variant="ghost" size="icon-sm" aria-label={`Edit ${metric.name}`} disabled={disabled} onClick={() => setCustomEditing(metric)}><Pencil className="size-3.5" /></Button>}
          <label className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground" title="Primary metric">
            <input
              type="radio"
              name="primary-metric"
              checked={metric.primary}
              disabled={disabled}
              onChange={() => update(normalized.map((m, j) => ({ ...m, primary: j === i })))}
            />
            Primary
          </label>
          <Select
            value={metric.direction}
            disabled={disabled}
            onValueChange={(value) => {
              if (value) update(normalized.map((m, j) => (j === i ? { ...m, direction: value as DesignMetric['direction'] } : m)))
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
          <Button variant="ghost" size="icon-sm" aria-label="Remove metric" disabled={disabled} onClick={() => remove(i)}>
            <X className="size-3.5" />
          </Button>
        </div>
      ))}
      {pickerOpen && <div className="rounded-md border bg-muted/20 p-2" onKeyDown={(event) => { if (event.key === 'Escape') dismissPicker() }}>
        <div className="flex gap-2"><Input ref={pickerRef} aria-label="Search metrics" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search metrics…" /><Button variant="ghost" size="sm" onClick={dismissPicker}>Cancel</Button></div>
        <div className="mt-2 max-h-48 space-y-1 overflow-y-auto" role="listbox" aria-label="Available metrics">
          {available.map((entry) => <button key={entry.key} type="button" role="option" className="w-full rounded px-2 py-1.5 text-left hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => { update([...normalized, makeCatalogMetric(entry, normalized.length === 0)]); dismissPicker() }}><span className="text-sm font-medium">{entry.name}</span><span className="ml-2 text-xs text-muted-foreground">Runtime · {entry.shortDescription}</span></button>)}
          {available.length === 0 && <p className="px-2 py-1 text-xs text-muted-foreground">No catalog metrics match.</p>}
          <button type="button" role="option" className="w-full rounded border-t px-2 py-1.5 text-left text-sm font-medium hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => { dismissPicker(); setCustomEditing({ name: '', description: '', direction: 'maximize', primary: false, kind: 'custom' }) }}>Custom metric…</button>
        </div>
      </div>}
      <Button variant="outline" size="sm" disabled={disabled || pickerOpen} onClick={() => setPickerOpen(true)}>
        <Plus className="size-3.5" /> Add metric
      </Button>
      {customEditing && <CustomMetricDialog metric={customEditing.name ? customEditing : undefined} existingNames={normalized.map((metric) => metric.name)} onCancel={() => setCustomEditing(undefined)} onSave={(input) => {
        if (customEditing.name) update(normalized.map((metric) => metric.id === customEditing.id ? { ...metric, ...input, kind: input.scoring ? 'model_judge' as const : 'custom' as const } : metric))
        else update([...normalized, makeCustomMetric(input, normalized.length === 0)])
        setCustomEditing(undefined)
      }} />}
    </div>
  )
}

// The experiment's full design declaration -- consolidates what used to be
// scattered across the canvas toolbar's DesignPreview and had no UI at all
// (hypothesis, replicates, randomization seed, metrics, coordination
// strategy). Edited as one local draft. Changes that alter cells are persisted
// and regenerated by one explicit Apply changes to cells action; metadata-only changes
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
  const [coordinationSlug, setCoordinationSlug] = useState<CoordinationStrategySlug>(
    experiment.design_spec?.coordination_strategy?.slug ?? 'sequential',
  )
  const metadataDraft: MetadataDraft = { hypothesis, randomizationSeed, metrics, coordinationSlug }
  const metadataDraftKey = JSON.stringify(metadataDraft)
  const matrixDraftKey = JSON.stringify({ factors, replicates })
  const latestMetadataDraftKey = useRef(metadataDraftKey)
  const latestMatrixDraftKey = useRef(matrixDraftKey)
  const pendingMetadataSaveRef = useRef<MetadataSave | null>(null)
  const lastScheduledMetadataSaveKeyRef = useRef<string | null>(null)
  latestMetadataDraftKey.current = metadataDraftKey
  latestMatrixDraftKey.current = matrixDraftKey

  // Re-seed local draft when a different experiment loads (or after a
  // successful save round-trips fresh server data back down).
  useEffect(() => {
    setHypothesis(experiment.hypothesis ?? '')
    setFactors(experiment.design_spec?.factors ?? [])
    setReplicates(experiment.design_spec?.replicates ?? 1)
    setRandomizationSeed(experiment.design_spec?.randomization_seed ?? null)
    setMetrics(normalizeDesignMetrics(experiment.design_spec?.metrics))
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
    mutationFn: async ({ draft }: MetadataSave) => {
      const fresh = await experimentsApi.get(experiment.id)
      return experimentsApi.update(experiment.id, {
        hypothesis: draft.hypothesis.trim() || null,
        design_spec: {
          ...fresh.design_spec,
          randomization_seed: draft.randomizationSeed,
          metrics: draft.metrics.filter((metric) => metric.name.trim() !== ''),
          coordination_strategy: { slug: draft.coordinationSlug, params: fresh.design_spec?.coordination_strategy?.params ?? {} },
        },
      })
    },
    onSuccess: (updated, saved) => {
      // An older response must not reset a newer local edit (including a
      // pending factors/replicates update). The next quiet pause will persist
      // that newer metadata snapshot instead.
      if (latestMetadataDraftKey.current !== saved.metadataKey || latestMatrixDraftKey.current !== saved.matrixKey) return
      queryClient.setQueryData(['experiments', experiment.id], updated)
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
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
  const metadataDraftChanged =
    hypothesis !== (experiment.hypothesis ?? '') ||
    randomizationSeed !== (experiment.design_spec?.randomization_seed ?? null) ||
    JSON.stringify(metrics) !== JSON.stringify(experiment.design_spec?.metrics ?? []) ||
    coordinationSlug !== (experiment.design_spec?.coordination_strategy?.slug ?? 'sequential')
  const canGenerate = validFactors.length > 0 || impact?.regeneration_required === true
  const needsDesignUpdate = matrixDraftChanged || impact?.regeneration_required === true
  const metadataSaveKey = `${metadataDraftKey}:${matrixDraftKey}`
  const isLocked = !!experiment.locked_at

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
            (e.g. "Critic Gate" requires a real Critic Gate node wired in) or running the protocol is rejected.
          </InfoTooltip>
        </Label>
        <Select value={coordinationSlug} disabled={isLocked} onValueChange={(value) => value && setCoordinationSlug(value as CoordinationStrategySlug)}>
          <SelectTrigger className="w-full" disabled={isLocked}>
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
            What this experiment records for each replicate. Primary marks the metric used for significance analysis;
            Maximize/Minimize says which direction is better.
          </InfoTooltip>
        </Label>
        <MetricsEditor metrics={metrics} onChange={setMetrics} disabled={isLocked} />
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
