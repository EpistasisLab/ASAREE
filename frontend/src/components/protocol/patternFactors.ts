import type { Node, Edge } from '@xyflow/react'
import { defaultReasonActPatternNodeData, defaultSingleAgentBaselinePatternNodeData } from '@/types/protocols'
import { bindableFieldsForNode } from './bindableFields'
import { sharedGroupConflict } from '@/lib/sharedFactors'
import type { ProtocolGraph } from '@/types/protocols'

export const PATTERN_FACTOR_PATH = 'pattern_override'
export const isPatternNode = (type?: string) => type === 'pattern_reason_act' || type === 'pattern_single_agent_baseline'

export function patternFactorFields(patternNodeId: string, nodes: Node[], edges: Edge[], agentId?: string) {
  const node = nodes.find((node) => node.id === patternNodeId)!
  const owner = nodes.find((candidate) => ['agent', 'sub_agent'].includes(candidate.type ?? '') && (!agentId || candidate.id === agentId) && edges.some((edge) => edge.source === node.id && edge.target === candidate.id && edge.targetHandle === 'architectural_pattern'))
  const reasonAct = node.type === 'pattern_reason_act'
  const slug = reasonAct ? 'reason_act' : 'single_agent_baseline'
  const alternateSlug = reasonAct ? 'single_agent_baseline' : 'reason_act'
  return [
    { nodeId: owner?.id ?? '', fieldPath: PATTERN_FACTOR_PATH, label: 'Pattern levels', levelType: 'pattern' as const,
      currentValue: { execution_pattern: slug, pattern_params: { [slug]: node.data.config } },
      alternateValue: { execution_pattern: alternateSlug, pattern_params: { [alternateSlug]: reasonAct ? defaultSingleAgentBaselinePatternNodeData().config : defaultReasonActPatternNodeData().config } } },
    ...bindableFieldsForNode(node).map((field) => ({ ...field, nodeId: node.id, currentValue: (node.data.config as Record<string, unknown>)[field.fieldPath.slice(7)], alternateValue: undefined })),
  ]
}

export function patternFactorConflict(patternNodeId: string, path: string, nodes: Node[], edges: Edge[], agentId?: string): string | undefined {
  if (path === PATTERN_FACTOR_PATH) {
    const conflict = sharedGroupConflict({ nodes, edges } as unknown as ProtocolGraph, patternNodeId, 'pattern')
    if (conflict) return conflict
  }
  const node = nodes.find((node) => node.id === patternNodeId)
  const owners = nodes.filter((candidate) => ['agent', 'sub_agent'].includes(candidate.type ?? '') && edges.some((edge) => edge.source === patternNodeId && edge.target === candidate.id && edge.targetHandle === 'architectural_pattern'))
  if (path === PATTERN_FACTOR_PATH && !owners.some((owner) => !agentId || owner.id === agentId)) return 'Connect this Pattern to an Agent before creating Pattern levels.'
  const bindings = node?.data.factor_bindings as Record<string, string> | undefined
  const conflict = path === PATTERN_FACTOR_PATH
    ? Object.keys(bindings ?? {}).some((key) => key.startsWith('config.'))
    : owners.some((owner) => (owner.data.factor_bindings as Record<string, string> | undefined)?.[PATTERN_FACTOR_PATH])
  return conflict ? 'Pattern levels cannot be combined with individual parameter factors. Select the existing factor type and remove its binding first.' : undefined
}
