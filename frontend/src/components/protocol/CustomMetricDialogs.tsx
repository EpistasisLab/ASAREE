import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { mcpServersApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { AGENT_OUTPUT_PRODUCER_ID, agentOutputSourceOptions } from '@/lib/agentOutputMetrics'
import { mcpToolSourceOptions, type McpToolSourceOption } from '@/lib/mcpToolMetrics'
import { pythonScriptSourceOptions } from '@/lib/pythonScriptMetrics'
import type { CustomMetricProducer, CustomMetricProducerConfig, CustomMetricSourceContext } from '@/lib/customMetrics'
import type { DesignMetric, MeasurementPlan } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { useDialogAutosave, type DialogAutosaveStatus } from './useDialogAutosave'

type Binding = MeasurementPlan['producers'][number] | undefined

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

export function CustomMetricFlow({ metric, binding, graph, existingMetrics, sourceContext, submitting = false, autosave = false, onAutosaveReady, onCancel, onDirtyChange, onSave }: {
  metric: DesignMetric
  binding: Binding
  graph: ProtocolGraph | undefined
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
  const initialProducer: CustomMetricProducer | undefined = binding?.producer_id === AGENT_OUTPUT_PRODUCER_ID
    ? 'agent'
    : binding?.producer_id === 'asaree.python_script'
      ? 'python'
      : binding?.producer_id === 'asaree.mcp_tool'
        ? 'mcp'
        : sourceContext?.producer
  const initialAgentSource = agentSources.find((source) => source.agentNodeId === binding?.config.agent_node_id)
    ?? (sourceContext?.producer === 'agent' && agentSources.length === 1 ? agentSources[0] : undefined)
  const initialPythonSource = pythonSources.find((source) => source.agentNodeId === binding?.config.agent_node_id && source.scriptNodeId === binding?.config.script_node_id)
    ?? (sourceContext?.producer === 'python' && pythonSources.length === 1 ? pythonSources[0] : undefined)
  const initialMcpSource = mcpSources.find((source) => source.agentNodeId === binding?.config.agent_node_id && source.mcpNodeId === binding?.config.mcp_node_id)
    ?? (sourceContext?.producer === 'mcp' && mcpSources.length === 1 ? mcpSources[0] : undefined)
  const editing = existingMetrics.some((item) => item.id === metric.id)
  const [name, setName] = useState(editing ? metric.name : '')
  const [producer, setProducer] = useState<CustomMetricProducer | undefined>(initialProducer)
  const [agentNodeId, setAgentNodeId] = useState(initialAgentSource?.agentNodeId ?? '')
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
  const selectedMetricNode = selectedAgentSource
    ? `agent:${selectedAgentSource.agentNodeId}`
    : selectedPythonSource
      ? `python:${selectedPythonSource.key}`
      : selectedMcpSource
        ? `mcp:${selectedMcpSource.key}`
        : '__none__'
  const sourceNodeLabel = (nodeId: string) => graph?.nodes.find((node) => node.id === nodeId)?.data.label || nodeId
  const sourceAgentLabel = (agentNodeId: string) => graph?.nodes.find((node) => node.id === agentNodeId)?.data.label || agentNodeId
  // Keep this picker in the same agent:node:field form as the factor picker.
  // The connection itself is the selectable field here, so its source type
  // is the final segment.
  const pythonNodeLabel = (source: (typeof pythonSources)[number]) => `${sourceAgentLabel(source.agentNodeId)}:${sourceNodeLabel(source.scriptNodeId)}:Python Script`
  const mcpNodeLabel = (source: McpToolSourceOption) => `${sourceAgentLabel(source.agentNodeId)}:${sourceNodeLabel(source.mcpNodeId)}:MCP Tool`
  const agentNodeLabel = (source: (typeof agentSources)[number]) => `${source.label}:Agent output`
  const selectedNodeLabel = selectedAgentSource
    ? agentNodeLabel(selectedAgentSource)
    : selectedPythonSource
      ? pythonNodeLabel(selectedPythonSource)
      : selectedMcpSource
        ? mcpNodeLabel(selectedMcpSource)
        : 'Select a node…'
  const sourceSelected = producer === 'agent' ? Boolean(selectedAgentSource)
    : producer === 'python' ? Boolean(selectedPythonSource)
      : producer === 'mcp' ? Boolean(selectedMcpSource) : false
  function selectMetricNode(value: string | null) {
    const agentSource = agentSources.find((source) => `agent:${source.agentNodeId}` === value)
    if (agentSource) {
      setProducer('agent')
      setAgentNodeId(agentSource.agentNodeId)
      setPythonSourceKey('')
      setMcpSourceKey('')
      setToolName('')
      return
    }
    const pythonSource = pythonSources.find((source) => `python:${source.key}` === value)
    if (pythonSource) {
      setProducer('python')
      setAgentNodeId('')
      setPythonSourceKey(pythonSource.key)
      setMcpSourceKey('')
      setToolName('')
      return
    }
    const mcpSource = mcpSources.find((source) => `mcp:${source.key}` === value)
    if (mcpSource) {
      setProducer('mcp')
      setAgentNodeId('')
      setPythonSourceKey('')
      setMcpSourceKey(mcpSource.key)
      setToolName('')
      return
    }
    setProducer(undefined)
    setAgentNodeId('')
    setPythonSourceKey('')
    setMcpSourceKey('')
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
  })
  const currentSignature = JSON.stringify({ name, producer, agentNodeId, pythonSourceKey, mcpSourceKey, toolName })
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

  const saveDisabled = Boolean(
    detailError
    || !producer
    || (producer === 'agent' ? agentSourceError : producer === 'python' ? pythonSourceError : mcpSourceError || mcpMappingError),
  )
  const nextMetric = { ...metric, name: name.trim(), description: metric.description, direction: 'neutral' as const, valueType: 'opaque' as const, aggregation: 'none' as const, primary: false }
  const nextConfig: CustomMetricProducerConfig | null = producer === 'agent' && selectedAgentSource
    ? { producer, agentNodeId: selectedAgentSource.agentNodeId }
    : producer === 'mcp' && selectedMcpSource
      ? { producer, source: selectedMcpSource, toolName }
      : producer === 'python'
        ? { producer, sourceKey: pythonSourceKey }
        : null
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
      <p className="mt-1 text-xs text-muted-foreground">Choose an Agent final output or Agent tool result to capture and export.</p>
    </div>

    <fieldset className="space-y-3">
      <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Metric node</legend>
      {!sourceContext ? <div className="space-y-1.5">
        <Label>Node</Label>
        <Select value={selectedMetricNode} onValueChange={selectMetricNode}>
          <SelectTrigger className="w-full" aria-label="Metric node"><SelectValue>{() => selectedNodeLabel}</SelectValue></SelectTrigger>
          <SelectContent>
            <SelectItem value="__none__" disabled>Select a node…</SelectItem>
            {agentSources.map((source) => { const reason = agentSourceDisabledReason(source); return <SelectItem key={`agent:${source.agentNodeId}`} value={`agent:${source.agentNodeId}`} disabled={!!reason}>{agentNodeLabel(source)}{reason ? ` — ${reason}` : ''}</SelectItem> })}
            {pythonSources.map((source) => { const reason = pythonSourceDisabledReason(source); return <SelectItem key={`python:${source.key}`} value={`python:${source.key}`} disabled={!!reason}>{pythonNodeLabel(source)}{reason ? ` — ${reason}` : ''}</SelectItem> })}
            {mcpSources.map((source) => { const reason = mcpSourceDisabledReason(source); return <SelectItem key={`mcp:${source.key}`} value={`mcp:${source.key}`} disabled={!!reason}>{mcpNodeLabel(source)}{reason ? ` — ${reason}` : ''}</SelectItem> })}
          </SelectContent>
        </Select>
      </div> : <>
        <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm">{producer === 'agent' ? 'Agent output' : producer === 'python' ? 'Python Script' : 'MCP Tool'}</p>
        {producer === 'agent' && <div className="space-y-1.5"><Label>Agent source</Label><p className="rounded-md border bg-muted/30 px-3 py-2 text-sm">{selectedAgentSource?.label}</p>{agentSourceError && <p role="alert" className="text-xs text-destructive">{agentSourceError}</p>}</div>}
        {producer === 'python' && <div className="space-y-1.5"><Label>Agent to Python Script source</Label>{pythonSources.length === 1 ? <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm">{selectedPythonSource?.label}</p> : <Select value={pythonSourceKey || '__none__'} onValueChange={(value) => setPythonSourceKey(value === '__none__' ? '' : value ?? '')}><SelectTrigger className="w-full" aria-label="Agent to Python Script source" aria-describedby={pythonSourceError ? 'python-flow-source-error' : undefined}><SelectValue>{() => selectedPythonSource?.label ?? 'Select a direct connection…'}</SelectValue></SelectTrigger><SelectContent><SelectItem value="__none__" disabled>Select a direct connection…</SelectItem>{pythonSources.map((source) => { const reason = pythonSourceDisabledReason(source); return <SelectItem key={source.key} value={source.key} disabled={!!reason}>{source.label}{reason ? ` — ${reason}` : ''}</SelectItem> })}</SelectContent></Select>}{pythonSourceError && <p id="python-flow-source-error" role="alert" className="text-xs text-destructive">{pythonSourceError}</p>}</div>}
        {producer === 'mcp' && <div className="space-y-1.5"><Label>Agent to MCP Tool source</Label>{mcpSources.length === 1 ? <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm">{selectedMcpSource?.label}</p> : <Select value={mcpSourceKey || '__none__'} onValueChange={(value) => { setMcpSourceKey(value === '__none__' ? '' : value ?? ''); setToolName('') }}><SelectTrigger className="w-full" aria-label="Agent to MCP Tool source" aria-describedby={mcpSourceError ? 'mcp-flow-source-error' : undefined}><SelectValue>{() => selectedMcpSource?.label ?? 'Select a direct connection…'}</SelectValue></SelectTrigger><SelectContent><SelectItem value="__none__" disabled>Select a direct connection…</SelectItem>{mcpSources.map((source) => { const reason = mcpSourceDisabledReason(source); return <SelectItem key={source.key} value={source.key} disabled={!!reason}>{source.label}{reason ? ` — ${reason}` : ''}</SelectItem> })}</SelectContent></Select>}{mcpSourceError && <p id="mcp-flow-source-error" role="alert" className="text-xs text-destructive">{mcpSourceError}</p>}</div>}
      </>}
      {producer === 'mcp' && <p role="status" className="text-xs text-muted-foreground">{serversQuery.isPending ? 'Loading registered MCP Servers…' : serversQuery.isError ? 'Registered MCP Servers could not be loaded.' : 'Registered MCP Server availability loaded.'}</p>}
    </fieldset>

    {sourceSelected && <fieldset className="space-y-3">
      <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Metric details</legend>
      <div className="space-y-1.5"><Label htmlFor="python-flow-name">Metric name</Label><Input id="python-flow-name" value={name} aria-invalid={Boolean(detailError)} aria-describedby={detailError ? 'python-flow-name-error' : undefined} onChange={(event) => setName(event.target.value)} />{detailError && <p id="python-flow-name-error" role="alert" className="text-xs text-destructive">{detailError}</p>}</div>
    </fieldset>}

    {sourceSelected && producer === 'mcp' && <div className="space-y-3">
        <div className="space-y-1.5"><Label>MCP tool</Label><Select value={toolName || '__none__'} onValueChange={(value) => setToolName(value === '__none__' ? '' : value ?? '')} disabled={!selectedMcpSource || !!mcpSourceError}><SelectTrigger className="w-full" aria-label="MCP tool"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="__none__" disabled>Select an enabled tool…</SelectItem>{[...new Set([...(selectedMcpSource?.toolNames ?? []), ...(selectedServer?.capabilities?.tools?.map((item) => item.name) ?? [])])].map((name) => { const candidate = selectedServer?.capabilities?.tools?.find((item) => item.name === name); const reason = !selectedMcpSource?.toolNames.includes(name) ? 'disabled for this MCP node' : !candidate ? 'unavailable on server' : undefined; return <SelectItem key={name} value={name} disabled={!!reason}>{name}{reason ? ` — ${reason}` : ''}</SelectItem> })}</SelectContent></Select>{selectedTool?.description && <p className="text-xs text-muted-foreground">{selectedTool.description}</p>}</div>
        {mcpMappingError && <p role="alert" className="text-xs text-destructive">{mcpMappingError}</p>}
        <p className="text-xs text-muted-foreground">ASAREE records this tool's last call result. The Agent decides whether and how often to call it.</p>
    </div>}

    {sourceSelected && producer === 'agent' && <p className="text-xs text-muted-foreground">ASAREE records this Agent's completed final output verbatim.</p>}

    {autosave ? (
      <p role="status" aria-live="polite" className={metricAutosave.status === 'error' ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}>
        {autosaveLabel(submitting ? 'saving' : metricAutosave.status, saveDisabled)}
      </p>
    ) : (
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="ghost" disabled={submitting} onClick={onCancel}>Cancel custom metric</Button>
        <Button type="button" disabled={saveDisabled || submitting} onClick={() => {
          if (nextConfig) onSave(nextMetric, nextConfig)
        }}>{submitting ? editing ? 'Saving…' : 'Creating…' : editing ? 'Save custom metric' : 'Add custom metric'}</Button>
      </div>
    )}
  </section>
}
