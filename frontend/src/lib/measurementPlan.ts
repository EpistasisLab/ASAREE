import type { DesignMetric, MeasurementPlan, ObservationStatus } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { metricCatalogEntry, type MetricCatalogEntry } from './metricCatalog'
import { AGENT_OUTPUT_PRODUCER_ID } from './agentOutputMetrics'
import { MCP_TOOL_PRODUCER_ID } from './mcpToolMetrics'
import { NODE_RUNTIME_PRODUCER_ID } from './nodeRuntimeMetrics'
import { TOOL_STEP_PRODUCER_ID } from './toolStepMetrics'

export type MetricReadiness = {
  ready: boolean
  producer: string
  detail: string
}

export const OBSERVATION_LABELS: Record<ObservationStatus, string> = {
  measured: 'Measured',
  unavailable: 'Unavailable',
  failed: 'Failed',
  timed_out: 'Timed out',
  cancelled: 'Cancelled',
  not_applicable: 'Not applicable',
}

// Producers ASAREE computes in code from what a run recorded
// (services/derived_metrics.py). A Tool Step metric is authored through the
// custom-metric dialog; the feature pipeline is declared in the plan document.
export { NODE_RUNTIME_PRODUCER_ID, TOOL_STEP_PRODUCER_ID }
export const FEATURE_PIPELINE_PRODUCER_ID = 'asaree.feature_pipeline'
export const DERIVED_PRODUCER_IDS = new Set([TOOL_STEP_PRODUCER_ID, FEATURE_PIPELINE_PRODUCER_ID])
const FEATURE_PIPELINE_STAGE_KEYS = ['dc_node_id', 'fte_node_id', 'fs_node_id'] as const

const PRODUCER_LABELS: Record<string, string> = {
  'asaree.runtime': 'ASAREE runtime',
  'asaree.python_script': 'Python Script',
  [AGENT_OUTPUT_PRODUCER_ID]: 'Agent output',
  [MCP_TOOL_PRODUCER_ID]: 'MCP Tool',
  [TOOL_STEP_PRODUCER_ID]: 'Tool Step',
  [FEATURE_PIPELINE_PRODUCER_ID]: 'Feature pipeline',
  [NODE_RUNTIME_PRODUCER_ID]: 'Node runtime',
}

export function removeMetricFromMeasurementPlan(
  plan: MeasurementPlan | null,
  metricId: string | undefined,
): MeasurementPlan | null {
  if (!plan || !metricId) return plan
  const producers = plan.producers.flatMap((producer) => {
    const outputs = Object.fromEntries(Object.entries(producer.outputs).filter(([, id]) => id !== metricId))
    if (!Object.keys(outputs).length) return []
    // A field projection belongs to its output key, so it leaves with it.
    const projections = producer.config.projections
    const config = projections && typeof projections === 'object'
      ? { ...producer.config, projections: Object.fromEntries(Object.entries(projections).filter(([key]) => key in outputs)) }
      : producer.config
    return [{ ...producer, outputs, config }]
  })
  const producerIds = new Set(producers.map((producer) => producer.id))
  return {
    metrics: plan.metrics.filter((metric) => metric.id !== metricId),
    producers,
    inputs: plan.inputs.filter((input) => producerIds.has(input.producer_binding_id)),
  }
}

export function producerLabel(producerId: string | undefined): string {
  return producerId ? (PRODUCER_LABELS[producerId] ?? producerId) : 'No producer'
}

