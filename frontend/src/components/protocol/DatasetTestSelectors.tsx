import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { connectedDatasets, datasetId } from '@/lib/datasetFactors'
import type { useDatasetTestSelection } from './useDatasetTestSelection'

export function DatasetTestSelectors({ selection }: { selection: ReturnType<typeof useDatasetTestSelection> }) {
  return <div className="space-y-2">{selection.owners.map((node) => {
    const options = connectedDatasets(selection.graph!, node.id)
    if (node.data.dataset_factor_mode === 'dataset_toggle') return <div key={node.id} className="space-y-1">
      <p className="text-xs">{node.data.label}: datasets for this test</p>
      <Select value={selection.selections[node.id]} onValueChange={(value) => value && selection.setChoice(node.id, value)}>
        <SelectTrigger className="w-full"><SelectValue>{selection.selections[node.id] === 'all' ? 'All enabled' : 'All disabled'}</SelectValue></SelectTrigger>
        <SelectContent><SelectItem value="all">All enabled</SelectItem><SelectItem value="none">All disabled</SelectItem></SelectContent>
      </Select>
    </div>
    return <div key={node.id} className="space-y-1">
      <p className="text-xs">{node.data.label}: dataset for this test</p>
      <Select value={selection.selections[node.id]} onValueChange={(id) => id && selection.setChoice(node.id, id)}>
        <SelectTrigger className="w-full"><SelectValue>{() => {
          const selected = options.find((option) => datasetId(option) === selection.selections[node.id])
          return String(selected?.data.label ?? 'Choose a dataset…')
        }}</SelectValue></SelectTrigger>
        <SelectContent>{options.map((option) => <SelectItem key={option.id} value={datasetId(option) || option.id} disabled={!selection.library.data?.some((dataset) => dataset.id === datasetId(option))}>{String(option.data.label)}{selection.library.isSuccess && !selection.library.data.some((dataset) => dataset.id === datasetId(option)) ? ' (unavailable)' : ''}</SelectItem>)}</SelectContent>
      </Select>
    </div>
  })}{selection.error && <p role="alert" className="text-xs text-destructive">{selection.error}</p>}</div>
}
