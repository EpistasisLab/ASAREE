import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { experimentsApi, type ResultsScope } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Card } from '@/components/ui/card'
import { cardAccent } from '@/lib/utils'
import { executionKeys, useExecutionSummary } from '@/lib/executionQueries'
import { EXECUTION_PAGE_SIZE, useCursor } from '@/lib/executionPagination'
import { displayFactorCondition } from '@/lib/experiment'
import type { ExecutionCell, ExecutionFilters, ExecutionSlot, ExecutionSummary, ExecutionUsageOptions } from '@/types/executionResults'
import type { Experiment } from '@/types/experiments'
import type { ResultsSelection } from './ResultsTab'
import { ResultsPanel } from './ResultsPanel'
import { ResultsProgress } from './ResultsProgress'
import { ResultsCellsTable } from './ResultsCellsTable'
import { formatUsageDistributionValue, type UsageMetric } from '@/lib/resultUsage'
import { ResultsMetadata } from './ResultsContext'
import { CellCard, ReplicateRow, ReplicateStatus } from './CellPresentation'
import { executionStatusColor, executionStatusCount, executionStatusPillColor, STATUS_PILL_SIZE } from '@/lib/executionStatus'
import { emptyExecutionFilters, filterParams, hasExecutionFilters, statusLabels, RunBrowserControls } from './RunBrowserControls'

const summaryStatuses = [
  '', 'not_started', 'pending', 'running', 'completed', 'failed', 'cancelled', 'limit_reached',
] as const

export function CursorControls({ next, back, onNext, onBack, busy, pageIndex = 0, count = 0, total, recordLabel = 'records' }: {
  next: string | null | undefined; back: boolean; onNext: (cursor: string) => void; onBack: () => void; busy: boolean
  pageIndex?: number; count?: number; total?: number; recordLabel?: 'cells' | 'replicates' | 'rows' | 'records'
}) {
  if (!next && !back && !count) return null
  const start = pageIndex * EXECUTION_PAGE_SIZE + 1
  return <div className="flex items-center justify-end gap-2 pt-2">
    {count > 0 && <span className="font-mono text-xs text-muted-foreground" aria-label="Records shown">Showing {start.toLocaleString()}–{(start + count - 1).toLocaleString()}{total !== undefined && ` of ${total.toLocaleString()}`} {recordLabel}</span>}
    {(next || back) && <>
    <Button size="xs" variant="outline" disabled={!back || busy} onClick={onBack}>Previous</Button>
    <Button size="xs" variant="outline" disabled={!next || busy} onClick={() => next && onNext(next)}>Next</Button>
    </>}
  </div>
}

