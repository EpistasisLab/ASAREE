import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'

export const SKILL_FACTOR_PATH = 'skill_selection'

export function connectedSkills(graph: ProtocolGraph, agentId: string): ProtocolNode[] {
  return graph.edges.filter((edge) => edge.target === agentId && edge.targetHandle === 'skill')
    .flatMap((edge) => {
      const node = graph.nodes.find((node) => node.id === edge.source && node.type === 'skill')
      return node ? [node] : []
    })
}

export function skillId(node: ProtocolNode): string {
  return String((node.data.config as { skill_id?: string } | undefined)?.skill_id ?? '')
}

export function reconcileSkillFactor(factor: DesignFactor, graph: ProtocolGraph, agentId: string): DesignFactor {
  const skills = connectedSkills(graph, agentId)
  const ids = [...new Set(skills.map(skillId))]
  const oldIds = factor.levels.map((level) => Array.isArray(level) ? String(level[0] ?? '') : '')
  const ordered = [...new Set([...oldIds.filter((id) => ids.includes(id)), ...ids.filter((id) => !oldIds.includes(id))])]
  return {
    ...factor,
    levels: ordered.map((id) => [id]),
    level_labels: ordered.map((id) => {
      const index = oldIds.indexOf(id)
      if (index >= 0 && factor.level_labels?.[index]) return factor.level_labels[index]
      const node = skills.find((node) => skillId(node) === id)
      const label = String((node?.data.config as { skill_name?: string } | undefined)?.skill_name ?? node?.data.label ?? 'Unavailable skill')
      return label || 'Unavailable skill'
    }),
  }
}

export function skillFactorOwner(graph: ProtocolGraph | undefined, skillNodeId: string): string | undefined {
  return graph?.edges.filter((edge) => edge.source === skillNodeId && edge.targetHandle === 'skill')
    .map((edge) => graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[SKILL_FACTOR_PATH])
    .find(Boolean)
}

export function skillFactorIssues(graph: ProtocolGraph | undefined, factors: DesignFactor[], availableIds?: Set<string>): string[] {
  return factors.filter((factor) => factor.level_type === 'skill_selection').flatMap((factor) => {
    const owner = graph?.nodes.find((node) => node.data.factor_bindings?.[SKILL_FACTOR_PATH] === factor.name)
    if (!owner || !graph) return [`${factor.name}: rebind or remove this skill factor.`]
    const skills = connectedSkills(graph, owner.id)
    const ids = [...new Set(skills.map(skillId))]
    const conflicts = skills.some((node) => Object.keys(node.data.factor_bindings ?? {}).length > 0)
    if (conflicts) return [`${factor.name}: remove individual skill factor bindings first.`]
    if (ids.length < 2) return [`${factor.name}: connect at least two different skills.`]
    if (ids.some((id) => !id || (availableIds && !availableIds.has(id)))) return [`${factor.name}: disconnect or restore unavailable skills.`]
    return []
  })
}
