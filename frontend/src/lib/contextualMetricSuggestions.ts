import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'
import { isMcpToolNodeType } from '@/lib/mcpToolMetrics'

export interface ContextualMetricSuggestion {
  key: 'tool_error_rate' | 'agent_loop_iterations' | 'critic_rejections' | 'critic_approvals'
  context: string
}

function enabledAgent(node: ProtocolNode | undefined): boolean {
  return (node?.type === 'agent' || node?.type === 'sub_agent') && node.data.active !== false
}

function sourceIsCallable(node: ProtocolNode, targetHandle: string | null | undefined): boolean {
  const config = node.data.config as unknown as Record<string, unknown> | undefined
  if (config?.enabled === false) return false
  if (isMcpToolNodeType(node.type)) {
    return targetHandle === 'tool' && Array.isArray(config?.tool_names) && config.tool_names.length > 0
  }
  if (node.type === 'script') return targetHandle === 'tool' && Boolean(String(config?.code ?? '').trim())
  if (node.type === 'skill') return targetHandle === 'skill' && Boolean(config?.skill_id)
  if (node.type === 'okf_bundle' || node.type === 'okf_document') {
    return targetHandle === 'knowledge' && Boolean(config?.server_name) && Array.isArray(config?.tool_names) && config.tool_names.length > 0
  }
  return false
}

function signature(value: unknown[]): string {
  return JSON.stringify([...value].sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right))))
}

/** Derive optional built-ins from behavior the current protocol canvas can execute. */
export function contextualMetricSuggestions(graph: ProtocolGraph | undefined): ContextualMetricSuggestion[] {
  if (!graph) return []
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]))
  const toolContext = graph.edges.flatMap((edge) => {
    const source = nodes.get(edge.source)
    const target = nodes.get(edge.target)
    if (!source || !enabledAgent(target) || !sourceIsCallable(source, edge.targetHandle)) return []
    const config = source.data.config as unknown as Record<string, unknown> | undefined
    const configuredTools = Array.isArray(config?.tool_names) ? [...config.tool_names].sort() : config?.skill_id
    return [{ source: source.id, target: target!.id, type: source.type, tools: configuredTools }]
  })
  const loopContext = graph.edges.flatMap((edge) => {
    const source = nodes.get(edge.source)
    const target = nodes.get(edge.target)
    if (edge.targetHandle !== 'architectural_pattern' || source?.type !== 'pattern_reason_act' || !enabledAgent(target)) return []
    const config = source.data.config as unknown as Record<string, unknown> | undefined
    const maxIterations = config?.max_iterations
    if (typeof maxIterations !== 'number' || maxIterations <= 1) return []
    return [{ source: source.id, target: target!.id, maxIterations }]
  })
  const gateContext = graph.edges.flatMap((edge) => {
    const source = nodes.get(edge.source)
    const target = nodes.get(edge.target)
    if (edge.targetHandle || !enabledAgent(source) || target?.type !== 'critic_gate') return []
    const config = target.data.config as unknown as Record<string, unknown> | undefined
    if (config?.enabled === false) return []
    return [{ worker: source!.id, gate: target.id, maxRevisions: config?.max_revisions }]
  })

  const suggestions: ContextualMetricSuggestion[] = []
  if (toolContext.length > 0) suggestions.push({ key: 'tool_error_rate', context: signature(toolContext) })
  if (loopContext.length > 0) suggestions.push({ key: 'agent_loop_iterations', context: signature(loopContext) })
  if (gateContext.length > 0) {
    const context = signature(gateContext)
    suggestions.push({ key: 'critic_rejections', context }, { key: 'critic_approvals', context })
  }
  return suggestions
}
