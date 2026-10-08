import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'

export type SharedFactorKind = 'skill' | 'dataset' | 'knowledge' | 'script' | 'tool' | 'sub_agent' | 'pattern'

const specs = {
  skill: { types: ['skill'], handles: ['skill'], path: 'skill_selection' },
  dataset: { types: ['dataset'], handles: ['dataset', 'resource', 'tool'], path: 'dataset_selection' },
  knowledge: { types: ['okf_bundle', 'okf_document'], handles: ['knowledge'], path: 'knowledge_selection' },
  script: { types: ['script'], handles: ['tool'], path: 'script_selection' },
  tool: { types: ['mcp_tool', 'mcp_scikit_learn', 'mcp_client_tool'], handles: ['tool'], path: 'tool_selection' },
  sub_agent: { types: ['sub_agent'], handles: ['sub_agents'], path: 'sub_agent_selection' },
  pattern: { types: ['pattern_reason_act', 'pattern_single_agent_baseline'], handles: ['architectural_pattern'], path: 'pattern_override' },
}

export function factorRecipients(graph: ProtocolGraph, nodeId: string, kind: SharedFactorKind): ProtocolNode[] {
  const ids = new Set(graph.edges.filter((edge) => edge.source === nodeId && specs[kind].handles.includes(edge.targetHandle ?? '')).map((edge) => edge.target))
  return graph.nodes.filter((node) => ids.has(node.id))
}

/** All alternatives must be visible to exactly the same recipients. */
export function sharedFactorGroup(graph: ProtocolGraph, nodeId: string, kind: SharedFactorKind) {
  const recipients = factorRecipients(graph, nodeId, kind)
  const recipientIds = new Set(recipients.map((node) => node.id))
  const spec = specs[kind]
  const memberIds = new Set(graph.edges.filter((edge) => recipientIds.has(edge.target) && spec.handles.includes(edge.targetHandle ?? '')).map((edge) => edge.source))
  const members = graph.nodes.filter((node) => memberIds.has(node.id) && spec.types.includes(node.type))
  const error = !recipients.length ? 'Connect this node to an Agent before creating a group factor.'
    : recipients.some((node) => !['agent', 'sub_agent'].includes(node.type)) ? 'Group factors require Agent connections only. Use an individual node factor for other recipients.'
    : kind !== 'pattern' && members.some((member) => {
      const ids = factorRecipients(graph, member.id, kind).map((node) => node.id)
      return ids.length !== recipientIds.size || ids.some((id) => !recipientIds.has(id))
    }) ? 'Every node in this factor group must connect to exactly the same Agents. Connect all alternatives to all affected Agents, or use individual node factors.' : undefined
  return { recipients, members, error }
}

export function sharedGroupConflict(graph: ProtocolGraph, nodeId: string, kind: SharedFactorKind): string | undefined {
  const group = sharedFactorGroup(graph, nodeId, kind)
  if (group.error) return group.error
  const names = new Set(group.recipients.map((node) => node.data.factor_bindings?.[specs[kind].path]).filter(Boolean))
  if (names.size > 1) return 'The connected Agents already have different factors for this group. Remove those factors before creating one shared factor.'
}

export function sharedGroupIssues(graph: ProtocolGraph | undefined, factors: DesignFactor[], kind: SharedFactorKind): string[] {
  if (!graph) return []
  const path = specs[kind].path
  return factors.filter((factor) => kind === 'pattern' ? factor.level_type === 'pattern' : factor.level_type === `${kind}_selection` || factor.level_type === `${kind}_toggle`).flatMap((factor) => {
    const owners = graph.nodes.filter((node) => node.data.factor_bindings?.[path] === factor.name)
    if (!owners.length) return [] // The family validator reports an unbound factor.
    const source = graph.edges.find((edge) => edge.target === owners[0].id && specs[kind].handles.includes(edge.targetHandle ?? '') && specs[kind].types.includes(graph.nodes.find((node) => node.id === edge.source)?.type ?? ''))?.source
    if (!source) return [`${factor.name}: restore the factor group's connections or remove its factor.`]
    const group = sharedFactorGroup(graph, source, kind)
    if (group.error) return [`${factor.name}: ${group.error}`]
    if (owners.length !== group.recipients.length || group.recipients.some((node) => node.data.factor_bindings?.[path] !== factor.name)) return [`${factor.name}: the factor must apply to every Agent connected to this group. Restore the connections or remove and recreate the factor.`]
    const baseline = JSON.stringify(owners[0].data[path])
    const mode = owners[0].data[`${kind}_factor_mode`] ?? `${kind}_selection`
    if (owners.some((node) => JSON.stringify(node.data[path]) !== baseline || kind !== 'pattern' && (node.data[`${kind}_factor_mode`] ?? `${kind}_selection`) !== mode)) return [`${factor.name}: every connected Agent must use the same default factor level and mode.`]
    return []
  })
}

/** One shared factor produces one test choice, even when several Agents use it. */
export function sharedFactorOwners(graph: ProtocolGraph | undefined, path: string, nodeId?: string) {
  const names = new Set<string>()
  return graph?.nodes.filter((node) => {
    const name = node.data.factor_bindings?.[path]
    if (!name || (nodeId && node.id !== nodeId) || names.has(name)) return false
    names.add(name)
    return true
  }) ?? []
}

export function graphWithSharedFactor(graph: ProtocolGraph, nodeId: string, kind: SharedFactorKind, factor: DesignFactor): ProtocolGraph {
  const group = sharedFactorGroup(graph, nodeId, kind)
  if (group.error) throw new Error(group.error)
  const ids = new Set(group.recipients.map((node) => node.id))
  const path = specs[kind].path
  return { ...graph, nodes: graph.nodes.map((node) => ids.has(node.id) ? { ...node, data: {
    ...node.data, [path]: factor.levels[0], ...(kind === 'pattern' ? {} : { [`${kind}_factor_mode`]: factor.level_type }),
    factor_bindings: { ...node.data.factor_bindings, [path]: factor.name },
  } } : node) }
}

export function factorScopeLabel(graph: ProtocolGraph, nodeId: string, kind: SharedFactorKind): string {
  return factorRecipients(graph, nodeId, kind).map((node) => String(node.data.label || node.id)).join(', ')
}
