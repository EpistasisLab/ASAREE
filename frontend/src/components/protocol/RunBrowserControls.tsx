import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { ExecutionFilters } from '@/types/executionResults'
import { cellResultStatusLabels, executionStatusLabels as statusLabels } from '@/lib/executionStatus'

export { statusLabels }

export const emptyExecutionFilters: ExecutionFilters = { search: '', status: '' }
export function filterParams(filters: ExecutionFilters) {
  return { search: filters.search || undefined, status: filters.status || undefined }
}
export function hasExecutionFilters(filters: ExecutionFilters) {
  return !!(filters.search || filters.status || filters.resultStatus)
}
export function RunBrowserControls({ filters, onChange }: {
  filters: ExecutionFilters
  onChange: (filters: ExecutionFilters) => void
}) {
  const updateFilters = (patch: Partial<ExecutionFilters>) => onChange({ ...filters, ...patch })
  return <div className="space-y-2">
    <Input type="search" aria-label="Search cells and factors" placeholder="Search cells or factor values…" maxLength={200}
      value={filters.search} onChange={event => updateFilters({ search: event.target.value })} />
    <div className="flex flex-wrap items-center gap-2">
      {hasExecutionFilters(filters) && <Button size="xs" variant="ghost" onClick={() => onChange(emptyExecutionFilters)}>Clear filters</Button>}
    </div>
    {hasExecutionFilters(filters) && <p className="text-xs text-muted-foreground">Matching {filters.resultStatus ? 'cells' : 'cells and replicates'} across all pages{filters.resultStatus ? ` · ${cellResultStatusLabels[filters.resultStatus]}` : ''}{filters.status ? ` · ${statusLabels[filters.status]}` : ''}{filters.search ? ` · “${filters.search}”` : ''}</p>}
  </div>
}
