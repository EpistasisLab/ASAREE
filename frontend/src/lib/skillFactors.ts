import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'

export const SKILL_FACTOR_PATH = 'skill_selection'

export const skillFactorModes = {
  boolean: 'This skill on/off',
  skill_selection: 'Skill levels',
  skill_toggle: 'All skills on/off',
}
export type SkillFactorMode = keyof typeof skillFactorModes

export function skillFactorConflict(graph: ProtocolGraph, factors: DesignFactor[], skillNodeId: string, mode: SkillFactorMode, agentId: string): string | undefined {
  const owner = graph.nodes.find((node) => node.id === (mode === 'boolean' ? skillNodeId : agentId))
  const currentName = owner?.data.factor_bindings?.[mode === 'boolean' ? 'config.enabled' : SKILL_FACTOR_PATH]
  const current = factors.find((factor) => factor.name === currentName)
  if (current && (current.level_type ?? 'boolean') !== mode) {
    return `${skillFactorModes[mode]} is unavailable because this Agent already uses the factor ${current.name} (${skillFactorModes[(current.level_type ?? 'boolean') as SkillFactorMode]}). Keep the current factor type, or select its factor type and remove it first.`
  }
  const conflicts = [...new Set(mode === 'boolean'
    ? graph.edges.filter((edge) => edge.source === skillNodeId && edge.targetHandle === 'skill')
      .flatMap((edge) => {
        const name = graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[SKILL_FACTOR_PATH]
        return name ? [name] : []
      })
    : connectedSkills(graph, agentId).flatMap((node) => Object.values(node.data.factor_bindings ?? {})))]
  if (!conflicts.length) return undefined
  return mode === 'boolean'
    ? `${skillFactorModes[mode]} is unavailable because a connected Agent already controls this Skill with the factor ${conflicts.join(', ')}. Use the existing Agent-level factor, or select its factor type and remove it first.`
    : `${skillFactorModes[mode]} is unavailable because connected Skills already have factor bindings: ${conflicts.join(', ')}. Agent-level skill factors cannot be combined with individual Skill factor bindings. Use This skill on/off, or remove those bindings in each Skill’s factor dialog first.`
}

export function isSkillFactor(factor: DesignFactor): boolean {
  return factor.level_type === 'skill_selection' || factor.level_type === 'skill_toggle'
}

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
  if (factor.level_type === 'skill_toggle') {
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
  return factors.filter(isSkillFactor).flatMap((factor) => {
    const owner = graph?.nodes.find((node) => node.data.factor_bindings?.[SKILL_FACTOR_PATH] === factor.name)
    if (!owner || !graph) return [`${factor.name}: rebind or remove this skill factor.`]
    const skills = connectedSkills(graph, owner.id)
    const ids = [...new Set(skills.map(skillId))]
    const conflicts = skills.some((node) => Object.keys(node.data.factor_bindings ?? {}).length > 0)
    if (conflicts) return [`${factor.name}: remove individual skill factor bindings first.`]
    if (ids.length < (factor.level_type === 'skill_toggle' ? 1 : 2)) return [`${factor.name}: ${factor.level_type === 'skill_toggle' ? 'connect at least one skill' : 'connect at least two different skills'}.`]
    if (ids.some((id) => !id || (availableIds && !availableIds.has(id)))) return [`${factor.name}: disconnect or restore unavailable skills.`]
    return []
  })
}