export function upsertRuntimeMetric(plan: MeasurementPlan | null, metric: DesignMetric): MeasurementPlan {
  if (!metric.id || !metric.catalogKey) throw new Error('A runtime metric needs an id and catalog key.')
  const current = plan ?? { metrics: [], producers: [], inputs: [] }
  const existing = current.producers.find((producer) => producer.producer_id === 'asaree.runtime')
  let bindingId = existing?.id
  if (!bindingId) {
    bindingId = 'runtime'
    for (let suffix = 2; current.producers.some((producer) => producer.id === bindingId); suffix += 1) {
      bindingId = `runtime-${suffix}`
    }
  }
  return {
    metrics: [
      ...current.metrics.filter((definition) => definition.id !== metric.id),
      {
        id: metric.id,
        name: metric.name,
        value_type: 'number',
        direction: metric.direction,
        aggregation: metric.aggregation ?? 'mean',
        primary: metric.primary,
        description: metric.description,
        unit: metric.unit,
      },
    ],
    producers: [
      ...current.producers.filter((producer) => producer.id !== bindingId),
      {
        id: bindingId,
        producer_id: 'asaree.runtime',
        kind: 'runtime',
        outputs: { ...existing?.outputs, [metric.catalogKey]: metric.id },
        artifacts: existing?.artifacts ?? [],
        config: existing?.config ?? {},
      },
    ],
    inputs: [
      ...current.inputs.filter((input) => input.producer_binding_id !== bindingId),
      { producer_binding_id: bindingId, input_key: 'facts', source_key: 'attempt.runtime' },
    ],
  }
}