export function ExecutionRows({ experimentId, scope, cellId, selectedSlotId, generation, summary, onSelect, actions, filters = emptyExecutionFilters, mode = 'results', usageOptions, executionSearch }: {
  experimentId: string; scope: ResultsScope; cellId?: string; generation?: number; summary?: ExecutionSummary; filters?: ExecutionFilters
  onSelect: (selection: ResultsSelection) => void; actions?: (row: ExecutionSlot) => React.ReactNode; mode?: 'runs' | 'results'
  selectedSlotId?: string
  usageOptions?: ExecutionUsageOptions
  executionSearch?: string
}) {
  const pager = useCursor()
  const params = { cell_id: cellId, cursor: pager.cursor, limit: EXECUTION_PAGE_SIZE, ...filterParams(filters) }
  const usageParams = {
    ...(usageOptions?.sortMetric ? { sort_metric: usageOptions.sortMetric, sort_direction: usageOptions.sortDirection ?? 'asc' } : {}),
    ...(usageOptions?.range ? { usage_metric: usageOptions.range.metric, usage_min: usageOptions.range.lower,
      usage_max: usageOptions.range.upper, usage_upper_inclusive: String(usageOptions.range.upper_inclusive) } : {}),
  }
  const requestParams = { ...params, ...usageParams, ...(executionSearch ? { execution_search: executionSearch } : {}) }
  const readoutMetric = usageOptions?.sortMetric ?? usageOptions?.range?.metric
  const filtered = !!(executionSearch || filters.status || filters.search || usageOptions?.range || usageOptions?.sortMetric)
  const query = useQuery({
    queryKey: [...executionKeys.root(experimentId), 'executions', scope, summary?.source_row_count, requestParams],
    queryFn: ({ signal }) => experimentsApi.listExecutions(experimentId, scope, requestParams, signal),
    // Refresh only visible pages when the summary publishes a newer generation.
    staleTime: 5000,
  })
  useGenerationRefresh(query.refetch, generation)
  return <div className="space-y-2">
    {query.isPending ? <Skeleton className="h-16" /> : query.error && !query.data ? <p role="alert" className="text-xs text-destructive">{query.error.message}</p> : <>
      <CursorControls next={query.data?.next_cursor} back={pager.back} onNext={pager.next} onBack={pager.previous} busy={query.isFetching} pageIndex={pager.pageIndex} count={query.data?.items.length} total={query.data?.total} recordLabel={summary?.unit === 'row' ? 'rows' : 'replicates'} />
      {!query.data?.items.length && <p className="text-xs text-muted-foreground">{filtered ? `No ${summary?.unit === 'row' ? 'rows' : 'replicates'} match these filters.` : `No ${summary?.unit === 'row' ? 'rows' : 'replicates'} planned in this version.`}</p>}
      <ul className="space-y-1.5">{query.data?.items.map(row => <ReplicateRow key={row.slot_id} number={row.replicate_number} results={mode === 'results' && !actions} selected={row.slot_id === selectedSlotId}
        onView={mode === 'results' && !actions && (row.run_id || row.status === 'completed') ? () => onSelect({ type: 'execution', slotId: row.slot_id, scope }) : undefined}
        detail={row.row_index !== null || readoutMetric ? <>{row.row_index !== null && <span className="font-mono">Source row {row.row_index + 1}</span>}
          {readoutMetric && <span className="font-mono">{readoutMetric === 'total_tokens' ? 'Tokens' : readoutMetric === 'cost_usd' ? 'Cost' : 'Duration'}: {formatUsageDistributionValue(readoutMetric, row.usage?.[readoutMetric] ?? null)}</span>}</> : undefined}
        status={<><ReplicateStatus status={row.status} filter={filters.status} />{row.result.truncated && row.status !== 'limit_reached' && <ReplicateStatus status="limit_reached" filter={filters.status} />}{row.result.evaluation_state === 'failed' && <ReplicateStatus status="scoring_failed" filter={filters.status} />}</>}
        error={row.error} actions={actions?.(row)} />)}</ul>
      {query.error && query.data && <p role="alert" className="text-xs text-destructive">Refresh failed; showing the last loaded page.</p>}
      {summary?.unit === 'row' && !filtered && !query.data?.items.length && <p className="text-xs text-muted-foreground">The published design forecasts {summary.expected ?? '?'} rows. Rows appear when the batch is planned.</p>}
    </>}
    {query.isError && <Button variant="outline" size="xs" disabled={query.isFetching} onClick={() => void query.refetch()}>Refresh</Button>}
  </div>
}

export function useGenerationRefresh(refetch: () => unknown, generation?: number) {
  const previous = useRef(generation)
  useEffect(() => {
    if (previous.current !== generation && previous.current !== undefined) void refetch()
    previous.current = generation
  }, [generation, refetch])
}

