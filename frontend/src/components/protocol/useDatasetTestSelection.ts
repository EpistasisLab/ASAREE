import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { datasetsApi } from '@/api/client'
import { connectedDatasets, datasetId, DATASET_FACTOR_PATH } from '@/lib/datasetFactors'
import type { ProtocolGraph } from '@/types/protocols'

export function useDatasetTestSelection(graph: ProtocolGraph | undefined, nodeId?: string) {
  const [choices, setChoices] = useState<Record<string, string>>({})
  const library = useQuery({ queryKey: ['datasets'], queryFn: () => datasetsApi.list(), enabled: !!graph?.nodes.some((node) => node.data.factor_bindings?.[DATASET_FACTOR_PATH]) })
  const owners = graph?.nodes.filter((node) => node.data.factor_bindings?.[DATASET_FACTOR_PATH] && (!nodeId || node.id === nodeId)) ?? []
  const signature = JSON.stringify(owners.map((node) => [node.id, node.data.dataset_factor_mode, node.data.dataset_selection, connectedDatasets(graph!, node.id).map(datasetId)]))
  useEffect(() => { setChoices({}) }, [signature])
  const selections = Object.fromEntries(owners.map((node) => {
    if (node.data.dataset_factor_mode === 'dataset_toggle') return [node.id, choices[node.id] ?? ((node.data.dataset_selection as string[] | undefined)?.length ? 'all' : 'none')]
    const ids = connectedDatasets(graph!, node.id).map(datasetId)
    const baseline = (node.data.dataset_selection as string[] | undefined)?.[0]
    return [node.id, choices[node.id] ?? (baseline && ids.includes(baseline) ? baseline : ids[0] ?? '')]
  }))
  const error = owners.length === 0 ? null : library.isError ? 'Could not load available datasets.' : !library.data ? 'Loading datasets…' : owners.some((node) => {
    const ids = node.data.dataset_factor_mode === 'dataset_toggle' ? (selections[node.id] === 'all' ? connectedDatasets(graph!, node.id).map(datasetId) : []) : [selections[node.id]]
    return ids.some((id) => !library.data.some((dataset) => dataset.id === id))
  }) ? 'Select an available dataset for each agent.' : null
  return { graph, owners, library, selections, options: owners.length ? { dataset_selections: selections } : {}, error, setChoice: (agentId: string, id: string) => setChoices((previous) => ({ ...previous, [agentId]: id })) }
}
