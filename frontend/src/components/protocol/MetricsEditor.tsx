import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Gauge, Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { experimentsApi } from '@/api/client'
import { contextualMetricSuggestions } from '@/lib/contextualMetricSuggestions'
import { AGENT_OUTPUT_PRODUCER_ID, agentOutputBindingForMetric, agentOutputSourceOptions } from '@/lib/agentOutputMetrics'
import { applyCustomMetricChange, type CustomMetricProducerConfig } from '@/lib/customMetrics'
import { pythonScriptBindingForMetric, pythonScriptSourceOptions } from '@/lib/pythonScriptMetrics'
import { mcpToolBindingForMetric, mcpToolSourceOptions } from '@/lib/mcpToolMetrics'
import { localMetricReadinessPreview, removeMetricFromMeasurementPlan, upsertRuntimeMetric } from '@/lib/measurementPlan'
import { METRIC_CATALOG, makeCatalogMetric, makeCustomMetric, normalizeDesignMetrics, type MetricCatalogEntry } from '@/lib/metricCatalog'
import type { ProtocolGraph } from '@/types/protocols'
import type { DesignMetric, MeasurementPlan } from '@/types/experiments'
import { CustomMetricFlow, MetricNodeLabel, type MetricNodeDisplay } from './CustomMetricDialogs'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { useDialogAutosave } from './useDialogAutosave'

function withoutRanking(metrics: DesignMetric[]): DesignMetric[] {
  return metrics.map((metric) => ({ ...metric, direction: 'neutral', primary: false }))
}

function synchronizeMeasurementPlanMetrics(plan: MeasurementPlan | null, metrics: DesignMetric[]) {
  if (!plan) return null
  const definitionsById = new Map(plan.metrics.map((definition) => [definition.id, definition]))
  const orderedDefinitions = metrics.flatMap((metric) => {
    const definition = metric.id ? definitionsById.get(metric.id) : undefined
    if (!definition) return []
    definitionsById.delete(definition.id)
    return [{
      ...definition,
      name: metric.name,
      direction: metric.direction,
      primary: metric.primary,
      description: metric.description,
      unit: metric.unit,
    }]
  })
  return { ...plan, metrics: [...orderedDefinitions, ...definitionsById.values()] }
}

type CustomMetricChange = {
  metric: DesignMetric
  config: CustomMetricProducerConfig
}

function customMetricProducerDisplay(
  binding: MeasurementPlan['producers'][number] | undefined,
  graph: ProtocolGraph | undefined,
): MetricNodeDisplay | null {
  if (!binding) return null
  const nodeLabel = (nodeId: unknown) => typeof nodeId === 'string'
    ? graph?.nodes.find((node) => node.id === nodeId)?.data.label || nodeId
    : 'Unknown node'
  const agent = nodeLabel(binding.config.agent_node_id)
  if (binding.producer_id === AGENT_OUTPUT_PRODUCER_ID) return { label: agent, type: 'Agent' }
  if (binding.producer_id === 'asaree.python_script') {
    return { label: `${agent}:${nodeLabel(binding.config.script_node_id)}`, type: 'Script' }
  }
  if (binding.producer_id === 'asaree.mcp_tool') {
    const toolName = typeof binding.config.tool_name === 'string' ? binding.config.tool_name : null
    return {
      label: `${agent}:${nodeLabel(binding.config.mcp_node_id)}${toolName ? ` · ${toolName}` : ''}`,
      type: 'MCP Tool',
    }
  }
  return null
}

