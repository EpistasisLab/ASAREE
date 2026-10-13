import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronsUpDown } from 'lucide-react'
import { mcpServersApi, protocolsApi } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { HUD_ACCENT_RING_CLASSNAME, PICKER_GROUP_CLASSNAME, cn } from '@/lib/utils'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { AGENT_OUTPUT_PRODUCER_ID, agentOutputSourceOptions } from '@/lib/agentOutputMetrics'
import { mcpToolSourceOptions, type McpToolSourceOption } from '@/lib/mcpToolMetrics'
import { pythonScriptSourceOptions } from '@/lib/pythonScriptMetrics'
import { makeCustomMetric } from '@/lib/metricCatalog'
import {
  bindingProjection,
  declaredAgentFields,
  defaultFieldNames,
  latestNodePayload,
  mergeFields,
  observedFields,
  segmentsForPath,
  type FieldProjection,
} from '@/lib/metricFields'
import { TOOL_STEP_PRODUCER_ID, toolStepSourceOptions } from '@/lib/toolStepMetrics'
import {
  NODE_RUNTIME_OUTPUTS,
  NODE_RUNTIME_PRODUCER_ID,
  bindingNodeIds,
  nodeRuntimeOutput,
  nodeRuntimeSourceOptions,
} from '@/lib/nodeRuntimeMetrics'
import type { CustomMetricProducer, CustomMetricProducerConfig, CustomMetricSourceContext } from '@/lib/customMetrics'
import type { DesignMetric, MeasurementPlan } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { MetricFieldPicker } from './MetricFieldPicker'
import { useDialogAutosave, type DialogAutosaveStatus } from './useDialogAutosave'

type Binding = MeasurementPlan['producers'][number] | undefined
export type MetricNodeDisplay = { label: string; type: 'Agent' | 'Script' | 'MCP Tool' | 'Tool Step' | 'Feature pipeline' | 'Node runtime' | 'Critic Gate' }

export function MetricNodeLabel({ display, reason }: { display: MetricNodeDisplay; reason?: string }) {
  return (
    <span className="flex min-w-0 flex-1 items-center gap-2">
      <span className="truncate text-sm font-medium">{display.label}</span>
      <Badge variant="outline" className="h-5 px-1.5 text-[10px] text-muted-foreground">
        {display.type}
      </Badge>
      {reason && <span className="truncate text-xs text-muted-foreground">— {reason}</span>}
    </span>
  )
}

type MetricNodeChoice = { value: string; display: MetricNodeDisplay; reason?: string; agentId?: string; category: string }

