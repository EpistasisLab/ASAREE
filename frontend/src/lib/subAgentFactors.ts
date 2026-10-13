import { sharedFactorGroup, sharedGroupConflict, sharedGroupIssues } from './sharedFactors'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'

export const SUB_AGENT_FACTOR_PATH = 'sub_agent_selection'

export const subAgentFactorModes = {
  boolean: 'This sub-agent on/off',
  sub_agent_selection: 'Sub-Agent levels',
  sub_agent_toggle: 'All sub-agents on/off',
}
export type SubAgentFactorMode = keyof typeof subAgentFactorModes

export function subAgentFactorConflict(graph: ProtocolGraph, factors: DesignFactor[], subAgentNodeId: string, mode: SubAgentFactorMode, agentId: string): string | undefined {
  if (mode !== 'boolean') {
    const groupConflict = sharedGroupConflict(graph, subAgentNodeId, 'sub_agent')
    if (groupConflict) return groupConflict
  }
  const owner = graph.nodes.find((node) => node.id === (mode === 'boolean' ? subAgentNodeId : agentId))
  const currentName = owner?.data.factor_bindings?.[mode === 'boolean' ? 'active' : SUB_AGENT_FACTOR_PATH]
  const current = factors.find((factor) => factor.name === currentName)
  if (current && (current.level_type ?? 'boolean') !== mode) {
    return `${subAgentFactorModes[mode]} is unavailable because this shared group already uses the factor ${current.name} (${subAgentFactorModes[(current.level_type ?? 'boolean') as SubAgentFactorMode]}). Keep the current factor type, or select its factor type and remove it first.`
  }
  const conflicts = [...new Set(mode === 'boolean'
    ? graph.edges.filter((edge) => edge.source === subAgentNodeId && edge.targetHandle === 'sub_agents')
      .flatMap((edge) => {
        const name = graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[SUB_AGENT_FACTOR_PATH]
        return name ? [name] : []
      })
    : connectedSubAgents(graph, agentId).flatMap((node) => node.data.factor_bindings?.active ? [node.data.factor_bindings.active] : []))]
  if (!conflicts.length) return undefined
  return mode === 'boolean'
    ? `${subAgentFactorModes[mode]} is unavailable because a connected Agent already controls this Sub-Agent with the factor ${conflicts.join(', ')}. Use the existing shared factor, or select its factor type and remove it first.`
    : `${subAgentFactorModes[mode]} is unavailable because connected Sub-Agents already have factor bindings: ${conflicts.join(', ')}. Shared sub-agent factors cannot be combined with individual Sub-Agent on/off factor bindings. Use This sub-agent on/off, or remove those bindings in each Sub-Agent’s factor dialog first.`
}

export function isSubAgentFactor(factor: DesignFactor): boolean {
  return factor.level_type === 'sub_agent_selection' || factor.level_type === 'sub_agent_toggle'
}

export function connectedSubAgents(graph: ProtocolGraph, agentId: string): ProtocolNode[] {
  return graph.edges.filter((edge) => edge.target === agentId && edge.targetHandle === 'sub_agents')
    .flatMap((edge) => {
      const node = graph.nodes.find((node) => node.id === edge.source && node.type === 'sub_agent')
      return node ? [node] : []
    })
}

export function subAgentId(node: ProtocolNode): string {
  return node.id
}

export function reconcileSubAgentFactor(factor: DesignFactor, graph: ProtocolGraph, agentId: string): DesignFactor {
  if (sharedGroupIssues(graph, [factor], 'sub_agent').length) return factor
  const source = connectedSubAgents(graph, agentId)[0]
  if (source && sharedFactorGroup(graph, source.id, 'sub_agent').error) return factor
  const subAgents = connectedSubAgents(graph, agentId)
  const ids = [...new Set(subAgents.map(subAgentId))]
  if (factor.level_type === 'sub_agent_toggle') {
    const noneFirst = factor.levels.length === 2 && Array.isArray(factor.levels[0]) && factor.levels[0].length === 0
    return {
      ...factor,
      levels: noneFirst ? [[], ids] : [ids, []],
      level_labels: factor.level_labels?.length === 2 ? factor.level_labels : noneFirst ? ['All disabled', 'All enabled'] : ['All enabled', 'All disabled'],
    }
  }
  const oldIds = factor.levels.map((level) => Array.isArray(level) ? String(level[0] ?? '') : '')
  const ordered = [...new Set([...oldIds.filter((id) => ids.includes(id)), ...ids.filter((id) => !oldIds.includes(id))])]
  return {
    ...factor,
    levels: ordered.map((id) => [id]),
    level_labels: ordered.map((id) => {
      const index = oldIds.indexOf(id)
      if (index >= 0 && factor.level_labels?.[index]) return factor.level_labels[index]
      const node = subAgents.find((node) => subAgentId(node) === id)
      const label = String(node?.data.label ?? 'Unavailable sub-agent')
      return label || 'Unavailable sub-agent'
    }),
  }
}

export function subAgentFactorOwner(graph: ProtocolGraph | undefined, subAgentNodeId: string): string | undefined {
  return graph?.edges.filter((edge) => edge.source === subAgentNodeId && edge.targetHandle === 'sub_agents')
    .map((edge) => graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[SUB_AGENT_FACTOR_PATH])
    .find(Boolean)
}

export function subAgentFactorIssues(graph: ProtocolGraph | undefined, factors: DesignFactor[]): string[] {
  const groupIssues = sharedGroupIssues(graph, factors, 'sub_agent')
  if (groupIssues.length) return groupIssues
  return factors.filter(isSubAgentFactor).flatMap((factor) => {
    const owner = graph?.nodes.find((node) => node.data.factor_bindings?.[SUB_AGENT_FACTOR_PATH] === factor.name)
    if (!owner || !graph) return [`${factor.name}: rebind or remove this sub-agent factor.`]
    const subAgents = connectedSubAgents(graph, owner.id)
    const ids = [...new Set(subAgents.map(subAgentId))]
    const conflicts = subAgents.some((node) => !!node.data.factor_bindings?.active)
    if (conflicts) return [`${factor.name}: remove individual sub-agent on/off factor bindings first.`]
    if (ids.length < (factor.level_type === 'sub_agent_toggle' ? 1 : 2)) return [`${factor.name}: ${factor.level_type === 'sub_agent_toggle' ? 'connect at least one sub-agent' : 'connect at least two different sub-agents'}.`]
    if (ids.some((id) => !id)) return [`${factor.name}: disconnect or restore unavailable sub-agents.`]
    return []
  })
}
