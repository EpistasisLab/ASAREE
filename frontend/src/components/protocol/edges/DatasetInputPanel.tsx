import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useEdges, useNodes, useReactFlow } from '@xyflow/react'
import { datasetsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { datasetRowTopologyError } from '@/lib/datasetRows'
import type { DatasetInput, ProtocolEdge, ProtocolNode } from '@/types/protocols'

export function DatasetInputPanel({ edgeId, source, disabled = false }: { edgeId: string; source: string; disabled?: boolean }) {
  const { setEdges } = useReactFlow()
  const nodes = useNodes() as unknown as ProtocolNode[]
  const edges = useEdges() as unknown as ProtocolEdge[]
  const edge = edges.find(item => item.id === edgeId)
  const saved = edge?.data?.dataset_input
  const [mode, setMode] = useState<DatasetInput['mode']>(saved?.mode ?? 'whole_dataset')
  const [columns, setColumns] = useState(saved?.mode === 'per_row' ? saved.columns : [])
  const [applied, setApplied] = useState<DatasetInput['mode'] | null>(null)
  const [appliedKey, setAppliedKey] = useState(() => JSON.stringify(saved?.mode === 'per_row'
    ? ['per_row', [...saved.columns].sort()] : ['whole_dataset']))
  const inputKey = JSON.stringify(mode === 'per_row' ? [mode, [...columns].sort()] : [mode])
  const hasChanges = inputKey !== appliedKey
  const id = (nodes.find(node => node.id === source)?.data.config as { dataset_id?: string })?.dataset_id
  const schema = useQuery({ queryKey: ['datasets', id, 'row-schema'], queryFn: () => datasetsApi.getRowSchema(id!), enabled: !!id })
  const input: DatasetInput = mode === 'per_row' ? { mode, columns } : { mode }
  const graph = { nodes, edges: edges.map(item => item.id === edgeId ? { ...item, data: { ...item.data, dataset_input: input } } : item) }
  const error = !id ? 'Select a registered Dataset.' : schema.isError ? schema.error.message : schema.isLoading ? 'Loading original columns…' : !schema.data ? 'Source metadata unavailable.' : mode === 'per_row' && !schema.data.row_count ? 'The original Dataset has no rows.' : mode === 'per_row' && columns.some(column => !schema.data.columns.includes(column)) ? 'Saved columns are missing from the original CSV.' : datasetRowTopologyError(graph)
  function apply() {
    if (error || disabled || !hasChanges) return
    setEdges(current => current.map(item => {
      if (item.id !== edgeId) return item
      const data = { ...item.data }
      delete data.dataset_input
      if (mode === 'per_row') data.dataset_input = { mode, columns: schema.data!.columns.filter(column => columns.includes(column)) }
      return { ...item, data }
    }))
    setApplied(mode)
    setAppliedKey(inputKey)
  }
  return <div className="space-y-3 text-xs">
    <p className="font-mono text-primary">Dataset input</p>
    <div role="radiogroup" aria-label="Dataset consumption">
      {(['whole_dataset', 'per_row'] as const).map(value => <label key={value} className="flex items-center gap-2 py-1"><input type="radio" name={`dataset-${edgeId}`} checked={mode === value} disabled={disabled} onChange={() => { setMode(value); setApplied(null) }} />{value === 'per_row' ? 'Per row' : 'Whole dataset'}</label>)}
    </div>
    {schema.data && <p>{schema.data.row_count} original rows. {mode === 'per_row' ? 'Each row runs one complete protocol; Agents share one driver and select their own columns.' : 'Each replicate runs the protocol with the whole dataset.'}</p>}
    {mode === 'per_row' && <div className="space-y-1" role="group" aria-label="Authorized columns">{[...new Set([...(schema.data?.columns ?? []), ...columns])].map(column => <label key={column} className="flex items-center gap-2 font-mono"><Checkbox checked={columns.includes(column)} disabled={disabled} onCheckedChange={checked => { setApplied(null); setColumns(current => checked ? [...current, column] : current.filter(item => item !== column)) }} />{column}</label>)}</div>}
    {error && <p role="alert" className="text-destructive">{error}</p>}
    <Button size="sm" disabled={!!error || disabled || !hasChanges} onClick={apply}>Apply</Button>
    {applied ? <p role="status" className="text-primary">{applied === 'per_row' ? 'Per row' : 'Whole dataset'} applied to the draft. Publish the experiment to update production runs and results.</p> : <p className="text-muted-foreground">Apply changes the draft connection. Publish the experiment to use it for production runs and results.</p>}
  </div>
}