export function ExecutionBrowser({ experimentId, scope, experiment, mode, onSelect, controls, batchControls, actions, cellActions, selectedCellId: controlledSelectedCellId }: {
  experimentId: string; scope: ResultsScope; experiment?: Experiment; mode: 'runs' | 'results'
  onSelect: (selection: ResultsSelection) => void; controls?: React.ReactNode
  batchControls?: React.ReactNode
  actions?: (row: ExecutionSlot) => React.ReactNode
  cellActions?: (cell: ExecutionCell) => React.ReactNode
  selectedCellId?: string | null
}) {
  const summary = useExecutionSummary(experimentId, scope)
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [filters, setFilters] = useState<ExecutionFilters>(emptyExecutionFilters)
  const [attemptScope, setAttemptScope] = useState<'latest' | 'all'>('latest')
  const [metric, setMetric] = useState<UsageMetric>('total_tokens')
  const [internalSelectedCellId, setSelectedCellId] = useState<string>()
  const selectedCellId = controlledSelectedCellId === undefined ? internalSelectedCellId : controlledSelectedCellId ?? undefined
  const pager = useCursor()
  const params = { cursor: pager.cursor, limit: EXECUTION_PAGE_SIZE, ...filterParams(filters),
    ...(filters.resultStatus ? { result_status: filters.resultStatus } : {}),
    ...(mode === 'results' && attemptScope === 'all' ? { include_all_attempt_usage: 'true' } : {}) }
  const changeFilters = (next: ExecutionFilters) => { pager.reset(); setFilters(next) }
  const cells = useQuery({
    queryKey: [...executionKeys.root(experimentId), 'cells', scope, params],
    queryFn: ({ signal }) => experimentsApi.listExecutionCells(experimentId, scope, params, signal),
  })
  // A summary refresh does not change the stable label/replicate order.
  useGenerationRefresh(cells.refetch, summary.data?.generation)
  const refresh = () => { void summary.refetch(); void cells.refetch() }
  const selectCell = (cell: ExecutionCell) => {
    setSelectedCellId(cell.cell_id)
    onSelect({ type: 'cell', cellId: cell.cell_id, cellLabel: cell.cell_label, scope })
  }
  const cellBody = <div className="space-y-2">
    {summary.data?.unit === 'row' && <p className="text-xs text-muted-foreground">Per-row outcomes are reported individually. Aggregation, ranking and factorial analysis are unavailable.</p>}
    <RunBrowserControls filters={filters} onChange={changeFilters} />
    {cells.isPending ? <Skeleton className="h-20" /> : cells.error && !cells.data ? <p role="alert" className="text-xs text-destructive">{cells.error.message}</p> : <>
      <CursorControls next={cells.data?.next_cursor} back={pager.back} onNext={pager.next} onBack={pager.previous} busy={cells.isFetching} pageIndex={pager.pageIndex} count={cells.data?.items.length} total={cells.data?.total} recordLabel="cells" />
      {!cells.data?.items.length && <p className="text-xs text-muted-foreground">{hasExecutionFilters(filters) ? 'No cells match these filters.' : 'No generated cells in this version.'}</p>}
      {mode === 'results' ? !!cells.data?.items.length && <ResultsCellsTable cells={cells.data.items} metric={metric}
        designSpec={summary.data?.design_spec ?? experiment?.design_spec}
        attemptScope={attemptScope} unit={summary.data?.unit ?? 'replicate'} startIndex={pager.pageIndex * EXECUTION_PAGE_SIZE}
        onSelect={selectCell} />
        : cells.data?.items.map(cell => <CellCard key={cell.cell_id} label={cell.cell_label}
        cellNumber={cell.cell_number}
        conditions={Object.entries(cell.factor_values).map(([name, value]) => displayFactorCondition(name, value, summary.data?.design_spec ?? experiment?.design_spec, cell.cell_label))}
        replicateCount={cell.replicate_count ?? 0} expanded={expanded.has(cell.cell_id)} listId={`executions-${cell.cell_id}`}
        onToggle={() => {
          const opening = !expanded.has(cell.cell_id)
          setExpanded(current => { const next = new Set(current); if (opening) next.add(cell.cell_id); else next.delete(cell.cell_id); return next })
        }}
        progress={<>
          <span aria-label="Cell status counts" className="mt-1 flex w-full flex-wrap gap-1" title="Counts for the whole cell; use the overview tiles to filter replicates.">
            {summaryStatuses.filter(status => status && executionStatusCount(cell.counts, status) > 0).map(status =>
              <Badge key={status} variant="outline" className={`${STATUS_PILL_SIZE} border-current ${executionStatusPillColor(status, filters.status)}`}>
                {executionStatusCount(cell.counts, status).toLocaleString()} {statusLabels[status]}
              </Badge>)}
          </span>
        </>}
        badges={cell.truncated ? <Badge variant="outline" className={`${STATUS_PILL_SIZE} ${executionStatusPillColor('limit_reached', filters.status)}`}>{cell.truncated} hit iteration limit</Badge> : undefined}
        actions={cellActions?.(cell)}>
        {expanded.has(cell.cell_id) && <ExecutionRows key={`${summary.data?.scope_key}:${summary.data?.source_row_count}:${cell.cell_id}:${JSON.stringify(filters)}`} experimentId={experimentId} scope={scope} cellId={cell.cell_id} generation={summary.data?.generation} summary={summary.data} onSelect={onSelect} actions={actions} filters={filters} mode={mode} />}
      </CellCard>)}
      {cells.error && cells.data && <p role="alert" className="text-xs text-destructive">Refresh failed; showing the last loaded cells.</p>}
    </>}
  </div>
  const overview = () => <div className="w-full min-w-0 space-y-2"><div className="flex flex-wrap items-center gap-2"><h2 className="text-sm font-medium">{summary.data?.unit === 'row' ? 'Row' : 'Replicate'} overview</h2>{(summary.isError || cells.isError) && <Button variant="outline" size="xs" disabled={summary.isFetching || cells.isFetching} onClick={refresh}>Refresh</Button>}{controls && <div className="ml-auto flex flex-wrap items-center justify-end gap-2">{controls}</div>}</div>
    {summary.isPending ? <p className="text-xs text-muted-foreground">Loading progress…</p> : summary.error && !summary.data ? <p role="alert" className="text-xs text-destructive">Progress unavailable. Records remain available below.</p> : !summary.data?.ready ? <p role="status" className="text-xs text-muted-foreground">Preparing the version summary. Records remain available below.</p> : <>
      {mode === 'runs' && <ul aria-label="Run status counts" className="grid grid-cols-[repeat(auto-fill,minmax(8rem,1fr))] gap-1.5">
        {summaryStatuses.map(status => <li key={status}>
          <Card style={cardAccent('currentColor')} className={`h-full gap-0 bg-[color-mix(in_oklch,currentColor,transparent_94%)] p-0 text-center ${status ? executionStatusColor(status) : 'text-foreground'}`}>
            <Button variant="ghost" aria-label={`Filter by ${status ? statusLabels[status] : 'All'}`} aria-pressed={filters.status === status}
              onClick={() => changeFilters({ ...filters, status: filters.status === status ? '' : status })}
              className="h-auto w-full flex-col gap-0.5 whitespace-nowrap p-1.5 text-inherit hover:text-inherit aria-pressed:border-current aria-pressed:bg-[color-mix(in_oklch,currentColor,transparent_85%)]">
              <span className="text-base font-mono tabular-nums leading-none">{(status ? executionStatusCount(summary.data?.counts, status)
                : summary.data?.expected ?? summary.data?.total ?? 0).toLocaleString()}</span>
              <span className="text-[0.65rem] leading-snug">{status ? statusLabels[status] : 'All'}</span>
            </Button>
          </Card>
        </li>)}
      </ul>}
    </>}
    {summary.error && summary.data && <p role="alert" className="text-xs text-destructive">Progress refresh failed; showing the last summary.</p>}
  </div>
  const resultsNotice = <>{summary.isPending ? <p className="text-xs text-muted-foreground">Loading progress…</p> : summary.error && !summary.data ? <p role="alert" className="text-xs text-destructive">Progress unavailable. Records remain available below.</p> : !summary.data?.ready ? <p role="status" className="text-xs text-muted-foreground">Preparing the version summary. Records remain available below.</p> : null}{summary.error && summary.data && <p role="alert" className="text-xs text-destructive">Progress refresh failed; showing the last summary.</p>}{(summary.isError || cells.isError) && <Button variant="outline" size="xs" disabled={summary.isFetching || cells.isFetching} onClick={refresh}>Refresh</Button>}</>
  const metadata = mode === 'results' && summary.data?.ready ? <ResultsMetadata summary={summary.data} /> : null
  return mode === 'results' && experiment
    ? <ResultsPanel experimentId={experimentId} experimentName={experiment.name} scope={scope}
      progress={summary.data?.ready && <ResultsProgress summary={summary.data} cells={cells.data?.items ?? []}
        selectedMetric={metric} onMetricChange={setMetric} onSelectCell={selectCell} selectedCellId={selectedCellId}
        cellsLoading={cells.isPending} cellsError={cells.error?.message} attemptScope={attemptScope} onAttemptScopeChange={setAttemptScope}
        chartPagination={!!cells.data?.items.length && <div className="flex flex-wrap items-center justify-end gap-2">
          <span className="font-mono text-[11px] text-muted-foreground">Cells {(pager.pageIndex * EXECUTION_PAGE_SIZE + 1).toLocaleString()}–{(pager.pageIndex * EXECUTION_PAGE_SIZE + cells.data.items.length).toLocaleString()}{cells.data.total !== undefined && ` of ${cells.data.total.toLocaleString()}`}</span>
          {(pager.back || cells.data.next_cursor) && <>
            <Button size="xs" variant="outline" disabled={!pager.back || cells.isFetching} onClick={pager.previous}>Previous cells</Button>
            <Button size="xs" variant="outline" disabled={!cells.data.next_cursor || cells.isFetching} onClick={() => cells.data?.next_cursor && pager.next(cells.data.next_cursor)}>Next cells</Button>
          </>}
        </div>} />}
      metadata={metadata} controls={controls} notice={resultsNotice} cells={cellBody} />
    : <div className="@container space-y-3 p-3">
      {metadata}
      {batchControls && <div className="flex flex-wrap items-center justify-end gap-2">{batchControls}</div>}
      {mode === 'runs' ? <Card className="p-3">{overview()}</Card> : resultsNotice}{cellBody}</div>
}
