import { useQuery } from '@tanstack/react-query'
import { experimentsApi, type ResultsScope } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { ExecutionFilters } from '@/types/executionResults'

export const emptyExecutionFilters: ExecutionFilters = { search: '', status: '', factors: {} }
export const statusLabels: Record<string, string> = {
  not_started: 'Not started', pending: 'Queued', running: 'Running', finalizing: 'Finalizing',
  completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled', limit_reached: 'Hit iteration limit',
  scoring_failed: 'Scoring failed',
}
export function filterParams(filters: ExecutionFilters) {
  return { search: filters.search || undefined, status: filters.status || undefined,
    factors: Object.keys(filters.factors).length ? JSON.stringify(filters.factors) : undefined }
}
export function hasExecutionFilters(filters: ExecutionFilters) {
  return !!(filters.search || filters.status || Object.keys(filters.factors).length)
}
const selectClass = 'max-w-full rounded border bg-background px-2 py-1 text-xs'
export function RunBrowserControls({ experimentId, scope, filters, onChange }: {
  experimentId: string; scope: ResultsScope; filters: ExecutionFilters
  onChange: (filters: ExecutionFilters) => void
}) {
  const options = useQuery({ queryKey: ['experiments', experimentId, 'execution-results', 'factor-options', scope],
    queryFn: ({ signal }) => experimentsApi.getExecutionFactorOptions(experimentId, scope, signal), staleTime: 60000 })
  const updateFilters = (patch: Partial<ExecutionFilters>) => onChange({ ...filters, ...patch })
  return <div className="space-y-2">
    <Input type="search" aria-label="Search cells and factors" placeholder="Search cells or factor values…" maxLength={200}
      value={filters.search} onChange={event => updateFilters({ search: event.target.value })} />
    <div className="flex flex-wrap items-center gap-2">
      <select aria-label="Filter by status" className={selectClass} value={filters.status} onChange={event => updateFilters({ status: event.target.value })}>
        <option value="">All statuses</option>{Object.entries(statusLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select>
      {Object.entries(options.data?.factors ?? {}).map(([name, values]) => <select key={name} aria-label={`Filter ${name}`} className={selectClass}
        value={name in filters.factors ? JSON.stringify(filters.factors[name]) : ''} onChange={event => {
          const factors = { ...filters.factors }
          if (event.target.value) factors[name] = JSON.parse(event.target.value)
          else delete factors[name]
          updateFilters({ factors })
        }}><option value="">All {name}</option>{values.map(value => <option key={JSON.stringify(value)} value={JSON.stringify(value)}>{typeof value === 'string' ? value : JSON.stringify(value)}</option>)}</select>)}
      {hasExecutionFilters(filters) && <Button size="xs" variant="ghost" onClick={() => onChange(emptyExecutionFilters)}>Clear filters</Button>}
    </div>
    {options.error && <p role="alert" className="text-xs text-destructive">Factor filters unavailable: {options.error.message}</p>}
    {options.data?.truncated && <p className="text-xs text-muted-foreground">Some factor levels are omitted. Use search to find them.</p>}
    {hasExecutionFilters(filters) && <p className="text-xs text-muted-foreground">Matching cells and replicates across all pages{filters.status ? ` · ${statusLabels[filters.status]}` : ''}{filters.search ? ` · “${filters.search}”` : ''}{Object.entries(filters.factors).map(([name, value]) => ` · ${name}=${JSON.stringify(value)}`).join('')}</p>}
  </div>
}
