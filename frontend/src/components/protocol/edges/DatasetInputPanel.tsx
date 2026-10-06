import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useEdges, useNodes, useReactFlow } from '@xyflow/react'
import { datasetsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { datasetConfigIsFactorBound, datasetRowBindings, datasetRowFactorBindingError, datasetRowTopologyError } from '@/lib/datasetRows'
import type { DatasetInput, ProtocolEdge, ProtocolNode } from '@/types/protocols'

export function DatasetInputPanel({ edgeId, source, disabled = false }: { edgeId: string; source: string; disabled?: boolean }) {
  const { setEdges } = useReactFlow()
  const nodes = useNodes() as unknown as ProtocolNode[]
  const edges = useEdges() as unknown as ProtocolEdge[]
  const edge = edges.find(item => item.id === edgeId)
  const saved = edge?.data?.dataset_input
  const [mode, setMode] = useState<DatasetInput['mode']>(saved?.mode ?? 'whole_dataset')
  const [columns, setColumns] = useState(saved?.mode === 'per_row' ? saved.columns : [])
  const [columnsOpen, setColumnsOpen] = useState(false)
  const [applied, setApplied] = useState<DatasetInput['mode'] | null>(null)
  const [appliedKey, setAppliedKey] = useState(() => JSON.stringify(saved?.mode === 'per_row'
    ? ['per_row', [...saved.columns].sort()] : ['whole_dataset']))
  const inputKey = JSON.stringify(mode === 'per_row' ? [mode, [...columns].sort()] : [mode])
  const hasChanges = inputKey !== appliedKey
  const sourceNode = nodes.find(node => node.id === source)
  const id = (sourceNode?.data.config as { dataset_id?: string })?.dataset_id
  const factorBound = datasetConfigIsFactorBound(sourceNode)
  const otherRowDriver = datasetRowBindings({ nodes, edges }).find(binding =>
    binding.edge.id !== edgeId && binding.input.mode === 'per_row' &&
    binding.config?.dataset_id?.toLowerCase() !== id?.toLowerCase(),
  )
  const schema = useQuery({ queryKey: ['datasets', id, 'row-schema'], queryFn: () => datasetsApi.getRowSchema(id!), enabled: !!id })
  const availableColumns = schema.data?.columns ?? []
  const allColumnsSelected = availableColumns.length > 0 && availableColumns.every(column => columns.includes(column))
  const displayedColumns = [...new Set([...availableColumns, ...columns])]
  const columnPicker = <div className="space-y-2" role="group" aria-label="Authorized columns">
    <div className="flex items-center gap-2">
      <Button size="sm" variant="outline" disabled={disabled || !availableColumns.length || allColumnsSelected} onClick={() => { setApplied(null); setColumns([...availableColumns]) }}>All</Button>
      <Button size="sm" variant="outline" disabled={disabled || !columns.length} onClick={() => { setApplied(null); setColumns([]) }}>None</Button>
    </div>
    <div className="space-y-1">
      {displayedColumns.map(column => <label key={column} className="flex items-start gap-2 break-all font-mono"><Checkbox className="mt-0.5 shrink-0" checked={columns.includes(column)} disabled={disabled} onCheckedChange={checked => { setApplied(null); setColumns(current => checked ? [...current, column] : current.filter(item => item !== column)) }} />{column}</label>)}
    </div>
  </div>
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
      {(['whole_dataset', 'per_row'] as const).map(value => {
        const optionDisabled = disabled || (value === 'per_row' && (!!otherRowDriver || factorBound))
        return <label key={value} className={`flex items-center gap-2 py-1 ${optionDisabled ? 'cursor-not-allowed text-muted-foreground opacity-50' : ''}`}><input type="radio" name={`dataset-${edgeId}`} checked={mode === value} disabled={optionDisabled} onChange={() => { setMode(value); setApplied(null) }} />{value === 'per_row' ? 'Per row' : 'Whole dataset'}</label>
      })}
    </div>
    {otherRowDriver && <p className="text-muted-foreground">Per row is unavailable because another dataset already uses it. All per-row inputs must use the same dataset. Use Whole dataset here, or switch the other dataset to Whole dataset first.</p>}
    {factorBound && <p className="text-muted-foreground">{datasetRowFactorBindingError}</p>}
    {schema.data && <p>{schema.data.row_count} original rows. {mode === 'per_row' ? 'Each row runs one complete protocol; Agents share one driver and select their own columns.' : 'Each replicate runs the protocol with the whole dataset.'}</p>}
    {mode === 'per_row' && (displayedColumns.length <= 10 ? columnPicker : <div className="space-y-2">
      <p className="text-muted-foreground">{columns.length} of {availableColumns.length} columns selected</p>
      <Dialog open={columnsOpen} onOpenChange={setColumnsOpen}>
        <DialogTrigger render={<Button size="sm" variant="outline" disabled={disabled} />}>Select columns</DialogTrigger>
        <DialogContent>
          <DialogHeader><DialogTitle>Select dataset columns</DialogTitle></DialogHeader>
          <p className="text-xs text-muted-foreground">{columns.length} of {availableColumns.length} columns selected</p>
          <div className="max-h-[50vh] overflow-y-auto p-1 text-xs">{columnPicker}</div>
          <DialogFooter><Button size="sm" onClick={() => setColumnsOpen(false)}>Done</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>)}
    {error && !(factorBound && error === datasetRowFactorBindingError) && <p role="alert" className="text-destructive">{error}</p>}
    <Button size="sm" disabled={!!error || disabled || !hasChanges} onClick={apply}>Apply</Button>
    {applied ? <p role="status" className="text-primary">{applied === 'per_row' ? 'Per row' : 'Whole dataset'} applied to the draft. Publish the experiment to update production runs and results.</p> : <p className="text-muted-foreground">Apply changes the draft connection. Publish the experiment to use it for production runs and results.</p>}
  </div>
}
