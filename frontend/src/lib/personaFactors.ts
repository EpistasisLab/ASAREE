import { sharedFactorGroup, sharedGroupConflict, sharedGroupIssues } from './sharedFactors'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'

export const PERSONA_FACTOR_PATH = 'persona_selection'

export const personaFactorModes = {
  boolean: 'This persona on/off',
  persona_selection: 'Persona levels',
  persona_toggle: 'All personas on/off',
}
export type PersonaFactorMode = keyof typeof personaFactorModes

export function personaFactorConflict(graph: ProtocolGraph, factors: DesignFactor[], personaNodeId: string, mode: PersonaFactorMode, agentId: string): string | undefined {
  if (graph.nodes.find((node) => node.id === personaNodeId)?.data.factor_bindings?.config) return 'Remove the legacy whole-persona binding in the inspector before creating a persona factor.'
  if (mode !== 'boolean') {
    const groupConflict = sharedGroupConflict(graph, personaNodeId, 'persona')
    if (groupConflict) return groupConflict
  }
  const owner = graph.nodes.find((node) => node.id === (mode === 'boolean' ? personaNodeId : agentId))
  const currentName = owner?.data.factor_bindings?.[mode === 'boolean' ? 'config.enabled' : PERSONA_FACTOR_PATH]
  const current = factors.find((factor) => factor.name === currentName)
  if (current && (current.level_type ?? 'boolean') !== mode) {
    return `${personaFactorModes[mode]} is unavailable because this shared group already uses the factor ${current.name} (${personaFactorModes[(current.level_type ?? 'boolean') as PersonaFactorMode]}). Keep the current factor type, or select its factor type and remove it first.`
  }
  const conflicts = [...new Set(mode === 'boolean'
    ? graph.edges.filter((edge) => edge.source === personaNodeId && edge.targetHandle === 'persona')
      .flatMap((edge) => {
        const name = graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[PERSONA_FACTOR_PATH]
        return name ? [name] : []
      })
    : connectedPersonas(graph, agentId).flatMap((node) => Object.values(node.data.factor_bindings ?? {})))]
  if (!conflicts.length) return undefined
  return mode === 'boolean'
    ? `${personaFactorModes[mode]} is unavailable because a connected Agent already controls this Persona with the factor ${conflicts.join(', ')}. Use the existing shared factor, or select its factor type and remove it first.`
    : `${personaFactorModes[mode]} is unavailable because connected Personas already have factor bindings: ${conflicts.join(', ')}. Shared persona factors cannot be combined with individual Persona factor bindings. Use This persona on/off, or remove those bindings in each Persona’s factor dialog first.`
}

export function isPersonaFactor(factor: DesignFactor): boolean {
  return factor.level_type === 'persona_selection' || factor.level_type === 'persona_toggle'
}

export function connectedPersonas(graph: ProtocolGraph, agentId: string): ProtocolNode[] {
  return graph.edges.filter((edge) => edge.target === agentId && edge.targetHandle === 'persona')
    .flatMap((edge) => {
      const node = graph.nodes.find((node) => node.id === edge.source && node.type === 'persona')
      return node ? [node] : []
    })
}

export function personaId(node: ProtocolNode): string {
  // Levels address immutable node snapshots, so library edits/deletions cannot change a run.
  return node.id
}

export function reconcilePersonaFactor(factor: DesignFactor, graph: ProtocolGraph, agentId: string): DesignFactor {
  if (sharedGroupIssues(graph, [factor], 'persona').length) return factor
  const source = connectedPersonas(graph, agentId)[0]
  if (source && sharedFactorGroup(graph, source.id, 'persona').error) return factor
  const personas = connectedPersonas(graph, agentId)
  const ids = [...new Set(personas.map(personaId))]
  if (factor.level_type === 'persona_toggle') {
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
      const node = personas.find((node) => personaId(node) === id)
      const label = String(node?.data.label || 'Unavailable persona')
      return label || 'Unavailable persona'
    }),
  }
}

export function personaFactorOwner(graph: ProtocolGraph | undefined, personaNodeId: string): string | undefined {
  return graph?.edges.filter((edge) => edge.source === personaNodeId && edge.targetHandle === 'persona')
    .map((edge) => graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[PERSONA_FACTOR_PATH])
    .find(Boolean)
}

export function personaFactorIssues(graph: ProtocolGraph | undefined, factors: DesignFactor[]): string[] {
  const groupIssues = sharedGroupIssues(graph, factors, 'persona')
  if (groupIssues.length) return groupIssues
  return factors.filter(isPersonaFactor).flatMap((factor) => {
    const owner = graph?.nodes.find((node) => node.data.factor_bindings?.[PERSONA_FACTOR_PATH] === factor.name)
    if (!owner || !graph) return [`${factor.name}: rebind or remove this persona factor.`]
    const personas = connectedPersonas(graph, owner.id)
    const ids = [...new Set(personas.map(personaId))]
    const conflicts = personas.some((node) => Object.keys(node.data.factor_bindings ?? {}).length > 0)
    if (conflicts) return [`${factor.name}: remove individual persona factor bindings first.`]
    if (ids.length < (factor.level_type === 'persona_toggle' ? 1 : 2)) return [`${factor.name}: ${factor.level_type === 'persona_toggle' ? 'connect at least one persona' : 'connect at least two different personas'}.`]
    if (ids.some((id) => !id)) return [`${factor.name}: disconnect or restore unavailable personas.`]
    return []
  })
}