function MetricsDialog({
  open,
  onOpenChange,
  catalogBuiltIns,
  supportedBuiltInKeys,
  selectedKeys,
  initialDraftKeys,
  capabilitiesLoading,
  capabilitiesUnavailable,
  disabled,
  onApply,
  metrics,
  measurementPlan,
  graph,
  metricIssueById,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  catalogBuiltIns: MetricCatalogEntry[]
  supportedBuiltInKeys: Set<string>
  selectedKeys: string[]
  initialDraftKeys?: string[]
  capabilitiesLoading: boolean
  capabilitiesUnavailable: boolean
  disabled: boolean
  onApply: (selected: Set<string>, customChanges: CustomMetricChange[], customMetricIds: string[]) => Promise<void>
  metrics: DesignMetric[]
  measurementPlan: MeasurementPlan | null
  graph: ProtocolGraph | undefined
  metricIssueById: Map<string, string>
}) {
  const selectedKeySignature = selectedKeys.join('\u0000')
  const selectedKeySet = useMemo(
    () => new Set(selectedKeySignature ? selectedKeySignature.split('\u0000') : []),
    [selectedKeySignature],
  )
  const initialDraftSignature = initialDraftKeys?.join('\u0000')
  const initialDraftKeySet = useMemo(
    () => initialDraftSignature === undefined
      ? selectedKeySet
      : new Set(initialDraftSignature ? initialDraftSignature.split('\u0000') : []),
    [initialDraftSignature, selectedKeySet],
  )
  const [draftKeys, setDraftKeys] = useState(selectedKeySet)
  const [saveError, setSaveError] = useState<string>()
  const [customChanges, setCustomChanges] = useState<CustomMetricChange[]>([])
  const initialCustomMetricIds = useMemo(
    () => metrics.filter((metric) => metric.kind === 'custom').flatMap((metric) => metric.id ? [metric.id] : []),
    [metrics],
  )
  const initialCustomMetricSignature = initialCustomMetricIds.join('\u0000')
  const [customMetricIds, setCustomMetricIds] = useState(initialCustomMetricIds)
  const [stagedOrderAnnouncement, setStagedOrderAnnouncement] = useState('')
  const [customMetricDraft, setCustomMetricDraft] = useState<DesignMetric>()
  const [customMetricDirty, setCustomMetricDirty] = useState(false)
  const [customMetricPendingDelete, setCustomMetricPendingDelete] = useState<DesignMetric>()
  const wasOpenRef = useRef(false)
  const previousUnavailableBuiltInKeysRef = useRef<Set<string> | null>(null)
  const newlyUnavailableBuiltInKeysRef = useRef(new Set<string>())
  const draftSignature = [...draftKeys].sort().join('\u0000')
  const savedSignature = [...selectedKeySet].sort().join('\u0000')
  const dirty = draftSignature !== savedSignature || customChanges.length > 0 || customMetricDirty || customMetricIds.join('\u0000') !== initialCustomMetricSignature
  const unresolvedAddedKeys = [...draftKeys].filter((key) => !selectedKeySet.has(key) && !supportedBuiltInKeys.has(key))
  const applyDependsOnUnresolvedCapabilities = unresolvedAddedKeys.length > 0
  // Keep catalog cards in one predictable order regardless of saved or staged
  // selection. Moving a checked card changes what sits under the pointer and
  // makes it look as though the metric itself changed.
  const orderEntries = (entries: MetricCatalogEntry[]) => [...entries].sort((left, right) => left.name.localeCompare(right.name))
  const builtInEntries = orderEntries(catalogBuiltIns)
  const hasPythonMetricCandidate = pythonScriptSourceOptions(graph).some((source) => !source.disabledReason)
  const hasMcpMetricCandidate = mcpToolSourceOptions(graph).some((source) => !source.disabledReason)
  const hasAgentMetricCandidate = agentOutputSourceOptions(graph).some((source) => !source.disabledReason)
  const canvasMetricKeys = new Set(contextualMetricSuggestions(graph).map((suggestion) => suggestion.key))
  const hasValidTool = canvasMetricKeys.has('tool_error_rate')
  const hasCriticGate = graph?.nodes.some((node) => node.type === 'critic_gate') ?? false
  const canvasCannotProduce = (key: string) =>
    ((key === 'tool_calls' || key === 'tool_error_rate') && !hasValidTool)
    || ((key === 'critic_approvals' || key === 'critic_rejections') && !hasCriticGate)
  const unavailableBuiltInKeys = capabilitiesLoading || capabilitiesUnavailable
    ? []
    : builtInEntries.flatMap((entry) => {
      const unavailable = !supportedBuiltInKeys.has(entry.key) || canvasCannotProduce(entry.key)
      return unavailable ? [entry.key] : []
    })
  const unavailableBuiltInKeySignature = unavailableBuiltInKeys.join('\u0000')
  const canCreateCustomMetric = hasAgentMetricCandidate || hasPythonMetricCandidate || hasMcpMetricCandidate
  const changedMetrics = metrics.map((metric) => customChanges.find((change) => change.metric.id === metric.id)?.metric ?? metric)
    .concat(customChanges.filter((change) => !metrics.some((metric) => metric.id === change.metric.id)).map((change) => change.metric))
  const customMetricById = new Map(changedMetrics.filter((metric) => metric.kind === 'custom' && metric.id).map((metric) => [metric.id!, metric]))
  const customMetrics = customMetricIds.flatMap((id) => customMetricById.get(id) ?? [])
  const stagedMetrics = [...changedMetrics.filter((metric) => metric.kind !== 'custom'), ...customMetrics]
  let stagedPlan = measurementPlan
  for (const metric of metrics.filter((item) => item.kind === 'custom' && item.id && !customMetricIds.includes(item.id))) {
    stagedPlan = removeMetricFromMeasurementPlan(stagedPlan, metric.id)
  }
  for (const change of customChanges.filter((item) => item.metric.id && customMetricIds.includes(item.metric.id))) {
    stagedPlan = applyCustomMetricChange(stagedPlan, change.metric, change.config, graph)
  }
  stagedPlan = synchronizeMeasurementPlanMetrics(stagedPlan, stagedMetrics)
  function stagedBindingForMetric(metricId: string | undefined) {
    return agentOutputBindingForMetric(stagedPlan, metricId)
      ?? pythonScriptBindingForMetric(stagedPlan, metricId)
      ?? mcpToolBindingForMetric(stagedPlan, metricId)
  }
  function stagedReadinessFor(metric: DesignMetric) {
    const local = localMetricReadinessPreview(metric, stagedPlan, graph)
    const serverIssue = metric.id ? metricIssueById.get(metric.id) : undefined
    return serverIssue ? { ...local, ready: false, detail: serverIssue } : local
  }
  const visibleCustomMetrics = customMetrics
  const autosaveSignature = JSON.stringify({
    builtIns: [...draftKeys].sort(),
    customChanges,
    customMetricIds,
  })
  const autosaveDraft = dirty && !customMetricDraft && !customMetricDirty && !applyDependsOnUnresolvedCapabilities
    ? { selectedKeys: [...draftKeys], customChanges, customMetricIds }
    : null
  const metricAutosave = useDialogAutosave({
    draft: autosaveDraft,
    signature: autosaveSignature,
    onSave: async (draft) => {
      setSaveError(undefined)
      try {
        await onApply(new Set(draft.selectedKeys), draft.customChanges, draft.customMetricIds)
      } catch (error) {
        setSaveError(error instanceof Error ? error.message : 'Could not save metric changes.')
        throw error
      }
    },
  })

  useEffect(() => {
    if (!open) {
      wasOpenRef.current = false
      return
    }
    if (wasOpenRef.current) return
    wasOpenRef.current = true
    // A default selection (nothing saved yet) must not pre-check a metric this
    // canvas can't produce -- it would save a metric that can never report.
    // A saved selection is shown as-is, so it can still be unchecked.
    setDraftKeys(new Set(initialDraftSignature === undefined
      ? initialDraftKeySet
      : [...initialDraftKeySet].filter((key) => !canvasCannotProduce(key))))
    setSaveError(undefined)
    setCustomChanges([])
    setCustomMetricIds(initialCustomMetricIds)
    setStagedOrderAnnouncement('')
    setCustomMetricDirty(false)
    setCustomMetricPendingDelete(undefined)
    setCustomMetricDraft(undefined)
    // Seeds once per open (wasOpenRef); canvas availability changing while the
    // dialog is open is handled by the newly-unavailable effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialDraftKeySet, metrics, initialCustomMetricIds, initialCustomMetricSignature])

  useEffect(() => {
    if (capabilitiesLoading || capabilitiesUnavailable) return
    const unavailable = new Set(unavailableBuiltInKeySignature ? unavailableBuiltInKeySignature.split('\u0000') : [])
    const previous = previousUnavailableBuiltInKeysRef.current
    if (previous) {
      for (const key of unavailable) {
        if (!previous.has(key)) newlyUnavailableBuiltInKeysRef.current.add(key)
      }
      for (const key of previous) {
        if (!unavailable.has(key)) newlyUnavailableBuiltInKeysRef.current.delete(key)
      }
    }
    previousUnavailableBuiltInKeysRef.current = unavailable
    if (!open || newlyUnavailableBuiltInKeysRef.current.size === 0) return
    const newlyUnavailable = new Set(newlyUnavailableBuiltInKeysRef.current)
    newlyUnavailableBuiltInKeysRef.current.clear()
    setDraftKeys((current) => {
      const next = new Set([...current].filter((key) => !newlyUnavailable.has(key)))
      return next.size === current.size ? current : next
    })
  }, [open, capabilitiesLoading, capabilitiesUnavailable, unavailableBuiltInKeySignature])

  function toggle(key: string, checked: boolean) {
    const next = new Set(draftKeys)
    if (checked) next.add(key)
    else next.delete(key)
    setDraftKeys(next)
  }
  function moveStagedCustomMetric(metric: DesignMetric, offset: -1 | 1) {
    if (!metric.id) return
    setCustomMetricIds((current) => {
      const position = current.indexOf(metric.id!)
      const target = position + offset
      if (position < 0 || target < 0 || target >= current.length) return current
      const next = [...current]
      ;[next[position], next[target]] = [next[target], next[position]]
      setStagedOrderAnnouncement(`${metric.name} moved to position ${target + 1} of ${next.length}.`)
      return next
    })
  }
  function requestClose() {
    metricAutosave.flush()
    onOpenChange(false)
  }
  function deleteCustomMetric(metric: DesignMetric) {
    if (!metric.id) return
    setCustomMetricIds((current) => current.filter((id) => id !== metric.id))
    setCustomChanges((current) => current.filter((change) => change.metric.id !== metric.id))
    if (customMetricDraft?.id === metric.id) {
      setCustomMetricDraft(undefined)
      setCustomMetricDirty(false)
    }
    setCustomMetricPendingDelete(undefined)
  }
  return (
    <>
    <NodeInspectorDialog
      open={open}
      onOpenChange={(nextOpen) => { if (!nextOpen) requestClose(); else onOpenChange(true) }}
      accent="var(--primary)"
      title={
        <>
          <Gauge className="size-5 text-primary" />
          <div className="min-w-0">
            <DialogTitle>Manage metrics</DialogTitle>
            <DialogDescription className="mt-1">Select built-in metrics and create custom metrics from eligible canvas nodes. Changes save automatically.</DialogDescription>
          </div>
        </>
      }
      onClose={requestClose}
      bodyClassName="grid min-h-0 grid-cols-1 space-y-0 overflow-y-auto p-0 md:grid-cols-2 md:overflow-hidden"
    >
          <section className="space-y-3 border-b p-4 md:overflow-y-auto md:border-r md:border-b-0" aria-labelledby="metric-section-built-in">
            <div>
              <h3 id="metric-section-built-in" className="text-sm font-semibold">Built-in metrics</h3>
              <p className="mt-1 text-xs text-muted-foreground">ASAREE captures these automatically during runs.</p>
            </div>
            <div className="rounded-md border px-3 py-2 text-xs" role="status" aria-live="polite">
              {capabilitiesLoading && <span className="text-muted-foreground">Checking built-in metric availability…</span>}
              {capabilitiesUnavailable && <span className="text-[color:var(--chart-4)]">Built-in metric availability could not be loaded.</span>}
              {!capabilitiesLoading && !capabilitiesUnavailable && <span className="text-[color:var(--chart-3)]">Built-in metrics loaded.</span>}
            </div>
            <div className="space-y-2">
                  {builtInEntries.map((entry) => {
                    const selected = draftKeys.has(entry.key)
                    const supported = supportedBuiltInKeys.has(entry.key)
                    const unavailableReason = capabilitiesLoading
                      ? 'Checking runtime support…'
                      : capabilitiesUnavailable
                        ? 'Runtime capabilities could not be loaded.'
                        : !supported
                          ? 'Not supported by this runtime.'
                          : (entry.key === 'tool_calls' || entry.key === 'tool_error_rate') && !hasValidTool
                            ? 'Connect an enabled, configured tool to an active Agent to use this metric.'
                            : (entry.key === 'critic_approvals' || entry.key === 'critic_rejections') && !hasCriticGate
                              ? 'Add a Critic Gate to the canvas to use this metric.'
                          : undefined
                    const unavailableReasonId = `metric-unavailable-${entry.key}`
                    return <div key={entry.key} className={`rounded-md border p-2.5 ${unavailableReason ? 'opacity-70' : 'hover:bg-muted/40'}`}>
                      <label className={unavailableReason ? 'flex cursor-not-allowed items-start gap-3' : 'flex cursor-pointer items-start gap-3'}>
                      <Checkbox aria-label={entry.name} aria-describedby={unavailableReason ? unavailableReasonId : undefined} checked={selected} disabled={disabled || (Boolean(unavailableReason) && !selected)} onCheckedChange={(checked) => toggle(entry.key, checked === true)} />
                      <span className="min-w-0 flex-1">
                        <span className="text-sm font-medium">{entry.name}</span>
                        <span className="mt-0.5 block text-xs text-muted-foreground">{entry.shortDescription}</span>
                        {unavailableReason && <span id={unavailableReasonId} className="mt-1 block text-xs text-[color:var(--chart-4)]">{unavailableReason}</span>}
                      </span>
                      </label>
                    </div>
                  })}
            </div>
          </section>
          <section className="min-w-0 space-y-3 p-4 md:overflow-y-auto" aria-labelledby="metric-section-custom">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <div><h3 id="metric-section-custom" className="text-sm font-semibold">Custom metrics</h3><p className="mt-1 text-xs text-muted-foreground">Create metrics from eligible nodes on this canvas.</p></div>
                  {!customMetricDraft && <Button type="button" size="sm" disabled={disabled || !canCreateCustomMetric} onClick={() => setCustomMetricDraft(makeCustomMetric({ name: '', description: '' }))}>Create custom metric</Button>}
                </div>
                {visibleCustomMetrics.length > 0 && <div className="space-y-2" role="list">{visibleCustomMetrics.map((metric) => {
                  const binding = stagedBindingForMetric(metric.id)
                  const readiness = stagedReadinessFor(metric)
                  const selectedPosition = metric.id ? customMetricIds.indexOf(metric.id) : -1
                  const producerDisplay = customMetricProducerDisplay(binding, graph)
                  return <div key={metric.id} role="listitem" aria-label={`${metric.name}, position ${selectedPosition + 1} of ${customMetricIds.length}`} className="flex flex-wrap items-start gap-2 rounded-md border p-2.5">
                    <span className="min-w-0 flex-1">
                      {producerDisplay
                        ? <MetricNodeLabel display={producerDisplay} />
                        : <span className="truncate text-sm font-medium">Custom metric</span>}
                      <span className="mt-0.5 block text-xs text-muted-foreground">{metric.name}</span>
                      {!readiness.ready && <span className="mt-1 block text-xs text-[color:var(--chart-4)]">{readiness.detail}</span>}
                    </span>
                    <span className="flex items-center gap-0.5">
                      <Button type="button" variant="ghost" size="icon-sm" aria-label={`Move ${metric.name} up in staged order`} disabled={disabled || selectedPosition === 0} onClick={() => moveStagedCustomMetric(metric, -1)}><ArrowUp className="size-3.5" /></Button>
                      <Button type="button" variant="ghost" size="icon-sm" aria-label={`Move ${metric.name} down in staged order`} disabled={disabled || selectedPosition === customMetricIds.length - 1} onClick={() => moveStagedCustomMetric(metric, 1)}><ArrowDown className="size-3.5" /></Button>
                    </span>
                    {binding && <Button type="button" variant="ghost" size="sm" aria-label={`Edit ${metric.name}`} disabled={disabled} onClick={() => setCustomMetricDraft(metric)}>Edit</Button>}
                    <Button type="button" variant="ghost" size="icon-sm" aria-label={`Delete ${metric.name}`} disabled={disabled} onClick={() => setCustomMetricPendingDelete(metric)}><Trash2 className="size-3.5 text-destructive" /></Button>
                  </div>
                })}</div>}
                <p role="status" aria-label="Staged metric order update" aria-live="polite" className="sr-only">{stagedOrderAnnouncement}</p>
                {visibleCustomMetrics.length === 0 && !customMetricDraft && <p className="rounded-md border border-dashed px-3 py-3 text-xs text-muted-foreground">No custom metrics yet.</p>}
                {!canCreateCustomMetric && !customMetricDraft && <p role="note" className="mt-2 rounded-md border border-dashed px-3 py-3 text-xs text-muted-foreground">Create custom metrics from an enabled Agent, Script, or configured MCP Tool.</p>}
                {customMetricDraft && <CustomMetricFlow
                  key={customMetricDraft.id}
                  metric={customMetricDraft}
                  binding={stagedBindingForMetric(customMetricDraft.id)}
                  graph={graph}
                  existingMetrics={stagedMetrics}
                  onCancel={() => { setCustomMetricDraft(undefined); setCustomMetricDirty(false) }}
                  onDirtyChange={setCustomMetricDirty}
                  onSave={(metric, config) => {
                    setCustomChanges((current) => [...current.filter((change) => change.metric.id !== metric.id), { metric, config }])
                    if (metric.id) setCustomMetricIds((current) => current.includes(metric.id!) ? current : [...current, metric.id!])
                    setCustomMetricDraft(undefined)
                    setCustomMetricDirty(false)
                  }}
                />}
            {saveError && <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive"><p className="font-medium">Could not save metric changes</p><p className="mt-1">{saveError} Make another change to retry.</p></div>}
          </section>
    </NodeInspectorDialog>
    <Dialog open={Boolean(customMetricPendingDelete)} onOpenChange={(nextOpen) => { if (!nextOpen) setCustomMetricPendingDelete(undefined) }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Delete {customMetricPendingDelete?.name}?</DialogTitle>
          <DialogDescription>This removes the custom metric and its reported source binding.</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => setCustomMetricPendingDelete(undefined)}>Cancel</Button>
          <Button variant="destructive" onClick={() => customMetricPendingDelete && deleteCustomMetric(customMetricPendingDelete)}>Delete</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    </>
  )
}

export function MetricsEditor({
  experimentId,
  metrics,
  measurementPlan,
  graph,
  onChange,
  onApplyMetrics,
  disabled = false,
}: {
  experimentId?: string
  metrics: DesignMetric[]
  measurementPlan: MeasurementPlan | null
  graph: ProtocolGraph | undefined
  onChange: (metrics: DesignMetric[], measurementPlan: MeasurementPlan | null) => void
  onApplyMetrics?: (metrics: DesignMetric[], measurementPlan: MeasurementPlan | null) => Promise<void>
  disabled?: boolean
}) {
  const [metricsDialogOpen, setMetricsDialogOpen] = useState(false)
  const normalized = useMemo(() => normalizeDesignMetrics(metrics), [metrics])
  const capabilitiesQuery = useQuery({
    queryKey: ['experiments', experimentId, 'measurement-capabilities'],
    queryFn: () => experimentsApi.getMeasurementCapabilities(experimentId!),
    enabled: !!experimentId,
    staleTime: 60_000,
  })
  const discoveredOutputs = capabilitiesQuery.data?.outputs
  const runtimeBuiltIns = normalized.filter((metric) => metric.kind === 'runtime' && metric.catalogKey)
  const savedBuiltInKeys = measurementPlan
    ? measurementPlan.producers
      .filter((producer) => producer.producer_id === 'asaree.runtime')
      .flatMap((producer) => Object.keys(producer.outputs))
    : []
  const catalogBuiltIns = METRIC_CATALOG.filter((entry) => entry.kind === 'runtime')
  const supportedBuiltInKeys = new Set(discoveredOutputs?.['asaree.runtime'] ?? [])
  const validationQuery = useQuery({
    queryKey: ['experiments', experimentId, 'measurement-plan-validation', measurementPlan, normalized, graph],
    queryFn: () => experimentsApi.validateMeasurementPlan(experimentId!, {
      measurement_plan: measurementPlan,
      metrics: normalized,
      graph: graph ?? { nodes: [], edges: [] },
    }),
    enabled: !!experimentId && normalized.length > 0,
    staleTime: 500,
  })
  const readinessIssues = (validationQuery.data?.issues ?? []).filter((issue) => issue.blocking === false)
  const blockingValidationIssues = (validationQuery.data?.issues ?? []).filter((issue) => issue.blocking !== false)
  const serverIssueByMetricId = new Map<string, string>()
  for (const issue of validationQuery.data?.issues ?? []) {
    const match = /^producers\[(\d+)\]/.exec(issue.path)
    const binding = match && measurementPlan?.producers[Number(match[1])]
    if (!binding) continue
    const outputMatch = /^producers\[\d+\]\.outputs\.([^.]+)$/.exec(issue.path)
    const affectedMetricIds = outputMatch && binding.outputs[outputMatch[1]]
      ? [binding.outputs[outputMatch[1]]]
      : Object.values(binding.outputs)
    for (const metricId of affectedMetricIds) {
      if (!serverIssueByMetricId.has(metricId)) serverIssueByMetricId.set(metricId, issue.message)
    }
  }
  function readinessFor(metric: DesignMetric) {
    const local = localMetricReadinessPreview(metric, measurementPlan, graph)
    const serverIssue = metric.id ? serverIssueByMetricId.get(metric.id) : undefined
    return serverIssue ? { ...local, ready: false, detail: serverIssue } : local
  }
  const unresolvedMetrics = normalized
    .map((metric) => ({ metric, readiness: readinessFor(metric) }))
    .filter(({ readiness }) => !readiness.ready)
  const hasActiveMetrics = normalized.length > 0 || !!measurementPlan && (
    measurementPlan.metrics.length > 0
    || measurementPlan.producers.length > 0
    || measurementPlan.inputs.length > 0
  )

  function builtInDraft(selectedKeys: Set<string>) {
    const currentByKey = new Map(runtimeBuiltIns.map((metric) => [metric.catalogKey!, metric]))
    // The plan's runtime outputs are what the dialog shows as selected, so they
    // are also what a deselect must remove -- even when the design's own metric
    // list has lost the matching declaration, which would otherwise leave the
    // plan naming a metric nothing declares and every save 422ing.
    const plannedIdByKey = new Map(
      (measurementPlan?.producers ?? [])
        .filter((producer) => producer.producer_id === 'asaree.runtime')
        .flatMap((producer) => Object.entries(producer.outputs)),
    )
    const preserved = normalized.filter((metric) => !(metric.kind === 'runtime' && metric.catalogKey && catalogBuiltIns.some((entry) => entry.key === metric.catalogKey)))
    const selected = catalogBuiltIns
      .filter((entry) => selectedKeys.has(entry.key))
      .map((entry) => {
        const current = currentByKey.get(entry.key)
        if (current) return current
        const plannedId = plannedIdByKey.get(entry.key)
        return plannedId ? { ...makeCatalogMetric(entry, false), id: plannedId } : makeCatalogMetric(entry, false)
      })
    let nextPlan = measurementPlan
    for (const entry of catalogBuiltIns.filter((entry) => !selectedKeys.has(entry.key))) {
      for (const metricId of new Set([currentByKey.get(entry.key)?.id, plannedIdByKey.get(entry.key)])) {
        nextPlan = removeMetricFromMeasurementPlan(nextPlan, metricId)
      }
    }
    for (const metric of selected) nextPlan = upsertRuntimeMetric(nextPlan, metric)
    const nextMetrics = withoutRanking([...preserved, ...selected])
    const byId = new Map(nextMetrics.map((metric) => [metric.id, metric]))
    const synchronizedPlan = nextPlan && {
      ...nextPlan,
      metrics: nextPlan.metrics.map((definition) => {
        const designMetric = byId.get(definition.id)
        return designMetric ? { ...definition, primary: designMetric.primary } : definition
      }),
    }
    return { metrics: nextMetrics, measurementPlan: synchronizedPlan }
  }

  return (
    <div className="@container/metrics space-y-2">
      {normalized.length === 0 && <p className="rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground">No metrics selected yet.</p>}
      {unresolvedMetrics.length > 0 && <div className="rounded-md border border-[color:var(--chart-4)]/40 bg-[color:var(--chart-4)]/5 px-3 py-2 text-xs text-[color:var(--chart-4)]"><p className="font-medium">Measurement plan needs attention</p><p className="mt-1">{unresolvedMetrics.length} metric {unresolvedMetrics.length === 1 ? 'needs' : 'need'} repair or removal.</p></div>}
      {validationQuery.isFetching && <p className="text-xs text-muted-foreground">Checking measurement-plan readiness…</p>}
      {readinessIssues.length > 0 && <div className="rounded-md border border-[color:var(--chart-4)]/40 bg-[color:var(--chart-4)]/5 px-3 py-2 text-xs text-[color:var(--chart-4)]"><p className="font-medium">Unavailable metrics will be skipped</p><ul className="mt-1 list-disc space-y-0.5 pl-4">{readinessIssues.map((issue) => <li key={`${issue.code}-${issue.path}`}>{issue.message}</li>)}</ul><p className="mt-1">Their configuration is preserved while other ready metrics continue to run.</p></div>}
      {blockingValidationIssues.length > 0 && <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive"><p className="font-medium">Production validation found invalid configuration</p><ul className="mt-1 list-disc space-y-0.5 pl-4">{blockingValidationIssues.map((issue) => <li key={`${issue.code}-${issue.path}`}>{issue.message}</li>)}</ul><p className="mt-1">Publishing and production runs remain blocked.</p></div>}
      {validationQuery.isError && <p className="text-xs text-[color:var(--chart-4)]">Could not check server-side readiness. Publishing still runs the same validation before creating a revision.</p>}
      {normalized.length > 0 && <p className="text-xs text-muted-foreground">{normalized.length} {normalized.length === 1 ? 'metric' : 'metrics'} selected.</p>}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" disabled={disabled} onClick={() => setMetricsDialogOpen(true)}>
          {!hasActiveMetrics && <Plus className="size-3.5" />} {hasActiveMetrics ? 'Manage metrics' : 'Add metrics'}
        </Button>
      </div>
      <MetricsDialog
        open={metricsDialogOpen}
        onOpenChange={(open) => {
          setMetricsDialogOpen(open)
        }}
        catalogBuiltIns={catalogBuiltIns}
        supportedBuiltInKeys={supportedBuiltInKeys}
        selectedKeys={savedBuiltInKeys}
        initialDraftKeys={savedBuiltInKeys.length === 0 && normalized.length === 0
          ? catalogBuiltIns.map((entry) => entry.key)
          : undefined}
        capabilitiesLoading={capabilitiesQuery.isPending}
        capabilitiesUnavailable={!experimentId || capabilitiesQuery.isError}
        disabled={disabled}
        metrics={normalized}
        measurementPlan={measurementPlan}
        graph={graph}
        metricIssueById={serverIssueByMetricId}
        onApply={async (selectedKeys, customChanges, customMetricIds) => {
          let draft = builtInDraft(selectedKeys)
          for (const metric of draft.metrics.filter((item) => item.kind === 'custom' && item.id && !customMetricIds.includes(item.id))) {
            draft = {
              metrics: draft.metrics.filter((item) => item.id !== metric.id),
              measurementPlan: removeMetricFromMeasurementPlan(draft.measurementPlan, metric.id),
            }
          }
          for (const change of customChanges) {
            if (!change.metric.id || !customMetricIds.includes(change.metric.id)) continue
            draft = {
              metrics: draft.metrics.some((metric) => metric.id === change.metric.id)
                ? draft.metrics.map((metric) => metric.id === change.metric.id ? change.metric : metric)
                : [...draft.metrics, change.metric],
              measurementPlan: applyCustomMetricChange(draft.measurementPlan, change.metric, change.config, graph),
            }
          }
          const customById = new Map(draft.metrics.filter((metric) => metric.kind === 'custom' && metric.id).map((metric) => [metric.id!, metric]))
          const orderedCustomMetrics = customMetricIds.flatMap((id) => customById.get(id) ?? [])
          draft.metrics = [...draft.metrics.filter((metric) => metric.kind !== 'custom'), ...orderedCustomMetrics]
          const normalizedDraftMetrics = withoutRanking(draft.metrics)
          draft = {
            metrics: normalizedDraftMetrics,
            measurementPlan: synchronizeMeasurementPlanMetrics(draft.measurementPlan, normalizedDraftMetrics),
          }
          if (onApplyMetrics) await onApplyMetrics(draft.metrics, draft.measurementPlan)
          else onChange(draft.metrics, draft.measurementPlan)
        }}
      />
    </div>
  )
}
