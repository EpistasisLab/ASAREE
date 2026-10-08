import { sharedFactorGroup, sharedGroupConflict, sharedGroupIssues } from './sharedFactors'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'

export const SCRIPT_FACTOR_PATH = 'script_selection'

export const scriptFactorModes = {
  boolean: 'This script on/off',
  script_selection: 'Script levels',
  script_toggle: 'All scripts on/off',
}
export type ScriptFactorMode = keyof typeof scriptFactorModes

export function scriptFactorConflict(graph: ProtocolGraph, factors: DesignFactor[], scriptNodeId: string, mode: ScriptFactorMode, agentId: string): string | undefined {
  if (!(isIndividualScriptMode(mode))) {
    const groupConflict = sharedGroupConflict(graph, scriptNodeId, 'script')
    if (groupConflict) return groupConflict
  }
  const owner = graph.nodes.find((node) => node.id === (isIndividualScriptMode(mode) ? scriptNodeId : agentId))
  const currentName = owner?.data.factor_bindings?.[isIndividualScriptMode(mode) ? scriptFactorPath(mode) : SCRIPT_FACTOR_PATH]
  const current = factors.find((factor) => factor.name === currentName)
  if (current && (current.level_type ?? 'boolean') !== mode) {
    return `${scriptFactorModes[mode]} is unavailable because this shared group already uses the factor ${current.name} (${scriptFactorModes[(current.level_type ?? 'boolean') as ScriptFactorMode]}). Keep the current factor type, or select its factor type and remove it first.`
  }
  if (isIndividualScriptMode(mode)) {
    const other = Object.entries(owner?.data.factor_bindings ?? {}).filter(([path]) => path === 'config').filter(([path]) => path !== scriptFactorPath(mode))
    if (other.length) return `${scriptFactorModes[mode]} is unavailable because this Script already has factor bindings: ${other.map(([, name]) => name).join(', ')}. Remove those bindings first.`
  }
  const conflicts = [...new Set(isIndividualScriptMode(mode)
    ? graph.edges.filter((edge) => edge.source === scriptNodeId && edge.targetHandle === 'tool')
      .flatMap((edge) => {
        const name = graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[SCRIPT_FACTOR_PATH]
        return name ? [name] : []
      })
    : connectedScripts(graph, agentId).flatMap((node) => Object.values(node.data.factor_bindings ?? {})))]
  if (!conflicts.length) return undefined
  return isIndividualScriptMode(mode)
    ? `${scriptFactorModes[mode]} is unavailable because a connected Agent already controls this Script with the factor ${conflicts.join(', ')}. Use the existing shared factor, or select its factor type and remove it first.`
    : `${scriptFactorModes[mode]} is unavailable because connected Scripts already have factor bindings: ${conflicts.join(', ')}. Shared script factors cannot be combined with individual Script factor bindings. Use This script on/off, or remove those bindings in each Script’s factor dialog first.`
}

export function isScriptFactor(factor: DesignFactor): boolean {
  return factor.level_type === 'script_selection' || factor.level_type === 'script_toggle'
}

export function connectedScripts(graph: ProtocolGraph, agentId: string): ProtocolNode[] {
  return graph.edges.filter((edge) => edge.target === agentId && edge.targetHandle === 'tool')
    .flatMap((edge) => {
      const node = graph.nodes.find((node) => node.id === edge.source && node.type === 'script')
      return node ? [node] : []
    })
}

export function scriptId(node: ProtocolNode): string { return node.id }

export function isIndividualScriptMode(mode: ScriptFactorMode): boolean { return mode === 'boolean' }
export function scriptFactorPath(mode: ScriptFactorMode): string { return mode === 'boolean' ? 'config.enabled' : SCRIPT_FACTOR_PATH }

export function reconcileScriptFactor(factor: DesignFactor, graph: ProtocolGraph, agentId: string): DesignFactor {
  if (sharedGroupIssues(graph, [factor], 'script').length) return factor
  const source = connectedScripts(graph, agentId)[0]
  if (source && sharedFactorGroup(graph, source.id, 'script').error) return factor
  const scripts = connectedScripts(graph, agentId)
  const ids = [...new Set(scripts.map(scriptId))]
  if (factor.level_type === 'script_toggle') {
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
      const node = scripts.find((node) => scriptId(node) === id)
      const label = String(node?.data.label ?? 'Unavailable script')
      return label || 'Unavailable script'
    }),
  }
}

export function scriptFactorOwner(graph: ProtocolGraph | undefined, scriptNodeId: string): string | undefined {
  return graph?.edges.filter((edge) => edge.source === scriptNodeId && edge.targetHandle === 'tool')
    .map((edge) => graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[SCRIPT_FACTOR_PATH])
    .find(Boolean)
}

export function scriptFactorIssues(graph: ProtocolGraph | undefined, factors: DesignFactor[]): string[] {
  const groupIssues = sharedGroupIssues(graph, factors, 'script')
  if (groupIssues.length) return groupIssues
  const connectorIssues = factors.filter(isScriptFactor).flatMap((factor) => {
    const owner = graph?.nodes.find((node) => node.data.factor_bindings?.[SCRIPT_FACTOR_PATH] === factor.name)
    if (!owner || !graph) return [`${factor.name}: rebind or remove this script factor.`]
    const scripts = connectedScripts(graph, owner.id)
    const ids = [...new Set(scripts.map(scriptId))]
    const conflicts = scripts.some((node) => Object.keys(node.data.factor_bindings ?? {}).length > 0)
    if (conflicts) return [`${factor.name}: remove individual script factor bindings first.`]
    if (ids.length < (factor.level_type === 'script_toggle' ? 1 : 2)) return [`${factor.name}: ${factor.level_type === 'script_toggle' ? 'connect at least one script' : 'connect at least two different scripts'}.`]
    if (scripts.some((node) => !(node.data.config as { code?: string })?.code?.trim())) return [`${factor.name}: configure code on every connected Script node.`]
    return []
  })
  return [...connectorIssues, ...requiredScriptIssues(graph, factors)]
}

export function requiredScriptIssues(graph: ProtocolGraph | undefined, factors: DesignFactor[]): string[] {
  return (graph?.nodes ?? []).filter((node) => node.type === 'tool_step' && node.data.active !== false).flatMap((step) => {
    const args = (step.data.config as { arguments?: Record<string, { source?: string }> })?.arguments ?? {}
    if (!Object.values(args).some((arg) => arg.source === 'script_code')) return []
    const scripts = connectedScripts(graph!, step.id)
    const prefix = `${step.data.label || step.id}: requires script_code`
    if (!scripts.length) return [`${prefix}; connect a Script with code.`]
    return scripts.flatMap((script) => {
      const bindings = script.data.factor_bindings ?? {}
      const enabled = factors.find((factor) => factor.name === bindings['config.enabled'])
      if (enabled?.levels.some((level) => level !== true)) return [`${prefix}; its Script factor cannot include off.`]
      const code = factors.find((factor) => factor.name === bindings['config.code'])
      if (code?.levels.some((level) => !String(level ?? '').trim())) return [`${prefix}; every Script code level needs code.`]
      const variants = factors.find((factor) => factor.name === bindings.config)?.levels ?? [script.data.config]
      if (variants.some((level) => {
        const config = level as { code?: string; enabled?: boolean } | null
        return !config?.code?.trim() || (!enabled && config.enabled === false)
      })) return [`${prefix}; every Script variant needs enabled code.`]
      return []
    })
  })
}
