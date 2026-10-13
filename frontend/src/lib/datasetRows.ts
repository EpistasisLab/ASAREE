import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'

const handles = new Set(['dataset', 'resource', 'tool'])
export const datasetRowFactorBindingError = 'Per row is unavailable because this Dataset or its Agent connector is bound to an experimental factor. Per-row runs require the same dataset across all cells. Use Whole dataset here, or remove the Dataset factor bindings first.'

export function datasetConfigIsFactorBound(node?: ProtocolNode) {
  return Object.keys(node?.data.factor_bindings ?? {}).some(path => path === 'config' || path.startsWith('config.'))
}

export function datasetRowBindings(graph: ProtocolGraph) {
  const nodes = new Map(graph.nodes.map(node => [node.id, node]))
  return graph.edges.flatMap(edge => {
    const source = nodes.get(edge.source)
    const target = nodes.get(edge.target)
    const config = source?.data.config as { enabled?: boolean; dataset_id?: string; dataset_name?: string } | undefined
    if (source?.type !== 'dataset' || target?.type !== 'agent' || !handles.has(edge.targetHandle ?? '') || config?.enabled === false) return []
    return [{ edge, source, config, input: edge.data?.dataset_input ?? { mode: 'whole_dataset' as const } }]
  })
}

export function rowBindingForNode(graph: ProtocolGraph, nodeId?: string) {
  return datasetRowBindings(graph).find(binding => binding.input.mode === 'per_row' && (!nodeId || binding.edge.target === nodeId))
}

export function datasetRowTopologyError(graph: ProtocolGraph): string | null {
  const bindings = datasetRowBindings(graph)
  const rows = bindings.filter(binding => binding.input.mode === 'per_row')
  if (!rows.length) return null
  const id = rows[0].config?.dataset_id
  if (!id || !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(id)) return 'Per row requires a registered dataset UUID.'
  if (rows.some(binding => binding.config?.dataset_id?.toLowerCase() !== id.toLowerCase())) return 'Per row inputs must share one Dataset driver.'
  const names = new Set(rows.map(binding => binding.config?.dataset_name).filter(Boolean))
  if (bindings.some(binding => binding.input.mode === 'whole_dataset' && (binding.config?.dataset_id?.toLowerCase() === id.toLowerCase() || names.has(binding.config?.dataset_name)))) return 'One Dataset cannot use both Whole dataset and Per row.'
  const agents = new Map<string, string>()
  for (const binding of rows) {
    if (binding.input.mode !== 'per_row') continue
    if (!binding.input.columns.length || new Set(binding.input.columns).size !== binding.input.columns.length || binding.input.columns.some(column => !column.trim())) return 'Select distinct, nonempty columns.'
    const key = JSON.stringify(binding.input.columns)
    if (agents.has(binding.edge.target) && agents.get(binding.edge.target) !== key) return 'Duplicate Dataset connections must select identical columns.'
    agents.set(binding.edge.target, key)
    if (datasetConfigIsFactorBound(binding.source) || graph.nodes.find((node) => node.id === binding.edge.target)?.data.factor_bindings?.dataset_selection) return datasetRowFactorBindingError
  }
  return null
}
