import type { DesignMetric, MeasurementPlan } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

export const MCP_TOOL_PRODUCER_ID = 'asaree.mcp_tool'
const MCP_TOOL_NODE_TYPES = new Set(['mcp_tool', 'mcp_scikit_learn', 'mcp_client_tool'])

export function isMcpToolNodeType(type: string): boolean {
  return MCP_TOOL_NODE_TYPES.has(type)
}

export interface McpToolSourceOption {
  key: string
  agentNodeId: string
  mcpNodeId: string
  serverId: string
  toolNames: string[]
  label: string
  disabledReason?: string
}

export function mcpToolSourceOptions(graph: ProtocolGraph | undefined): McpToolSourceOption[] {
  if (!graph) return []
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]))
  return graph.edges.flatMap((edge) => {
    if (edge.targetHandle !== 'tool') return []
    const agent = nodes.get(edge.target); const tool = nodes.get(edge.source)
    if (!agent || !tool || !['agent', 'sub_agent'].includes(agent.type) || !isMcpToolNodeType(tool.type)) return []
    const config = 'config' in tool.data ? tool.data.config : undefined
    const serverId = config && 'server_id' in config ? config.server_id : null
    const toolNames = config && 'tool_names' in config && Array.isArray(config.tool_names) ? config.tool_names : []
    const disabledReason = config && 'enabled' in config && config.enabled === false ? 'MCP Tool is disabled.'
      : !serverId ? 'MCP Server is not configured.' : toolNames.length === 0 ? 'MCP Tool has no enabled tools.' : undefined
    return [{ key: JSON.stringify([agent.id, tool.id]), agentNodeId: agent.id, mcpNodeId: tool.id, serverId: serverId ?? '', toolNames, label: `${agent.data.label || agent.id} → ${tool.data.label || tool.id}`, disabledReason }]
  })
}

export function mcpToolBindingForMetric(plan: MeasurementPlan | null, metricId: string | undefined) {
  if (!metricId) return undefined
  return plan?.producers.find((producer) => producer.producer_id === MCP_TOOL_PRODUCER_ID && Object.values(producer.outputs).includes(metricId))
}

export function upsertMcpToolMetric(plan: MeasurementPlan | null, metric: DesignMetric, source: McpToolSourceOption | undefined, toolName = ''): MeasurementPlan {
  if (!metric.id) throw new Error('An MCP Tool metric needs a stable id.')
  const current = plan ?? { metrics: [], producers: [], inputs: [] }; const id = `mcp-${metric.id}`
  return { metrics: [...current.metrics.filter((item) => item.id !== metric.id), { id: metric.id, name: metric.name, value_type: 'opaque', direction: 'neutral', aggregation: 'none', primary: false, description: metric.description, unit: metric.unit }], producers: [...current.producers.filter((item) => item.id !== id), { id, producer_id: MCP_TOOL_PRODUCER_ID, kind: 'reported', outputs: { value: metric.id }, artifacts: [], config: { agent_node_id: source?.agentNodeId ?? '', mcp_node_id: source?.mcpNodeId ?? '', server_id: source?.serverId ?? '', tool_name: toolName } }], inputs: current.inputs.filter((item) => item.producer_binding_id !== id) }
}
