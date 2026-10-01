import type { DesignMetric, MeasurementPlan } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

// Runtime totals over only the runs that chosen canvas nodes launched --
// `runtime_metrics.NodeRuntimeMetricProducer`. Several nodes sum into one
// metric (e.g. every Critic Gate as one "critic" stage). One binding per
// metric, so editing or deleting one never touches another.
export const NODE_RUNTIME_PRODUCER_ID = 'asaree.node_runtime'

export interface NodeRuntimeOutput {
  key: string
  label: string
  unit?: string
  aggregation: 'sum' | 'mean'
}

// Mirrors `NodeRuntimeMetricProducer.output_keys`.
export const NODE_RUNTIME_OUTPUTS: NodeRuntimeOutput[] = [
  { key: 'total_tokens', label: 'Total tokens', unit: 'tokens', aggregation: 'sum' },
  { key: 'input_tokens', label: 'Input tokens', unit: 'tokens', aggregation: 'sum' },
  { key: 'output_tokens', label: 'Output tokens', unit: 'tokens', aggregation: 'sum' },
  { key: 'cache_read_tokens', label: 'Cache-read tokens', unit: 'tokens', aggregation: 'sum' },
  { key: 'cache_creation_tokens', label: 'Cache-creation tokens', unit: 'tokens', aggregation: 'sum' },
  { key: 'thinking_tokens', label: 'Thinking tokens', unit: 'tokens', aggregation: 'sum' },
  { key: 'agent_loop_iterations', label: 'Turns', unit: 'turns', aggregation: 'sum' },
  { key: 'agent_runs', label: 'Runs', unit: 'runs', aggregation: 'sum' },
  { key: 'tool_calls', label: 'Tool calls', unit: 'calls', aggregation: 'sum' },
  { key: 'tool_error_rate', label: 'Tool error rate', aggregation: 'mean' },
  { key: 'capped_agent_runs', label: 'Runs that hit the iteration cap', unit: 'runs', aggregation: 'sum' },
  { key: 'cost_usd', label: 'Cost', unit: 'USD', aggregation: 'sum' },
]

const NODE_RUNTIME_NODE_TYPES = new Set(['agent', 'sub_agent', 'critic_gate'])

export interface NodeRuntimeSourceOption {
  nodeId: string
  label: string
  type: 'Agent' | 'Critic Gate'
  disabledReason?: string
}

export function nodeRuntimeSourceOptions(graph: ProtocolGraph | undefined): NodeRuntimeSourceOption[] {
  if (!graph) return []
  return graph.nodes.flatMap((node) => {
    if (!NODE_RUNTIME_NODE_TYPES.has(node.type)) return []
    return [{
      nodeId: node.id,
      label: node.data.label || node.id,
      type: node.type === 'critic_gate' ? 'Critic Gate' as const : 'Agent' as const,
      disabledReason: node.data.active === false ? 'Disabled.' : undefined,
    }]
  })
}

export function nodeRuntimeBindingForMetric(plan: MeasurementPlan | null, metricId: string | undefined) {
  if (!plan || !metricId) return undefined
  return plan.producers.find((producer) =>
    producer.producer_id === NODE_RUNTIME_PRODUCER_ID && Object.values(producer.outputs).includes(metricId),
  )
}

export function bindingNodeIds(binding: MeasurementPlan['producers'][number] | undefined): string[] {
  const nodeIds = binding?.config.node_ids
  return Array.isArray(nodeIds) ? nodeIds.filter((id): id is string => typeof id === 'string' && !!id) : []
}

export function nodeRuntimeOutput(key: string | undefined): NodeRuntimeOutput | undefined {
  return NODE_RUNTIME_OUTPUTS.find((output) => output.key === key)
}

export function upsertNodeRuntimeMetric(
  plan: MeasurementPlan | null,
  metric: DesignMetric,
  nodeIds: string[],
  outputKey: string,
): MeasurementPlan {
  if (!metric.id) throw new Error('A node runtime metric needs a stable id.')
  const output = nodeRuntimeOutput(outputKey)
  if (!output) throw new Error(`Unknown node runtime output ${outputKey}.`)
  const current = plan ?? { metrics: [], producers: [], inputs: [] }
  const bindingId = `node-runtime-${metric.id}`
  return {
    metrics: [
      ...current.metrics.filter((definition) => definition.id !== metric.id),
      {
        id: metric.id,
        name: metric.name,
        value_type: 'number',
        aggregation: output.aggregation,
        ...(output.unit ? { unit: output.unit } : {}),
      },
    ],
    producers: [
      ...current.producers.filter((producer) => producer.id !== bindingId),
      {
        id: bindingId,
        producer_id: NODE_RUNTIME_PRODUCER_ID,
        kind: 'runtime',
        outputs: { [outputKey]: metric.id },
        artifacts: [],
        config: { node_ids: nodeIds },
      },
    ],
    inputs: [
      ...current.inputs.filter((input) => input.producer_binding_id !== bindingId),
      { producer_binding_id: bindingId, input_key: 'facts', source_key: 'attempt.runtime' },
    ],
  }
}