/** Immediate draft hint only; the backend measurement engine remains authoritative. */
export function localMetricReadinessPreview(
  metric: DesignMetric,
  plan: MeasurementPlan | null,
  graph: ProtocolGraph | undefined,
): MetricReadiness {
  if (!metric.id || !plan?.metrics.some((definition) => definition.id === metric.id)) {
    return { ready: false, producer: 'No producer', detail: 'No producer is bound to this metric.' }
  }
  const bindings = plan.producers.filter((producer) => Object.values(producer.outputs).includes(metric.id!))
  if (bindings.length === 0) return { ready: false, producer: 'No producer', detail: 'No producer is bound to this metric.' }
  if (bindings.length > 1) return { ready: false, producer: 'Multiple producers', detail: 'More than one producer is bound to this metric.' }
  const binding = bindings[0]
  const producer = producerLabel(binding.producer_id)
  if (binding.producer_id === AGENT_OUTPUT_PRODUCER_ID) {
    const agentNodeId = String(binding.config.agent_node_id ?? '')
    const agent = graph?.nodes.find((node) => node.id === agentNodeId && (node.type === 'agent' || node.type === 'sub_agent'))
    if (!agent) return { ready: false, producer, detail: `Agent ${agentNodeId || '(not selected)'} is not available.` }
    if (agent.data.active === false) return { ready: false, producer, detail: 'The Agent is disabled.' }
    return { ready: true, producer, detail: 'The Agent final output will be captured after execution.' }
  }
  if (binding.producer_id === TOOL_STEP_PRODUCER_ID) {
    const step = graph?.nodes.find((node) => node.id === binding.config.node_id && node.type === 'tool_step')
    if (!step) return { ready: false, producer, detail: 'The Tool Step is unavailable.' }
    if (step.data.active === false) return { ready: false, producer, detail: 'The Tool Step is disabled.' }
    return { ready: true, producer, detail: "The Tool Step's result will be read after execution." }
  }
  if (binding.producer_id === FEATURE_PIPELINE_PRODUCER_ID) {
    const missing = FEATURE_PIPELINE_STAGE_KEYS.find((key) => !graph?.nodes.some(
      (node) => node.id === binding.config[key] && (node.type === 'agent' || node.type === 'sub_agent'),
    ))
    if (missing) return { ready: false, producer, detail: `The ${missing.split('_')[0].toUpperCase()} Agent is unavailable.` }
    return { ready: true, producer, detail: 'Computed from the stage payloads after execution.' }
  }
  if (binding.producer_id === NODE_RUNTIME_PRODUCER_ID) {
    const nodeIds = Array.isArray(binding.config.node_ids) ? binding.config.node_ids.map(String) : []
    if (nodeIds.length === 0) return { ready: false, producer, detail: 'No node is selected to measure.' }
    const missing = nodeIds.filter((id) => !graph?.nodes.some((node) => node.id === id))
    if (missing.length) return { ready: false, producer, detail: `Node ${missing.join(', ')} is not on the canvas.` }
  }
  if (binding.kind === 'reported') {
    const graphNodes = graph?.nodes ?? []
    const graphEdges = graph?.edges ?? []
    const scriptNodeId = String(binding.config.script_node_id ?? '')
    if (binding.producer_id !== MCP_TOOL_PRODUCER_ID
      && !graphNodes.some((node) => node.id === scriptNodeId && node.type === 'script')) {
      return { ready: false, producer, detail: `Model Script node ${scriptNodeId || '(not selected)'} is not available.` }
    }
    if (binding.producer_id === 'asaree.python_script') {
      const agentNodeId = String(binding.config.agent_node_id ?? '')
      const script = graphNodes.find((node) => node.id === scriptNodeId && node.type === 'script')
      const config = script && 'config' in script.data ? script.data.config : undefined
      if (!graphNodes.some((node) => node.id === agentNodeId && (node.type === 'agent' || node.type === 'sub_agent'))) {
        return { ready: false, producer, detail: `Source Agent ${agentNodeId || '(not selected)'} is not available.` }
      }
      if (!graphEdges.some((edge) => edge.source === scriptNodeId && edge.target === agentNodeId && edge.targetHandle === 'tool')) {
        return { ready: false, producer, detail: 'The source Agent and Python Script are not directly connected.' }
      }
      if (config && 'enabled' in config && config.enabled === false) {
        return { ready: false, producer, detail: 'The Python Script is disabled.' }
      }
      if (!config || !('code' in config) || !String(config.code ?? '').trim()) {
        return { ready: false, producer, detail: 'The Python Script has no code.' }
      }
    }
    if (binding.producer_id === MCP_TOOL_PRODUCER_ID) {
      const agentNodeId = String(binding.config.agent_node_id ?? '')
      const toolNodeId = String(binding.config.mcp_node_id ?? '')
      const tool = graph?.nodes.find((node) => node.id === toolNodeId && ['mcp_tool', 'mcp_scikit_learn', 'mcp_client_tool'].includes(node.type))
      const config = tool && 'config' in tool.data ? tool.data.config as unknown as Record<string, unknown> : undefined
      if (!graph?.nodes.some((node) => node.id === agentNodeId && (node.type === 'agent' || node.type === 'sub_agent'))) return { ready: false, producer, detail: 'The source Agent is unavailable.' }
      if (!tool || !config) return { ready: false, producer, detail: 'The MCP Tool is unavailable.' }
      if (!graph?.edges.some((edge) => edge.source === toolNodeId && edge.target === agentNodeId && edge.targetHandle === 'tool')) return { ready: false, producer, detail: 'The source Agent and MCP Tool are not directly connected.' }
      if (config.enabled === false) return { ready: false, producer, detail: 'The MCP Tool is disabled.' }
      if (binding.config.server_id !== config.server_id) return { ready: false, producer, detail: 'The MCP Server configuration has changed.' }
      if (typeof binding.config.tool_name !== 'string' || !Array.isArray(config.tool_names) || !config.tool_names.includes(binding.config.tool_name)) return { ready: false, producer, detail: 'The selected MCP tool is no longer enabled.' }
    }
  }
  if (binding.kind === 'runtime'
    && !plan.inputs.some((input) => input.producer_binding_id === binding.id && input.input_key === 'facts')) {
    return { ready: false, producer, detail: 'Missing required facts input binding.' }
  }
  return { ready: true, producer, detail: 'Definition and producer are ready.' }
}

export function metricMetadata(metric: DesignMetric): string {
  if (metric.kind === 'custom') return 'captured output'
  const catalog = metricCatalogEntry(metric.catalogKey)
  const unit = metric.unit || catalog?.unit || 'unitless'
  const aggregation = metric.valueType === 'boolean' ? 'rate' : (metric.aggregation ?? catalog?.aggregation ?? 'mean')
  return `${unit} · ${aggregation}`
}

export function catalogRequiredInputs(catalog: MetricCatalogEntry): string {
  if (catalog.kind === 'runtime') return 'Requires completed-attempt runtime facts.'
  return 'Captured from the configured source during normal protocol execution.'
}