function MetricNodePicker({ value, display, choices, graph, onChange }: {
  value: string
  display: MetricNodeDisplay | null | undefined
  choices: MetricNodeChoice[]
  graph?: ProtocolGraph
  onChange: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const expandGroups = (graph?.nodes.filter(node => node.type === 'agent' || node.type === 'sub_agent').length ?? 0) < 2
  const groups = new Map<string, { label: string; connectors: Map<string, Map<string, MetricNodeChoice[]>> }>()
  for (const choice of choices) {
    const key = choice.agentId ?? (choice.value === 'node_runtime' ? 'runtime' : 'shared')
    const label = choice.agentId ? String(graph?.nodes.find(node => node.id === choice.agentId)?.data.label || 'Agent')
      : key === 'runtime' ? 'Runtime metrics for selected nodes' : 'Shared or unconnected components'
    const connector = ['MCP Tools', 'Scripts', 'Tool steps'].includes(choice.category) ? 'Tools' : choice.category === 'Agent output' ? 'Agent' : choice.category
    if (!`${label} ${connector} ${choice.category} ${choice.display.label} ${choice.display.type}`.toLowerCase().includes(search.trim().toLowerCase())) continue
    if (!groups.has(key)) groups.set(key, { label, connectors: new Map() })
    const connectors = groups.get(key)!.connectors
    if (!connectors.has(connector)) connectors.set(connector, new Map())
    const categories = connectors.get(connector)!
    categories.set(choice.category, [...(categories.get(choice.category) ?? []), choice])
  }
  return <Popover open={open} onOpenChange={(next) => { setOpen(next); if (next) setSearch('') }}>
    <PopoverTrigger render={<Button variant="outline" role="combobox" aria-label="Metric node" aria-expanded={open} className="w-full justify-between" />}>
      {display ? <MetricNodeLabel display={display} /> : 'Select a node…'}<ChevronsUpDown className="size-4 shrink-0 text-muted-foreground" />
    </PopoverTrigger>
    <PopoverContent align="start" className={cn('w-[min(36rem,calc(100vw-4rem))] max-h-[min(32rem,var(--available-height))] overflow-hidden', HUD_ACCENT_RING_CLASSNAME)}>
      <Input autoFocus placeholder="Search nodes…" aria-label="Search metric nodes" value={search} onChange={event => setSearch(event.target.value)} />
      <div className="min-h-0 overflow-y-auto" role="listbox" aria-label="Metric nodes">
        {groups.size === 0 && <p className="p-3 text-sm text-muted-foreground">No matching nodes.</p>}
        {[...groups.entries()].sort(([, a], [, b]) => a.label.localeCompare(b.label)).map(([id, group]) => <details key={`${id}:${!!search.trim()}:${expandGroups}`} open={expandGroups || !!search.trim()} className={PICKER_GROUP_CLASSNAME}>
          <summary className="cursor-pointer bg-muted/30 px-3 py-2 text-sm font-semibold">{group.label}</summary>
          <div className="space-y-3 p-2">{[...group.connectors.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([connector, categories]) => <section key={connector} aria-label={`${group.label}: ${connector}`}>
            <h3 className="px-2 py-1 text-sm font-medium text-primary">{connector}</h3>
            <div className="ml-3 space-y-2 border-l border-border pl-2">{[...categories.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([category, options]) => <section key={category} aria-label={`${group.label}: ${connector}: ${category}`}>
            {connector === 'Tools' && <h4 className="px-2 py-1 text-sm font-semibold text-chart-2">{category}</h4>}
            <div className={connector === 'Tools' ? 'ml-3' : undefined}>{options.sort((a, b) => a.display.label.localeCompare(b.display.label)).map(choice => <button key={choice.value} type="button" role="option" aria-selected={value === choice.value} disabled={!!choice.reason}
              aria-label={`${choice.display.label}, ${choice.display.type}${choice.reason ? `, ${choice.reason}` : ''}`}
              className={cn('flex w-full cursor-pointer items-center rounded-md px-2 py-2 text-left hover:bg-muted disabled:cursor-not-allowed disabled:text-muted-foreground', value === choice.value && 'bg-primary/15 ring-1 ring-primary')}
              onClick={() => { onChange(choice.value); setOpen(false) }}><MetricNodeLabel display={choice.display} reason={choice.reason} /></button>)}</div>
            </section>)}</div>
          </section>)}</div>
        </details>)}
      </div>
    </PopoverContent>
  </Popover>
}

function duplicateName(metric: DesignMetric, name: string, metrics: DesignMetric[]) {
  const normalized = name.trim().toLocaleLowerCase()
  return metrics.some((item) => item.id !== metric.id && item.name.trim().toLocaleLowerCase() === normalized)
}

function availableMetricName(metric: DesignMetric, name: string, metrics: DesignMetric[]) {
  const requested = name.trim()
  const base = metrics.find((item) => item.id !== metric.id && item.name.trim().toLocaleLowerCase() === requested.toLocaleLowerCase())?.name.trim() || requested || 'Metric'
  const names = new Set(metrics.filter((item) => item.id !== metric.id).map((item) => item.name.trim().toLocaleLowerCase()))
  let suffix = 2
  while (names.has(`${base} ${suffix}`.toLocaleLowerCase())) suffix += 1
  return `${base} ${suffix}`
}

function autosaveLabel(status: DialogAutosaveStatus, invalid: boolean) {
  if (invalid) return 'Complete the required fields to start autosaving.'
  if (status === 'waiting') return 'Waiting to save…'
  if (status === 'saving') return 'Saving…'
  if (status === 'error') return 'Could not autosave. Edit a field or close to retry.'
  if (status === 'saved') return 'Saved'
  return 'Changes save automatically.'
}

export function CustomMetricFlow({ metric, binding, graph, protocolId, existingMetrics, sourceContext, submitting = false, autosave = false, onAutosaveReady, onCancel, onDirtyChange, onSave }: {
  metric: DesignMetric
  binding: Binding
  graph: ProtocolGraph | undefined
  // Where the field picker looks for a source's last observed output.
  protocolId?: string
  existingMetrics: DesignMetric[]
  sourceContext?: CustomMetricSourceContext
  submitting?: boolean
  autosave?: boolean
  onAutosaveReady?: (flush: (() => void) | null) => void
  onCancel?: () => void
  onDirtyChange: (dirty: boolean) => void
  onSave: (metric: DesignMetric, config: CustomMetricProducerConfig) => void | Promise<unknown>
}) {
  const agentSources = agentOutputSourceOptions(graph).filter((source) => !sourceContext || sourceContext.producer === 'agent' && source.agentNodeId === sourceContext.nodeId)
  const pythonSources = pythonScriptSourceOptions(graph).filter((source) => !sourceContext || sourceContext.producer === 'python' && source.scriptNodeId === sourceContext.nodeId)
  const mcpSources = mcpToolSourceOptions(graph).filter((source) => !sourceContext || sourceContext.producer === 'mcp' && source.mcpNodeId === sourceContext.nodeId)
  const toolStepSources = toolStepSourceOptions(graph).filter((source) => !sourceContext || sourceContext.producer === 'tool_step' && source.nodeId === sourceContext.nodeId)
  // Every Agent/Critic Gate stays choosable even from a node-first context:
  // a stage total often spans several nodes (all the gates, say).
  const runtimeNodeSources = nodeRuntimeSourceOptions(graph)
  const offerNodeRuntime = runtimeNodeSources.length > 0 && (!sourceContext || sourceContext.producer === 'node_runtime')
  const initialProducer: CustomMetricProducer | undefined = binding?.producer_id === AGENT_OUTPUT_PRODUCER_ID
    ? 'agent'
    : binding?.producer_id === 'asaree.python_script'
      ? 'python'
      : binding?.producer_id === 'asaree.mcp_tool'
        ? 'mcp'
        : binding?.producer_id === TOOL_STEP_PRODUCER_ID
          ? 'tool_step'
          : binding?.producer_id === NODE_RUNTIME_PRODUCER_ID
            ? 'node_runtime'
            : sourceContext?.producer
  const initialAgentSource = agentSources.find((source) => source.agentNodeId === binding?.config.agent_node_id)
    ?? (sourceContext?.producer === 'agent' && agentSources.length === 1 ? agentSources[0] : undefined)
  const initialPythonSource = pythonSources.find((source) => source.agentNodeId === binding?.config.agent_node_id && source.scriptNodeId === binding?.config.script_node_id)
    ?? (sourceContext?.producer === 'python' && pythonSources.length === 1 ? pythonSources[0] : undefined)
  const initialMcpSource = mcpSources.find((source) => source.agentNodeId === binding?.config.agent_node_id && source.mcpNodeId === binding?.config.mcp_node_id)
    ?? (sourceContext?.producer === 'mcp' && mcpSources.length === 1 ? mcpSources[0] : undefined)
  const initialToolStepSource = toolStepSources.find((source) => source.nodeId === binding?.config.node_id)
    ?? (sourceContext?.producer === 'tool_step' && toolStepSources.length === 1 ? toolStepSources[0] : undefined)
  const initialProjection = bindingProjection(binding, metric.id)
  const initialRuntimeNodeIds = binding?.producer_id === NODE_RUNTIME_PRODUCER_ID
    ? bindingNodeIds(binding)
    : sourceContext?.producer === 'node_runtime' ? [sourceContext.nodeId] : []
  const initialRuntimeOutput = binding?.producer_id === NODE_RUNTIME_PRODUCER_ID
    ? Object.keys(binding.outputs)[0] ?? 'total_tokens'
    : 'total_tokens'
  const editing = existingMetrics.some((item) => item.id === metric.id)
  // Ticking several fields creates one metric per field in one pass -- only
  // when creating with an explicit Add button, since autosave needs a single
  // draft with a stable id.
  const multiField = !editing && !autosave
  const [name, setName] = useState(editing ? metric.name : '')
  const [nameTouched, setNameTouched] = useState(editing)
  const [picks, setPicks] = useState<FieldProjection[]>(initialProjection ? [initialProjection] : [])
  const [toolStepNodeId, setToolStepNodeId] = useState(initialToolStepSource?.nodeId ?? '')
  const [producer, setProducer] = useState<CustomMetricProducer | undefined>(initialProducer)
  const [agentNodeId, setAgentNodeId] = useState(initialAgentSource?.agentNodeId ?? '')
  const [runtimeNodeIds, setRuntimeNodeIds] = useState<string[]>(initialRuntimeNodeIds)
  const [runtimeOutput, setRuntimeOutput] = useState(initialRuntimeOutput)
  const serversQuery = useQuery({ queryKey: ['mcp-servers'], queryFn: mcpServersApi.list, staleTime: 60_000, enabled: mcpSources.length > 0 })
  const servers = serversQuery.data ?? []
  const [pythonSourceKey, setPythonSourceKey] = useState(initialPythonSource?.key ?? '')
  const [mcpSourceKey, setMcpSourceKey] = useState(initialMcpSource?.key ?? '')
  const [toolName, setToolName] = useState(typeof binding?.config.tool_name === 'string' ? binding.config.tool_name : '')
  const selectedPythonSource = pythonSources.find((source) => source.key === pythonSourceKey)
  const selectedMcpSource = mcpSources.find((source) => source.key === mcpSourceKey)
  const selectedServer = servers.find((server) => server.id === selectedMcpSource?.serverId)
  const selectedTool = selectedServer?.capabilities?.tools?.find((tool) => tool.name === toolName)
  const duplicate = duplicateName(metric, name, existingMetrics)
  const detailError = !name.trim() ? 'Enter a metric name.' : duplicate ? `A metric with this name already exists. Try ${availableMetricName(metric, name, existingMetrics)}.` : undefined
  const agentSourceDisabledReason = (source: (typeof agentSources)[number]) => source.disabledReason
  const selectedAgentSource = agentSources.find((source) => source.agentNodeId === agentNodeId)
  const agentSourceError = !selectedAgentSource ? 'Choose an Agent.' : agentSourceDisabledReason(selectedAgentSource)
  const pythonSourceDisabledReason = (source: (typeof pythonSources)[number]) => source.disabledReason
  const pythonSourceError = !selectedPythonSource
    ? 'Choose a direct Agent-to-Python-Script connection.'
    : pythonSourceDisabledReason(selectedPythonSource)
  const mcpSourceDisabledReason = (source: McpToolSourceOption) => source.disabledReason
    ?? (serversQuery.isError ? 'Registered MCP Servers could not be loaded.'
      : serversQuery.isPending ? undefined
        : !servers.some((server) => server.id === source.serverId) ? 'The registered MCP Server is unavailable.'
          : servers.find((server) => server.id === source.serverId)?.status !== 'connected' ? 'The registered MCP Server is not connected.'
            : undefined)
  const mcpSourceError = !selectedMcpSource
    ? 'Choose a direct Agent-to-MCP Tool connection.'
    : mcpSourceDisabledReason(selectedMcpSource)
  const selectedToolStepSource = toolStepSources.find((source) => source.nodeId === toolStepNodeId)
  const toolStepSourceError = !selectedToolStepSource ? 'Choose a Tool Step.' : selectedToolStepSource.disabledReason
  const runtimeNodeError = runtimeNodeIds.length === 0 ? 'Choose at least one node.' : undefined
  const selectedMetricNode = producer === 'node_runtime' ? 'node_runtime' : selectedAgentSource
    ? `agent:${selectedAgentSource.agentNodeId}`
    : selectedPythonSource
      ? `python:${selectedPythonSource.key}`
      : selectedMcpSource
        ? `mcp:${selectedMcpSource.key}`
        : selectedToolStepSource
          ? `tool_step:${selectedToolStepSource.nodeId}`
          : '__none__'
  const sourceNodeLabel = (nodeId: string) => graph?.nodes.find((node) => node.id === nodeId)?.data.label || nodeId
  const sourceAgentLabel = (agentNodeId: string) => graph?.nodes.find((node) => node.id === agentNodeId)?.data.label || agentNodeId
  // Keep the connection path recognizable without making the node type look
  // like another colon-delimited part of the user-defined name.
  const pythonNodeDisplay = (source: (typeof pythonSources)[number]): MetricNodeDisplay => ({
    label: `${sourceAgentLabel(source.agentNodeId)}:${sourceNodeLabel(source.scriptNodeId)}`,
    type: 'Script',
  })
  const mcpNodeDisplay = (source: McpToolSourceOption): MetricNodeDisplay => ({
    label: `${sourceAgentLabel(source.agentNodeId)}:${sourceNodeLabel(source.mcpNodeId)}`,
    type: 'MCP Tool',
  })
  const agentNodeDisplay = (source: (typeof agentSources)[number]): MetricNodeDisplay => ({
    label: source.label,
    type: 'Agent',
  })
  const toolStepNodeDisplay = (source: (typeof toolStepSources)[number]): MetricNodeDisplay => ({
    label: source.label,
    type: 'Tool Step',
  })
  const nodeRuntimeDisplay: MetricNodeDisplay = { label: 'Runtime metrics for selected nodes', type: 'Node runtime' }
  const selectedNodeDisplay = producer === 'node_runtime' ? nodeRuntimeDisplay : selectedAgentSource
    ? agentNodeDisplay(selectedAgentSource)
    : selectedPythonSource
      ? pythonNodeDisplay(selectedPythonSource)
      : selectedMcpSource
        ? mcpNodeDisplay(selectedMcpSource)
        : selectedToolStepSource
          ? toolStepNodeDisplay(selectedToolStepSource)
          : null
  const sourceSelected = producer === 'agent' ? Boolean(selectedAgentSource)
    : producer === 'python' ? Boolean(selectedPythonSource)
      : producer === 'mcp' ? Boolean(selectedMcpSource)
        : producer === 'tool_step' ? Boolean(selectedToolStepSource)
          : producer === 'node_runtime'
  // Agent and Tool Step outputs are JSON documents, so a metric can record
  // one field of them; Script/MCP Tool metrics record the whole tool result.
  const fieldNodeId = producer === 'agent' ? selectedAgentSource?.agentNodeId : producer === 'tool_step' ? selectedToolStepSource?.nodeId : undefined
  const runsQuery = useQuery({
    queryKey: ['protocols', protocolId, 'runs'],
    queryFn: () => protocolsApi.listRuns(protocolId!),
    enabled: Boolean(protocolId && fieldNodeId),
    staleTime: 30_000,
  })
  const observedPayload = fieldNodeId ? latestNodePayload(runsQuery.data, fieldNodeId) : undefined
  const fields = fieldNodeId
    ? mergeFields(producer === 'agent' ? declaredAgentFields(graph, fieldNodeId) : [], observedFields(observedPayload))
    : []
  const fieldStatus = !fieldNodeId ? undefined
    : runsQuery.isPending && protocolId ? 'Looking for fields in the latest run…'
      : observedPayload ? 'Fields from the latest completed run' + (producer === 'agent' ? ' and the Output Parser.' : '.')
        : fields.length ? 'Fields from the Output Parser. Run the protocol once to list nested fields.'
          : 'No fields yet. Run the protocol once to list them, or type a field path.'
  const otherMetricNames = existingMetrics.filter((item) => item.id !== metric.id).map((item) => item.name)
  const pickNames = defaultFieldNames(
    picks.map((projection) => ({ field: { segments: segmentsForPath(projection.path, fields) }, projection })),
    otherMetricNames,
  )
  function changePicks(next: FieldProjection[]) {
    setPicks(next)
    if (!nameTouched) {
      setName(next.length === 1
        ? defaultFieldNames([{ field: { segments: segmentsForPath(next[0].path, fields) }, projection: next[0] }], otherMetricNames)[0]
        : '')
    }
  }
  // e.g. "Total tokens · DC", or "Turns · Critic (DC) + Critic (FTE)".
  function runtimeName(nodeIds: string[], outputKey: string) {
    const labels = nodeIds.map((id) => runtimeNodeSources.find((source) => source.nodeId === id)?.label ?? id)
    return labels.length ? `${nodeRuntimeOutput(outputKey)?.label ?? outputKey} · ${labels.join(' + ')}` : ''
  }
  function changeRuntime(nodeIds: string[], outputKey: string) {
    setRuntimeNodeIds(nodeIds)
    setRuntimeOutput(outputKey)
    if (!nameTouched) setName(runtimeName(nodeIds, outputKey))
  }
  function toggleRuntimeNode(nodeId: string, checked: boolean) {
    const next = checked
      ? runtimeNodeSources.map((source) => source.nodeId).filter((id) => id === nodeId || runtimeNodeIds.includes(id))
      : runtimeNodeIds.filter((id) => id !== nodeId)
    changeRuntime(next, runtimeOutput)
  }
  function selectMetricNode(value: string | null) {
    setPicks([])
    if (!nameTouched) setName('')
    if (value === 'node_runtime') {
      setProducer('node_runtime')
      setAgentNodeId('')
      setPythonSourceKey('')
      setMcpSourceKey('')
      setToolStepNodeId('')
      setToolName('')
      return
    }
    const agentSource = agentSources.find((source) => `agent:${source.agentNodeId}` === value)
    const pythonSource = pythonSources.find((source) => `python:${source.key}` === value)
    const mcpSource = mcpSources.find((source) => `mcp:${source.key}` === value)
    const toolStepSource = toolStepSources.find((source) => `tool_step:${source.nodeId}` === value)
    setProducer(agentSource ? 'agent' : pythonSource ? 'python' : mcpSource ? 'mcp' : toolStepSource ? 'tool_step' : undefined)
    setAgentNodeId(agentSource?.agentNodeId ?? '')
    setPythonSourceKey(pythonSource?.key ?? '')
    setMcpSourceKey(mcpSource?.key ?? '')
    setToolStepNodeId(toolStepSource?.nodeId ?? '')
    setToolName('')
  }
  const selectedToolDisabled = Boolean(toolName && selectedMcpSource && !selectedMcpSource.toolNames.includes(toolName))
  const mcpMappingError = selectedToolDisabled
    ? 'The selected MCP tool is disabled for this MCP node.'
    : !selectedTool
    ? 'Choose an available MCP tool.'
    : undefined
  const initialSignature = JSON.stringify({
    name: editing ? metric.name : '',
    producer: initialProducer,
    agentNodeId: initialAgentSource?.agentNodeId ?? '',
    pythonSourceKey: initialPythonSource?.key ?? '',
    mcpSourceKey: initialMcpSource?.key ?? '',
    toolName: typeof binding?.config.tool_name === 'string' ? binding.config.tool_name : '',
    toolStepNodeId: initialToolStepSource?.nodeId ?? '',
    runtimeNodeIds: initialRuntimeNodeIds,
    runtimeOutput: initialRuntimeOutput,
    picks: initialProjection ? [initialProjection] : [],
  })
  const currentSignature = JSON.stringify({ name, producer, agentNodeId, pythonSourceKey, mcpSourceKey, toolName, toolStepNodeId, runtimeNodeIds, runtimeOutput, picks })
  const formRef = useRef<HTMLElement>(null)
  useEffect(() => onDirtyChange(currentSignature !== initialSignature), [currentSignature, initialSignature, onDirtyChange])
  useEffect(() => {
    formRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }, [])

  useEffect(() => {
    if (producer !== 'mcp' || toolName) return
    const availableTool = selectedMcpSource?.toolNames[0]
    if (availableTool) setToolName(availableTool)
  }, [producer, selectedMcpSource, toolName])

  const manyFields = picks.length > 1
  const saveDisabled = Boolean(
    (manyFields ? undefined : detailError)
    || !producer
    || (producer === 'agent' ? agentSourceError
      : producer === 'tool_step' ? toolStepSourceError
        : producer === 'node_runtime' ? runtimeNodeError
          : producer === 'python' ? pythonSourceError : mcpSourceError || mcpMappingError),
  )
  const nextMetric: DesignMetric = { id: metric.id, name: name.trim(), kind: 'custom' }
  const configFor = (projection: FieldProjection | undefined): CustomMetricProducerConfig | null =>
    producer === 'agent' && selectedAgentSource
      ? { producer, agentNodeId: selectedAgentSource.agentNodeId, ...(projection ? { projection } : {}) }
      : producer === 'tool_step' && selectedToolStepSource
        ? { producer, nodeId: selectedToolStepSource.nodeId, ...(projection ? { projection } : {}) }
        : producer === 'mcp' && selectedMcpSource
          ? { producer, source: selectedMcpSource, toolName }
          : producer === 'python'
            ? { producer, sourceKey: pythonSourceKey }
            : producer === 'node_runtime'
              ? { producer, nodeIds: runtimeNodeIds, output: runtimeOutput }
              : null
  const nextConfig = configFor(picks[0])
  async function saveAll() {
    if (!manyFields) {
      if (nextConfig) await onSave(nextMetric, nextConfig)
      return
    }
    for (const [index, projection] of picks.entries()) {
      const config = configFor(projection)
      const fieldMetric = index === 0 ? { ...nextMetric, name: pickNames[0] } : { ...makeCustomMetric({ name: pickNames[index] }) }
      if (config) await onSave(fieldMetric, config)
    }
  }
  const autosaveDraft = autosave && !saveDisabled && nextConfig && currentSignature !== initialSignature
    ? { metric: nextMetric, config: nextConfig }
    : null
  const metricAutosave = useDialogAutosave({
    draft: autosaveDraft,
    signature: currentSignature,
    onSave: ({ metric: savedMetric, config }) => onSave(savedMetric, config),
  })
  useEffect(() => {
    if (!autosave || !onAutosaveReady) return
    onAutosaveReady(metricAutosave.flush)
    return () => onAutosaveReady(null)
  }, [autosave, metricAutosave.flush, onAutosaveReady])

  return <section ref={formRef} aria-label={editing ? `Edit ${metric.name}` : 'Create custom metric'} className="scroll-mt-4 space-y-5 rounded-md border border-primary/30 bg-primary/5 p-4">
    <div>
      <h3 className="text-sm font-semibold">{editing ? `Edit ${metric.name}` : 'Create custom metric'}</h3>
      <p className="mt-1 text-xs text-muted-foreground">Choose an Agent output, Agent tool result, Tool Step result, or the runtime totals of chosen nodes to capture and export.</p>
    </div>

    <fieldset className="space-y-3">
      <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Metric node</legend>
      {!sourceContext ? <div className="space-y-1.5">
        <Label>Node</Label>
        <MetricNodePicker value={selectedMetricNode} display={selectedNodeDisplay} graph={graph} onChange={selectMetricNode} choices={[
          ...agentSources.map(source => ({ value: `agent:${source.agentNodeId}`, display: agentNodeDisplay(source), reason: agentSourceDisabledReason(source), agentId: source.agentNodeId, category: 'Agent output' })),
          ...pythonSources.map(source => ({ value: `python:${source.key}`, display: pythonNodeDisplay(source), reason: pythonSourceDisabledReason(source), agentId: source.agentNodeId, category: 'Scripts' })),
          ...mcpSources.map(source => ({ value: `mcp:${source.key}`, display: mcpNodeDisplay(source), reason: mcpSourceDisabledReason(source), agentId: source.agentNodeId, category: 'MCP Tools' })),
          ...toolStepSources.map(source => ({ value: `tool_step:${source.nodeId}`, display: toolStepNodeDisplay(source), reason: source.disabledReason, category: 'Tool steps' })),
          ...(offerNodeRuntime ? [{ value: 'node_runtime', display: nodeRuntimeDisplay, category: 'Runtime' }] : []),
        ]} />
      </div> : <>
        <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm">{producer === 'agent' ? 'Agent output' : producer === 'tool_step' ? 'Tool Step' : producer === 'node_runtime' ? 'Node runtime' : producer === 'python' ? 'Python Script' : 'MCP Tool'}</p>
        {producer === 'tool_step' && <div className="space-y-1.5"><Label>Tool Step source</Label><p className="rounded-md border bg-muted/30 px-3 py-2 text-sm">{selectedToolStepSource?.label}</p>{toolStepSourceError && <p role="alert" className="text-xs text-destructive">{toolStepSourceError}</p>}</div>}
        {producer === 'agent' && <div className="space-y-1.5"><Label>Agent source</Label><p className="rounded-md border bg-muted/30 px-3 py-2 text-sm">{selectedAgentSource?.label}</p>{agentSourceError && <p role="alert" className="text-xs text-destructive">{agentSourceError}</p>}</div>}
        {producer === 'python' && <div className="space-y-1.5"><Label>Agent to Python Script source</Label>{pythonSources.length === 1 ? <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm">{selectedPythonSource?.label}</p> : <Select value={pythonSourceKey || '__none__'} onValueChange={(value) => setPythonSourceKey(value === '__none__' ? '' : value ?? '')}><SelectTrigger className="w-full" aria-label="Agent to Python Script source" aria-describedby={pythonSourceError ? 'python-flow-source-error' : undefined}><SelectValue>{() => selectedPythonSource?.label ?? 'Select a direct connection…'}</SelectValue></SelectTrigger><SelectContent><SelectItem value="__none__" disabled>Select a direct connection…</SelectItem>{pythonSources.map((source) => { const reason = pythonSourceDisabledReason(source); return <SelectItem key={source.key} value={source.key} disabled={!!reason}>{source.label}{reason ? ` — ${reason}` : ''}</SelectItem> })}</SelectContent></Select>}{pythonSourceError && <p id="python-flow-source-error" role="alert" className="text-xs text-destructive">{pythonSourceError}</p>}</div>}
        {producer === 'mcp' && <div className="space-y-1.5"><Label>Agent to MCP Tool source</Label>{mcpSources.length === 1 ? <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm">{selectedMcpSource?.label}</p> : <Select value={mcpSourceKey || '__none__'} onValueChange={(value) => { setMcpSourceKey(value === '__none__' ? '' : value ?? ''); setToolName('') }}><SelectTrigger className="w-full" aria-label="Agent to MCP Tool source" aria-describedby={mcpSourceError ? 'mcp-flow-source-error' : undefined}><SelectValue>{() => selectedMcpSource?.label ?? 'Select a direct connection…'}</SelectValue></SelectTrigger><SelectContent><SelectItem value="__none__" disabled>Select a direct connection…</SelectItem>{mcpSources.map((source) => { const reason = mcpSourceDisabledReason(source); return <SelectItem key={source.key} value={source.key} disabled={!!reason}>{source.label}{reason ? ` — ${reason}` : ''}</SelectItem> })}</SelectContent></Select>}{mcpSourceError && <p id="mcp-flow-source-error" role="alert" className="text-xs text-destructive">{mcpSourceError}</p>}</div>}
      </>}
      {producer === 'mcp' && <p role="status" className="text-xs text-muted-foreground">{serversQuery.isPending ? 'Loading registered MCP Servers…' : serversQuery.isError ? 'Registered MCP Servers could not be loaded.' : 'Registered MCP Server availability loaded.'}</p>}
    </fieldset>

    {producer === 'node_runtime' && <fieldset className="space-y-3">
      <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Runtime metrics for selected nodes</legend>
      <div className="space-y-1.5" role="group" aria-label="Nodes to measure">
        <Label>Nodes</Label>
        {runtimeNodeSources.map((source) => <label key={source.nodeId} className="flex cursor-pointer items-center gap-2">
          <Checkbox checked={runtimeNodeIds.includes(source.nodeId)} onCheckedChange={(checked) => toggleRuntimeNode(source.nodeId, checked === true)} />
          <MetricNodeLabel display={{ label: source.label, type: source.type }} reason={source.disabledReason} />
        </label>)}
        {runtimeNodeError && <p role="alert" className="text-xs text-destructive">{runtimeNodeError}</p>}
      </div>
      <div className="space-y-1.5">
        <Label>Measure</Label>
        <Select value={runtimeOutput} onValueChange={(value) => value && changeRuntime(runtimeNodeIds, value)}>
          <SelectTrigger className="w-full" aria-label="Runtime measure"><SelectValue>{() => nodeRuntimeOutput(runtimeOutput)?.label ?? runtimeOutput}</SelectValue></SelectTrigger>
          <SelectContent>
            {NODE_RUNTIME_OUTPUTS.map((output) => <SelectItem key={output.key} value={output.key}>{output.label}<span className="ml-2 font-mono text-xs text-muted-foreground">{output.key}</span></SelectItem>)}
          </SelectContent>
        </Select>
      </div>
      <p className="text-xs text-muted-foreground">ASAREE sums every run these nodes launch in a replicate, critic-requested revisions included. A Critic Gate's runs are its critic's reviews.</p>
    </fieldset>}

    {sourceSelected && fieldNodeId && <MetricFieldPicker fields={fields} picks={picks} multi={multiField} status={fieldStatus} onChange={changePicks} />}

    {sourceSelected && <fieldset className="space-y-3">
      <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Metric details</legend>
      {manyFields
        ? <div className="space-y-1"><p className="text-xs text-muted-foreground">Creates {picks.length} metrics, one per field. Rename any of them afterwards.</p><ul className="space-y-0.5 rounded-md border p-2 font-mono text-xs">{picks.map((pick, index) => <li key={`${pick.path}:${pick.transform ?? ''}`}>{pickNames[index]}</li>)}</ul></div>
        : <div className="space-y-1.5"><Label htmlFor="python-flow-name">Metric name</Label><Input id="python-flow-name" value={name} aria-invalid={Boolean(detailError)} aria-describedby={detailError ? 'python-flow-name-error' : undefined} onChange={(event) => { setName(event.target.value); setNameTouched(true) }} />{detailError && <p id="python-flow-name-error" role="alert" className="text-xs text-destructive">{detailError}</p>}</div>}
    </fieldset>}

    {sourceSelected && producer === 'mcp' && <div className="space-y-3">
        <div className="space-y-1.5"><Label>MCP tool</Label><Select value={toolName || '__none__'} onValueChange={(value) => setToolName(value === '__none__' ? '' : value ?? '')} disabled={!selectedMcpSource || !!mcpSourceError}><SelectTrigger className="w-full" aria-label="MCP tool"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="__none__" disabled>Select an enabled tool…</SelectItem>{[...new Set([...(selectedMcpSource?.toolNames ?? []), ...(selectedServer?.capabilities?.tools?.map((item) => item.name) ?? [])])].map((name) => { const candidate = selectedServer?.capabilities?.tools?.find((item) => item.name === name); const reason = !selectedMcpSource?.toolNames.includes(name) ? 'disabled for this MCP node' : !candidate ? 'unavailable on server' : undefined; return <SelectItem key={name} value={name} disabled={!!reason}>{name}{reason ? ` — ${reason}` : ''}</SelectItem> })}</SelectContent></Select>{selectedTool?.description && <p className="text-xs text-muted-foreground">{selectedTool.description}</p>}</div>
        {mcpMappingError && <p role="alert" className="text-xs text-destructive">{mcpMappingError}</p>}
        <p className="text-xs text-muted-foreground">ASAREE records this tool's last call result. The Agent decides whether and how often to call it.</p>
    </div>}

    {sourceSelected && producer === 'agent' && <p className="text-xs text-muted-foreground">{picks.length ? "ASAREE records the picked field from this Agent's typed output (its Output Parser payload, else JSON in its final answer)." : "ASAREE records this Agent's completed final output verbatim."}</p>}
    {sourceSelected && producer === 'tool_step' && <p className="text-xs text-muted-foreground">{picks.length ? "ASAREE records the picked field from this Tool Step's result." : "ASAREE records this Tool Step's whole result."}</p>}

    {autosave ? (
      <p role="status" aria-live="polite" className={metricAutosave.status === 'error' ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}>
        {autosaveLabel(submitting ? 'saving' : metricAutosave.status, saveDisabled)}
      </p>
    ) : (
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="ghost" disabled={submitting} onClick={onCancel}>Cancel custom metric</Button>
        <Button type="button" disabled={saveDisabled || submitting} onClick={() => { void saveAll() }}>{submitting ? editing ? 'Saving…' : 'Creating…' : editing ? 'Save custom metric' : manyFields ? `Add ${picks.length} custom metrics` : 'Add custom metric'}</Button>
      </div>
    )}
  </section>
}
