import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { ExecutionFilters } from '@/types/executionResults'

export const emptyExecutionFilters: ExecutionFilters = { search: '', status: '' }
export const statusLabels: Record<string, string> = {
  not_started: 'Not started', pending: 'Queued', running: 'Running', finalizing: 'Finalizing',
  completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled', limit_reached: 'Hit iteration limit',
  scoring_failed: 'Scoring failed',
}
export function filterParams(filters: ExecutionFilters) {
  return { search: filters.search || undefined, status: filters.status || undefined }
}
export function hasExecutionFilters(filters: ExecutionFilters) {
  return !!(filters.search || filters.status)
}
const selectClass = 'max-w-full rounded border bg-background px-2 py-1 text-xs'
export function RunBrowserControls({ filters, onChange }: {
  filters: ExecutionFilters
  onChange: (filters: ExecutionFilters) => void
}) {
  const updateFilters = (patch: Partial<ExecutionFilters>) => onChange({ ...filters, ...patch })
  return <div className="space-y-2">
    <Input type="search" aria-label="Search cells and factors" placeholder="Search cells or factor values…" maxLength={200}
      value={filters.search} onChange={event => updateFilters({ search: event.target.value })} />
    <div className="flex flex-wrap items-center gap-2">
      <select aria-label="Filter by status" className={selectClass} value={filters.status} onChange={event => updateFilters({ status: event.target.value })}>
        <option value="">All statuses</option>{Object.entries(statusLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select>
      {hasExecutionFilters(filters) && <Button size="xs" variant="ghost" onClick={() => onChange(emptyExecutionFilters)}>Clear filters</Button>}
    </div>
    {hasExecutionFilters(filters) && <p className="text-xs text-muted-foreground">Matching cells and replicates across all pages{filters.status ? ` · ${statusLabels[filters.status]}` : ''}{filters.search ? ` · “${filters.search}”` : ''}</p>}
  </div>
}
