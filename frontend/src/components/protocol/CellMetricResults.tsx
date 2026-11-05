import { displayCellLabel } from '@/lib/experiment'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { experimentsApi, type ResultsScope } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { OBSERVATION_LABELS } from '@/lib/measurementPlan'
import { executionKeys } from '@/lib/executionQueries'
import { formatCellMetric, type ResultMetric } from '@/lib/resultMetrics'
import type { CellMetricObservation, CellMetricObservationPage, ExecutionCell } from '@/types/executionResults'

function observationValue(observation: CellMetricObservation, metric: ResultMetric): string {
  if (observation.value_preview === null) return 'null'
  if (observation.value_truncated) return observation.value_preview
  try {
    const value: unknown = JSON.parse(observation.value_preview)
    if (typeof value === 'number' && metric.unit === 'tokens' && value >= 1_000_000) return formatCellMetric(value, metric)
    return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  } catch {
    return observation.value_preview
  }
}

function isNonNumeric(observation: CellMetricObservation): boolean {
  if (observation.value_truncated) return true
  try {
    const value: unknown = JSON.parse(observation.value_preview ?? 'null')
    return typeof value !== 'number' && typeof value !== 'boolean'
  } catch {
    return true
  }
}

function MetricCell({ cell, initialPage, pageSize, experimentId, scope, metric, generation, onSelectCell }: {
  cell: ExecutionCell; initialPage: CellMetricObservationPage['cells'][number]
  pageSize: number
  experimentId: string; scope: ResultsScope; metric: ResultMetric; generation: number
  onSelectCell: (cell: ExecutionCell) => void
}) {
  const [offset, setOffset] = useState(0)
  const more = useQuery({
    queryKey: [...executionKeys.root(experimentId), 'metric-observations', scope, metric.key, cell.cell_id, generation, offset],
    queryFn: ({ signal }) => experimentsApi.getCellMetricObservations(experimentId, scope, metric.key, [cell.cell_id], offset, signal),
    enabled: offset > 0,
  })
  const page = offset === 0 ? initialPage : more.data?.cells[0]
  return <Card size="sm" className="min-w-0 gap-2 p-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <Button variant="ghost" size="xs" className="font-mono" onClick={() => onSelectCell(cell)}>Cell {cell.cell_number ?? displayCellLabel(cell.factor_values, undefined, cell.cell_label)}</Button>
      {page && <span className="font-mono text-[11px] text-muted-foreground">{page.total > 0 ? `${offset + 1}–${offset + page.items.length} of ${page.total}` : '0'} executions</span>}
    </div>
    <p className="break-words font-mono text-[11px] text-muted-foreground">{displayCellLabel(cell.factor_values, undefined, cell.cell_label)}</p>
    {offset > 0 && more.isPending ? <p role="status" className="text-xs text-muted-foreground">Loading recorded measurements…</p>
      : more.error ? <div role="alert" className="text-xs text-destructive"><p>Could not load recorded measurements: {more.error.message}</p><Button size="xs" variant="outline" onClick={() => void more.refetch()}>Retry</Button></div>
        : !page?.items.length ? <p className="text-xs text-muted-foreground">No executions have been recorded for this cell in this version. Run the planned replicates to produce measurements.</p>
          : <ul className="divide-y">{page.items.map(observation => <li key={observation.slot_id} className="min-w-0 space-y-1 py-2 first:pt-0 last:pb-0">
            <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
              <span className="font-mono">Replicate {observation.replicate_number}{observation.row_index !== null && ` · Row ${observation.row_index + 1}`}</span>
              <span className="text-muted-foreground">{OBSERVATION_LABELS[observation.status as keyof typeof OBSERVATION_LABELS] ?? 'Not recorded'}</span>
            </div>
            {observation.status === 'measured' && <pre title={metric.unit === 'tokens' ? observation.value_preview ?? undefined : undefined} className="max-h-60 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{observationValue(observation, metric)}</pre>}
            {observation.value_truncated && <p className="text-[11px] text-muted-foreground">Preview truncated. Open the cell’s execution details for the complete recorded value.</p>}
            {observation.reason && <p className="whitespace-pre-wrap break-words text-xs text-muted-foreground">{observation.reason}</p>}
            {observation.status === 'measured' && metric.valueType && isNonNumeric(observation) && <p className="text-[11px] text-muted-foreground">This recorded value is not numeric, so it has no numeric cell aggregation.</p>}
            {observation.exclusion_reason && <p className="text-[11px] text-[color:var(--chart-4)]">{observation.exclusion_reason}</p>}
          </li>)}</ul>}
    {(offset > 0 || page && offset + page.items.length < page.total) && <div className="flex justify-end gap-2">
      <Button size="xs" variant="outline" disabled={offset === 0 || more.isFetching} onClick={() => setOffset(current => Math.max(0, current - pageSize))}>Previous measurements</Button>
      <Button size="xs" variant="outline" disabled={!page || offset + page.items.length >= page.total || more.isFetching} onClick={() => setOffset(current => current + pageSize)}>Next measurements</Button>
    </div>}
  </Card>
}

export function CellMetricResults({ cells, experimentId, scope, metric, generation, onSelectCell, cellsLoading = false, cellsError }: {
  cells: ExecutionCell[]; experimentId: string; scope: ResultsScope; metric: ResultMetric; generation: number
  onSelectCell: (cell: ExecutionCell) => void
  cellsLoading?: boolean; cellsError?: string
}) {
  const cellIds = cells.map(cell => cell.cell_id)
  const query = useQuery({
    queryKey: [...executionKeys.root(experimentId), 'metric-observations', scope, metric.key, cellIds, generation],
    queryFn: ({ signal }) => experimentsApi.getCellMetricObservations(experimentId, scope, metric.key, cellIds, 0, signal),
    enabled: cellIds.length > 0,
  })
  return <section aria-label={`${metric.label} recorded results`} className="space-y-2">
    <h3 className="text-xs font-medium">Recorded values and missing results · {metric.label}</h3>
    {cellsLoading ? <p role="status" className="text-xs text-muted-foreground">Loading cells…</p>
      : cellsError ? <p role="alert" className="text-xs text-destructive">Could not load cells: {cellsError}</p>
      : cells.length === 0 ? <p className="text-xs text-muted-foreground">No cells are available in the current version and filter selection.</p>
      : query.isPending ? <p role="status" className="text-xs text-muted-foreground">Loading recorded measurements and availability reasons…</p>
        : query.error ? <div role="alert" className="text-xs text-destructive"><p>Could not load metric results: {query.error.message}</p><Button size="xs" variant="outline" onClick={() => void query.refetch()}>Retry</Button></div>
          : cells.map(cell => <MetricCell key={`${metric.key}:${cell.cell_id}:${generation}`}
            cell={cell} initialPage={query.data.cells.find(page => page.cell_id === cell.cell_id) ?? { cell_id: cell.cell_id, total: 0, items: [] }}
            pageSize={query.data.limit} experimentId={experimentId} scope={scope} metric={metric} generation={generation} onSelectCell={onSelectCell} />)}
  </section>
}
