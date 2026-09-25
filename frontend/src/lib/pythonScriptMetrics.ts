import type { DesignMetric, MeasurementPlan } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

export interface PythonScriptSourceOption {
  key: string
  agentNodeId: string
  scriptNodeId: string
  label: string
  disabledReason?: string
}

export function pythonScriptSourceOptions(graph: ProtocolGraph | undefined): PythonScriptSourceOption[] {
  if (!graph) return []
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]))
  return graph.edges.flatMap((edge) => {
    if (edge.targetHandle !== 'tool') return []
    const agent = nodes.get(edge.target)
    const script = nodes.get(edge.source)
    if (!agent || !script || !['agent', 'sub_agent'].includes(agent.type) || script.type !== 'script') return []
    const config = 'config' in script.data ? script.data.config : undefined
    const code = config && 'code' in config ? config.code : ''
    const enabled = !(config && 'enabled' in config && config.enabled === false)
    const disabledReason = !enabled ? 'Script is disabled.' : !String(code ?? '').trim() ? 'Script has no code.' : undefined
    return [{
      key: JSON.stringify([agent.id, script.id]),
      agentNodeId: agent.id,
      scriptNodeId: script.id,
      label: `${agent.data.label || agent.id} → ${script.data.label || script.id}`,
      disabledReason,
    }]
  })
}

export function pythonScriptBindingForMetric(plan: MeasurementPlan | null, metricId: string | undefined) {
  if (!plan || !metricId) return undefined
  return plan.producers.find((producer) =>
    producer.producer_id === 'asaree.python_script' && Object.values(producer.outputs).includes(metricId),
  )
}

export function upsertPythonScriptMetric(
  plan: MeasurementPlan | null,
  metric: DesignMetric,
  source: Pick<PythonScriptSourceOption, 'agentNodeId' | 'scriptNodeId'> | undefined,
): MeasurementPlan {
  if (!metric.id) throw new Error('A Python Script metric needs a stable id.')
  const current = plan ?? { metrics: [], producers: [], inputs: [] }
  const bindingId = `python-${metric.id}`
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
        producer_id: 'asaree.python_script',
        kind: 'reported',
        outputs: { value: metric.id },
        artifacts: [],
        config: {
          agent_node_id: source?.agentNodeId ?? '',
          script_node_id: source?.scriptNodeId ?? '',
        },
      },
    ],
    inputs: current.inputs.filter((input) => input.producer_binding_id !== bindingId),
  }
}
