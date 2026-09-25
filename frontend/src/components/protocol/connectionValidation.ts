import { CONNECTOR_HANDLES } from '@/lib/coordinationStrategy'
import { MCP_TOOL_NODE_TYPES } from './mcpServerCatalog'
import type { Connection, Edge, Node } from '@xyflow/react'

export const MODEL_NODE_TYPES = [
  'model_anthropic',
  'model_openai',
  'model_azure_foundry',
  'model_openrouter',
  'model_local',
]
export const PATTERN_NODE_TYPES = ['pattern_reason_act', 'pattern_single_agent_baseline']
export const KNOWLEDGE_NODE_TYPES = ['okf_bundle', 'okf_document']

// These slots represent one scalar configuration on their target node. Keep
// this aligned with protocol_execution.py's backend cardinality checks. The
// remaining named slots are collections and deliberately accept many edges.
const SINGLE_CAPACITY_SLOTS = new Set([
  'model',
  'memory',
  'architectural_pattern',
  'output_parser',
])

export function isProtocolConnectionValid(
  connection: Edge | Connection,
  nodes: Node[],
  edges: Edge[],
  isSequential: boolean,
): boolean {
  const sourceNode = nodes.find((node) => node.id === connection.source)
  const targetNode = nodes.find((node) => node.id === connection.target)
  if (!sourceNode || !targetNode) return false

  if (
    connection.targetHandle &&
    SINGLE_CAPACITY_SLOTS.has(connection.targetHandle) &&
    edges.some(
      (edge) => edge.target === connection.target && edge.targetHandle === connection.targetHandle,
    )
  ) {
    return false
  }

  const targetIsAgentLike = targetNode.type === 'agent' || targetNode.type === 'sub_agent'
  switch (connection.targetHandle) {
    case 'model':
      return (
        MODEL_NODE_TYPES.includes(sourceNode.type ?? '') &&
        (targetIsAgentLike || targetNode.type === 'critic_gate')
      )
    case 'tool':
      return (
        (MCP_TOOL_NODE_TYPES.includes(sourceNode.type ?? '') || sourceNode.type === 'script') &&
        targetIsAgentLike
      )
    case 'memory':
      return sourceNode.type === 'memory' && targetIsAgentLike
    case 'output_parser':
      return sourceNode.type === 'output_parser' && targetIsAgentLike
    case 'architectural_pattern':
      return PATTERN_NODE_TYPES.includes(sourceNode.type ?? '') && targetIsAgentLike
    case 'skill':
      return sourceNode.type === 'skill' && targetIsAgentLike
    case 'dataset':
      return sourceNode.type === 'dataset' && targetIsAgentLike
    case 'knowledge':
      return KNOWLEDGE_NODE_TYPES.includes(sourceNode.type ?? '') && targetIsAgentLike
    case 'sub_agents':
      return (
        sourceNode.type === 'sub_agent' &&
        targetNode.type === 'agent' &&
        !edges.some((edge) => edge.source === sourceNode.id && edge.targetHandle === 'sub_agents')
      )
    default: {
      const sourceCanFeedMainFlow =
        !MODEL_NODE_TYPES.includes(sourceNode.type ?? '') &&
        sourceNode.type !== 'memory' &&
        sourceNode.type !== 'output_parser' &&
        !MCP_TOOL_NODE_TYPES.includes(sourceNode.type ?? '') &&
        sourceNode.type !== 'dataset' &&
        sourceNode.type !== 'skill' &&
        !KNOWLEDGE_NODE_TYPES.includes(sourceNode.type ?? '') &&
        sourceNode.type !== 'script' &&
        !PATTERN_NODE_TYPES.includes(sourceNode.type ?? '') &&
        sourceNode.type !== 'sub_agent' &&
        targetNode.type !== 'sub_agent'
      if (!sourceCanFeedMainFlow) return false
      if (!isSequential) return true
      return (
        !edges.some((edge) => edge.source === connection.source && !CONNECTOR_HANDLES.has(edge.targetHandle ?? '')) &&
        !edges.some((edge) => edge.target === connection.target && !CONNECTOR_HANDLES.has(edge.targetHandle ?? ''))
      )
    }
  }
}
