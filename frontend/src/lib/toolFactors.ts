import type { DesignFactor } from '@/types/experiments'
import { MCP_TOOL_NODE_TYPES } from '@/components/protocol/mcpServerCatalog'
import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'

export const TOOL_FACTOR_PATH = 'tool_selection'

export const toolFactorModes = {
  boolean: 'This tool on/off',
  tool_selection: 'Tool levels',
  tool_toggle: 'All agent tools on/off',
  tool_names: 'Tools allowed',
}
export type ToolFactorMode = keyof typeof toolFactorModes

export function toolFactorConflict(graph: ProtocolGraph, factors: DesignFactor[], toolNodeId: string, mode: ToolFactorMode, agentId: string): string | undefined {
  const owner = graph.nodes.find((node) => node.id === (isIndividualToolMode(mode) ? toolNodeId : agentId))
  const currentName = owner?.data.factor_bindings?.[isIndividualToolMode(mode) ? toolFactorPath(mode) : TOOL_FACTOR_PATH]
  const current = factors.find((factor) => factor.name === currentName)
  if (current && (current.level_type ?? 'boolean') !== mode) {
    return `${toolFactorModes[mode]} is unavailable because this Agent already uses the factor ${current.name} (${toolFactorModes[(current.level_type ?? 'boolean') as ToolFactorMode]}). Keep the current factor type, or select its factor type and remove it first.`
  }
  const conflicts = [...new Set(isIndividualToolMode(mode)
    ? graph.edges.filter((edge) => edge.source === toolNodeId && edge.targetHandle === 'tool')
      .flatMap((edge) => {
        const name = graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[TOOL_FACTOR_PATH]
        return name ? [name] : []
      })
    : connectedTools(graph, agentId).flatMap((node) => Object.values(node.data.factor_bindings ?? {})))]
  if (!conflicts.length) return undefined
  return isIndividualToolMode(mode)
    ? `${toolFactorModes[mode]} is unavailable because a connected Agent already controls this Tool with the factor ${conflicts.join(', ')}. Use the existing Agent-level factor, or select its factor type and remove it first.`
    : `${toolFactorModes[mode]} is unavailable because connected Tools already have factor bindings: ${conflicts.join(', ')}. Agent-level tool factors cannot be combined with individual Tool factor bindings. Use This tool on/off, or remove those bindings in each Tool’s factor dialog first.`
}

export function isToolFactor(factor: DesignFactor): boolean {
  return factor.level_type === 'tool_selection' || factor.level_type === 'tool_toggle'
}

export function connectedTools(graph: ProtocolGraph, agentId: string): ProtocolNode[] {
  return graph.edges.filter((edge) => edge.target === agentId && edge.targetHandle === 'tool')
    .flatMap((edge) => {
      const node = graph.nodes.find((node) => node.id === edge.source && MCP_TOOL_NODE_TYPES.includes(node.type))
      return node ? [node] : []
    })
}

export function toolId(node: ProtocolNode): string { return node.id }

export function isIndividualToolMode(mode: ToolFactorMode): boolean { return mode === 'boolean' || mode === 'tool_names' }
export function toolFactorPath(mode: ToolFactorMode): string { return mode === 'boolean' ? 'config.enabled' : mode === 'tool_names' ? 'config.tool_names' : TOOL_FACTOR_PATH }

export function reconcileToolFactor(factor: DesignFactor, graph: ProtocolGraph, agentId: string): DesignFactor {
  const tools = connectedTools(graph, agentId)
  const ids = [...new Set(tools.map(toolId))]
  if (factor.level_type === 'tool_toggle') {
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
      const node = tools.find((node) => toolId(node) === id)
      const label = String(node?.data.label ?? 'Unavailable tool')
      return label || 'Unavailable tool'
    }),
  }
}

export function toolFactorOwner(graph: ProtocolGraph | undefined, toolNodeId: string): string | undefined {
  return graph?.edges.filter((edge) => edge.source === toolNodeId && edge.targetHandle === 'tool')
    .map((edge) => graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[TOOL_FACTOR_PATH])
    .find(Boolean)
}

export function toolFactorIssues(graph: ProtocolGraph | undefined, factors: DesignFactor[], availableIds?: Set<string>): string[] {
  return factors.filter(isToolFactor).flatMap((factor) => {
    const owner = graph?.nodes.find((node) => node.data.factor_bindings?.[TOOL_FACTOR_PATH] === factor.name)
    if (!owner || !graph) return [`${factor.name}: rebind or remove this tool factor.`]
    const tools = connectedTools(graph, owner.id)
    const ids = [...new Set(tools.map(toolId))]
    const conflicts = tools.some((node) => Object.keys(node.data.factor_bindings ?? {}).length > 0)
    if (conflicts) return [`${factor.name}: remove individual tool factor bindings first.`]
    if (ids.length < (factor.level_type === 'tool_toggle' ? 1 : 2)) return [`${factor.name}: ${factor.level_type === 'tool_toggle' ? 'connect at least one tool' : 'connect at least two different tools'}.`]
    if (tools.some((node) => !(node.data.config as { server_id?: string })?.server_id || (availableIds && !availableIds.has(String((node.data.config as { server_id?: string }).server_id))))) return [`${factor.name}: disconnect or restore unavailable tools.`]
    if (tools.some((node) => !(node.data.config as { tool_names?: string[] })?.tool_names?.length)) return [`${factor.name}: configure allowed tools on every connected Tool node.`]
    return []
  })
}
