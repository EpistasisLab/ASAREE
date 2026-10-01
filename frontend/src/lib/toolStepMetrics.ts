import type { DesignMetric, MeasurementPlan } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { projectionConfig, type FieldProjection } from './metricFields'

export const TOOL_STEP_PRODUCER_ID = 'asaree.tool_step'

export interface ToolStepSourceOption {
  nodeId: string
  label: string
  disabledReason?: string
}

export function toolStepSourceOptions(graph: ProtocolGraph | undefined): ToolStepSourceOption[] {
  if (!graph) return []
  return graph.nodes.flatMap((node) => {
    if (node.type !== 'tool_step') return []
    const disabledReason = node.data.active === false ? 'Tool Step is disabled.' : undefined
    return [{ nodeId: node.id, label: node.data.label || node.id, disabledReason }]
  })
}

export function toolStepBindingForMetric(plan: MeasurementPlan | null, metricId: string | undefined) {
  if (!plan || !metricId) return undefined
  return plan.producers.find((producer) =>
    producer.producer_id === TOOL_STEP_PRODUCER_ID && Object.values(producer.outputs).includes(metricId),
  )
}

export function upsertToolStepMetric(
  plan: MeasurementPlan | null,
  metric: DesignMetric,
  nodeId: string,
  projection: FieldProjection | undefined,
): MeasurementPlan {
  if (!metric.id) throw new Error('A Tool Step metric needs a stable id.')
  const current = plan ?? { metrics: [], producers: [], inputs: [] }
  const bindingId = `tool-step-${metric.id}`
  return {
    metrics: [...current.metrics.filter((definition) => definition.id !== metric.id), { id: metric.id, name: metric.name }],
    producers: [
      ...current.producers.filter((producer) => producer.id !== bindingId),
      {
        id: bindingId,
        producer_id: TOOL_STEP_PRODUCER_ID,
        kind: 'reported',
        outputs: { value: metric.id },
        artifacts: [],
        config: { node_id: nodeId, ...projectionConfig(projection) },
      },
    ],
    inputs: current.inputs.filter((input) => input.producer_binding_id !== bindingId),
  }
}
