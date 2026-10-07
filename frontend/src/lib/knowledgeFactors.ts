import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'

export const KNOWLEDGE_FACTOR_PATH = 'knowledge_selection'

export const knowledgeFactorModes = {
  boolean: 'This knowledge on/off',
  knowledge_selection: 'Knowledge levels',
  knowledge_toggle: 'All knowledge on/off',
}
export type KnowledgeFactorMode = keyof typeof knowledgeFactorModes

export function knowledgeFactorConflict(graph: ProtocolGraph, factors: DesignFactor[], knowledgeNodeId: string, mode: KnowledgeFactorMode, agentId: string): string | undefined {
  const owner = graph.nodes.find((node) => node.id === (mode === 'boolean' ? knowledgeNodeId : agentId))
  const currentName = owner?.data.factor_bindings?.[mode === 'boolean' ? 'config.enabled' : KNOWLEDGE_FACTOR_PATH]
  const current = factors.find((factor) => factor.name === currentName)
  if (current && (current.level_type ?? 'boolean') !== mode) {
    return `${knowledgeFactorModes[mode]} is unavailable because this Agent already uses the factor ${current.name} (${knowledgeFactorModes[(current.level_type ?? 'boolean') as KnowledgeFactorMode]}). Keep the current factor type, or select its factor type and remove it first.`
  }
  const conflicts = [...new Set(mode === 'boolean'
    ? graph.edges.filter((edge) => edge.source === knowledgeNodeId && edge.targetHandle === 'knowledge')
      .flatMap((edge) => {
        const name = graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[KNOWLEDGE_FACTOR_PATH]
        return name ? [name] : []
      })
    : connectedKnowledge(graph, agentId).flatMap((node) => Object.values(node.data.factor_bindings ?? {})))]
  if (!conflicts.length) return undefined
  return mode === 'boolean'
    ? `${knowledgeFactorModes[mode]} is unavailable because a connected Agent already controls this Knowledge with the factor ${conflicts.join(', ')}. Use the existing Agent-level factor, or select its factor type and remove it first.`
    : `${knowledgeFactorModes[mode]} is unavailable because connected Knowledge nodes already have factor bindings: ${conflicts.join(', ')}. Agent-level knowledge factors cannot be combined with individual Knowledge factor bindings. Use This knowledge on/off, or remove those bindings in each Knowledge node’s factor dialog first.`
}

export function isKnowledgeFactor(factor: DesignFactor): boolean {
  return factor.level_type === 'knowledge_selection' || factor.level_type === 'knowledge_toggle'
}

export function connectedKnowledge(graph: ProtocolGraph, agentId: string): ProtocolNode[] {
  return graph.edges.filter((edge) => edge.target === agentId && edge.targetHandle === 'knowledge')
    .flatMap((edge) => {
      const node = graph.nodes.find((node) => node.id === edge.source && ['okf_bundle', 'okf_document'].includes(node.type))
      return node ? [node] : []
    })
}

export function knowledgeId(node: ProtocolNode): string {
  const config = node.data.config as { bundle_id?: string; document_id?: string } | undefined
  return String(config?.bundle_id ?? config?.document_id ?? '')
}

export function reconcileKnowledgeFactor(factor: DesignFactor, graph: ProtocolGraph, agentId: string): DesignFactor {
  const knowledge = connectedKnowledge(graph, agentId)
  const ids = [...new Set(knowledge.map(knowledgeId))]
  if (factor.level_type === 'knowledge_toggle') {
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
      const node = knowledge.find((node) => knowledgeId(node) === id)
      const label = String(node?.data.label ?? 'Unavailable knowledge')
      return label || 'Unavailable knowledge'
    }),
  }
}

export function knowledgeFactorOwner(graph: ProtocolGraph | undefined, knowledgeNodeId: string): string | undefined {
  return graph?.edges.filter((edge) => edge.source === knowledgeNodeId && edge.targetHandle === 'knowledge')
    .map((edge) => graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[KNOWLEDGE_FACTOR_PATH])
    .find(Boolean)
}

export function knowledgeFactorIssues(graph: ProtocolGraph | undefined, factors: DesignFactor[], availableIds?: Set<string>): string[] {
  return factors.filter(isKnowledgeFactor).flatMap((factor) => {
    const owner = graph?.nodes.find((node) => node.data.factor_bindings?.[KNOWLEDGE_FACTOR_PATH] === factor.name)
    if (!owner || !graph) return [`${factor.name}: rebind or remove this knowledge factor.`]
    const knowledge = connectedKnowledge(graph, owner.id)
    const ids = [...new Set(knowledge.map(knowledgeId))]
    const conflicts = knowledge.some((node) => Object.keys(node.data.factor_bindings ?? {}).length > 0)
    if (conflicts) return [`${factor.name}: remove individual knowledge factor bindings first.`]
    if (ids.length < (factor.level_type === 'knowledge_toggle' ? 1 : 2)) return [`${factor.name}: ${factor.level_type === 'knowledge_toggle' ? 'connect at least one knowledge source' : 'connect at least two different knowledge sources'}.`]
    if (ids.some((id) => !id || (availableIds && !availableIds.has(id)))) return [`${factor.name}: disconnect or restore unavailable knowledge.`]
    return []
  })
}
