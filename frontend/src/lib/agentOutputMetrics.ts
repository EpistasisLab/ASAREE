import type { DesignMetric, MeasurementPlan } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

export const AGENT_OUTPUT_PRODUCER_ID = 'asaree.agent_output'

export interface AgentOutputSourceOption {
  agentNodeId: string
  label: string
  disabledReason?: string
}

export function agentOutputSourceOptions(graph: ProtocolGraph | undefined): AgentOutputSourceOption[] {
  if (!graph) return []
  return graph.nodes.flatMap((node) => {
    if (node.type !== 'agent') return []
    const disabledReason = node.data.active === false ? 'Agent is disabled.' : undefined
    return [{ agentNodeId: node.id, label: node.data.label || node.id, disabledReason }]
  })
}

export function agentOutputBindingForMetric(plan: MeasurementPlan | null, metricId: string | undefined) {
  if (!plan || !metricId) return undefined
  return plan.producers.find((producer) =>
    producer.producer_id === AGENT_OUTPUT_PRODUCER_ID && Object.values(producer.outputs).includes(metricId),
  )
}

export function upsertAgentOutputMetric(
  plan: MeasurementPlan | null,
  metric: DesignMetric,
  agentNodeId: string,
): MeasurementPlan {
  if (!metric.id) throw new Error('An Agent output metric needs a stable id.')
  const current = plan ?? { metrics: [], producers: [], inputs: [] }
  const bindingId = `agent-${metric.id}`
  return {
    metrics: [
      ...current.metrics.filter((definition) => definition.id !== metric.id),
      {
        id: metric.id,
        name: metric.name,
        value_type: 'opaque',
        direction: 'neutral',
        aggregation: 'none',
        primary: false,
        description: metric.description,
        unit: metric.unit,
      },
    ],
    producers: [
      ...current.producers.filter((producer) => producer.id !== bindingId),
      {
        id: bindingId,
        producer_id: AGENT_OUTPUT_PRODUCER_ID,
        kind: 'reported',
        outputs: { value: metric.id },
        artifacts: [],
        config: { agent_node_id: agentNodeId },
      },
    ],
    inputs: current.inputs.filter((input) => input.producer_binding_id !== bindingId),
  }
}
