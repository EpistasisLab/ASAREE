import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { datasetsApi } from '@/api/client'
import { datasetRowTopologyError, rowBindingForNode } from '@/lib/datasetRows'
import type { ProtocolGraph } from '@/types/protocols'

export function useDatasetRowSelection(graph: ProtocolGraph | undefined, scope: string, nodeId?: string) {
  const binding = graph && rowBindingForNode(graph, nodeId)
  const id = binding?.config?.dataset_id
  const schema = useQuery({ queryKey: ['datasets', id, 'row-schema', scope], queryFn: () => datasetsApi.getRowSchema(id!), enabled: !!id, retry: false })
  const key = `${scope}:${id}:${schema.data?.raw_sha256}:${nodeId ?? ''}`
  const [selection, setSelection] = useState({ key, index: 0 })
  const rowIndex = selection.key === key ? selection.index : 0
  const error = !binding ? null : !id ? 'Registered row source unavailable.' : schema.isLoading ? 'Loading original rows…' : schema.isError ? schema.error.message : !schema.data ? 'Original row metadata unavailable.' : !schema.data.row_count ? 'Original Dataset has no rows.' : !Number.isInteger(rowIndex) || rowIndex < 0 || rowIndex >= schema.data.row_count ? 'Source row is outside the original Dataset.' : binding.input.mode === 'per_row' && binding.input.columns.some(column => !schema.data.columns.includes(column)) ? 'Selected columns are absent from the original CSV.' : graph ? datasetRowTopologyError(graph) : null
  return { binding, schema: schema.data, rowIndex, setRowIndex: (index: number) => setSelection({ key, index }), error }
}
