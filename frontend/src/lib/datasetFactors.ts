import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'

export const DATASET_FACTOR_PATH = 'dataset_selection'

export const datasetFactorModes = {
  boolean: 'This dataset on/off',
  dataset_selection: 'Dataset levels',
  dataset_toggle: 'All datasets on/off',
}
export type DatasetFactorMode = keyof typeof datasetFactorModes

export function datasetFactorConflict(graph: ProtocolGraph, factors: DesignFactor[], datasetNodeId: string, mode: DatasetFactorMode, agentId: string): string | undefined {
  const affectedIds = new Set(mode === 'boolean' ? [datasetNodeId] : connectedDatasets(graph, agentId).map((node) => node.id))
  if (graph.edges.some((edge) => affectedIds.has(edge.source) && edge.data?.dataset_input?.mode === 'per_row')) return 'Dataset factors require Whole dataset inputs. Change Per row to Whole dataset first.'
  const owner = graph.nodes.find((node) => node.id === (mode === 'boolean' ? datasetNodeId : agentId))
  const currentName = owner?.data.factor_bindings?.[mode === 'boolean' ? 'config.enabled' : DATASET_FACTOR_PATH]
  const current = factors.find((factor) => factor.name === currentName)
  if (current && (current.level_type ?? 'boolean') !== mode) {
    return `${datasetFactorModes[mode]} is unavailable because this Agent already uses the factor ${current.name} (${datasetFactorModes[(current.level_type ?? 'boolean') as DatasetFactorMode]}). Keep the current factor type, or select its factor type and remove it first.`
  }
  const conflicts = [...new Set(mode === 'boolean'
    ? [...(owner?.data.factor_bindings?.config ? [owner.data.factor_bindings.config] : []), ...graph.edges.filter((edge) => edge.source === datasetNodeId && ['dataset', 'resource', 'tool'].includes(edge.targetHandle ?? ''))
      .flatMap((edge) => {
        const name = graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[DATASET_FACTOR_PATH]
        return name ? [name] : []
      })]
    : connectedDatasets(graph, agentId).flatMap((node) => Object.values(node.data.factor_bindings ?? {})))]
  if (!conflicts.length) return undefined
  return mode === 'boolean'
    ? `${datasetFactorModes[mode]} is unavailable because a connected Agent already controls this Dataset with the factor ${conflicts.join(', ')}. Use the existing Agent-level factor, or select its factor type and remove it first.`
    : `${datasetFactorModes[mode]} is unavailable because connected Datasets already have factor bindings: ${conflicts.join(', ')}. Agent-level dataset factors cannot be combined with individual Dataset factor bindings. Use This dataset on/off, or remove those bindings in each Dataset’s factor dialog first.`
}

export function isDatasetFactor(factor: DesignFactor): boolean {
  return factor.level_type === 'dataset_selection' || factor.level_type === 'dataset_toggle'
}

export function connectedDatasets(graph: ProtocolGraph, agentId: string): ProtocolNode[] {
  return graph.edges.filter((edge) => edge.target === agentId && ['dataset', 'resource', 'tool'].includes(edge.targetHandle ?? ''))
    .flatMap((edge) => {
      const node = graph.nodes.find((node) => node.id === edge.source && node.type === 'dataset')
      return node ? [node] : []
    })
}

export function datasetId(node: ProtocolNode): string {
  return String((node.data.config as { dataset_id?: string } | undefined)?.dataset_id ?? '')
}

export function reconcileDatasetFactor(factor: DesignFactor, graph: ProtocolGraph, agentId: string): DesignFactor {
  const datasets = connectedDatasets(graph, agentId)
  const ids = [...new Set(datasets.map(datasetId))]
  if (factor.level_type === 'dataset_toggle') {
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
      const node = datasets.find((node) => datasetId(node) === id)
      const label = String((node?.data.config as { dataset_name?: string } | undefined)?.dataset_name ?? node?.data.label ?? 'Unavailable dataset')
      return label || 'Unavailable dataset'
    }),
  }
}

export function datasetFactorOwner(graph: ProtocolGraph | undefined, datasetNodeId: string): string | undefined {
  return graph?.edges.filter((edge) => edge.source === datasetNodeId && ['dataset', 'resource', 'tool'].includes(edge.targetHandle ?? ''))
    .map((edge) => graph.nodes.find((node) => node.id === edge.target)?.data.factor_bindings?.[DATASET_FACTOR_PATH])
    .find(Boolean)
}

export function datasetFactorIssues(graph: ProtocolGraph | undefined, factors: DesignFactor[], availableIds?: Set<string>): string[] {
  return factors.filter(isDatasetFactor).flatMap((factor) => {
    const owner = graph?.nodes.find((node) => node.data.factor_bindings?.[DATASET_FACTOR_PATH] === factor.name)
    if (!owner || !graph) return [`${factor.name}: rebind or remove this dataset factor.`]
    const datasets = connectedDatasets(graph, owner.id)
    if (graph.edges.some((edge) => edge.target === owner.id && datasets.some((node) => node.id === edge.source) && edge.data?.dataset_input?.mode === 'per_row')) return [`${factor.name}: use Whole dataset inputs before factorizing this connector.`]
    const ids = [...new Set(datasets.map(datasetId))]
    const conflicts = datasets.some((node) => Object.keys(node.data.factor_bindings ?? {}).length > 0)
    if (conflicts) return [`${factor.name}: remove individual dataset factor bindings first.`]
    if (ids.length < (factor.level_type === 'dataset_toggle' ? 1 : 2)) return [`${factor.name}: ${factor.level_type === 'dataset_toggle' ? 'connect at least one dataset' : 'connect at least two different datasets'}.`]
    if (ids.some((id) => !id || (availableIds && !availableIds.has(id)))) return [`${factor.name}: disconnect or restore unavailable datasets.`]
    return []
  })
}
