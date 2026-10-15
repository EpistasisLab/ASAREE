import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError, experimentsApi, type ResultsScope } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Card } from '@/components/ui/card'
import { executionKeys, useExecutionSummary } from '@/lib/executionQueries'
import { useCursor } from '@/lib/executionPagination'
import { displayFactorCondition, formatMetricLabel, formatMetricValue } from '@/lib/experiment'
import type { ExecutionCell, ExecutionFilters, ExecutionSlot, ExecutionSummary, ExecutionReplicate } from '@/types/executionResults'
import type { Experiment } from '@/types/experiments'
import type { ResultsSelection } from './ResultsTab'
import { ResultsPanel } from './ResultsPanel'
import { CellCard, ReplicateRow } from './CellPresentation'
import { emptyExecutionFilters, filterParams, hasExecutionFilters, statusLabels, type RunBrowserSettings } from './RunBrowserControls'

export function CursorControls({ next, back, onNext, onBack, busy }: {
  next: string | null | undefined; back: boolean; onNext: (cursor: string) => void; onBack: () => void; busy: boolean
}) {
  return <div className="flex items-center justify-end gap-2 pt-2">
    <Button size="xs" variant="outline" disabled={!back || busy} onClick={onBack}>Previous</Button>
    <Button size="xs" variant="outline" disabled={!next || busy} onClick={() => next && onNext(next)}>Next</Button>
  </div>
}

function Usage({ usage }: { usage: ExecutionSummary['usage'] }) {
  if (!usage) return null
  return <span className="font-mono text-xs text-muted-foreground">
    {usage.cost_usd != null && `${new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(usage.cost_usd)} · `}
    {usage.total_tokens != null && `${usage.total_tokens.toLocaleString()} tokens`}
  </span>
}

export function ExecutionRows({ experimentId, scope, cellId, generation, summary, onSelect, actions,
  filters = emptyExecutionFilters, sort = 'number', sortDirection = 'asc', replicateId, jobId, replicateActions }: {
  experimentId: string; scope: ResultsScope; cellId?: string; generation?: number; summary?: ExecutionSummary
  onSelect: (selection: ResultsSelection) => void; actions?: (row: ExecutionSlot) => React.ReactNode
  filters?: ExecutionFilters; sort?: string; sortDirection?: string; replicateId?: string; jobId?: string
  replicateActions?: (replicate: ExecutionReplicate) => React.ReactNode
}) {
  const pager = useCursor()
  const grouped = summary?.unit === 'row' && !replicateId && !!cellId
  const params = { cell_id: cellId, replicate_id: replicateId, cursor: pager.cursor,
    ...filterParams(filters), sort, sort_direction: sortDirection, job_id: jobId }
  const query = useQuery({
    queryKey: [...executionKeys.root(experimentId), 'executions', scope, params],
    queryFn: ({ signal }) => experimentsApi.listExecutions(experimentId, scope, params, signal),
    enabled: !grouped,
    // Refresh only visible pages when the summary publishes a newer generation.
    staleTime: 5000,
  })
  useGenerationRefresh(query.refetch, grouped ? undefined : generation)
  const parents = useQuery({ queryKey: [...executionKeys.root(experimentId), 'replicates', scope, params],
    queryFn: ({ signal }) => experimentsApi.listExecutionReplicates(experimentId, scope, params, signal), enabled: grouped })
  useGenerationRefresh(parents.refetch, grouped ? generation : undefined)
  const [expandedReplicates, setExpandedReplicates] = useState<Set<string>>(() => new Set())
  if (grouped) return <div className="space-y-2">
    {parents.isPending ? <Skeleton className="h-16" /> : parents.error ? <p role="alert" className="text-xs text-destructive">{parents.error.message}</p> : <>
      {!parents.data?.items.length && <p className="text-xs text-muted-foreground">No matching replicates.</p>}
      {parents.data?.items.map(parent => <div key={parent.replicate_id} className="space-y-2 rounded border p-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button variant="ghost" size="xs" aria-expanded={expandedReplicates.has(parent.replicate_id)} onClick={() => setExpandedReplicates(current => {
            const next = new Set(current); if (next.has(parent.replicate_id)) next.delete(parent.replicate_id); else next.add(parent.replicate_id); return next
          })}>Replicate {parent.replicate_number}</Button>
          {replicateActions?.(parent)}
        </div>
        <p className="text-xs text-muted-foreground">{filters.status ? `${parent.matching} matching · ` : ''}{parent.total} dataset rows · {parent.counts.failed ?? 0} failed · {parent.counts.completed ?? 0} completed</p>
        {expandedReplicates.has(parent.replicate_id) && <ExecutionRows key={`${parent.replicate_id}:${filters.status}`} experimentId={experimentId} scope={scope} cellId={cellId}
          replicateId={parent.replicate_id} generation={generation} summary={summary} onSelect={onSelect} actions={actions}
          filters={filters} sort={sort} sortDirection={sortDirection} jobId={jobId} />}
      </div>)}
      <CursorControls next={parents.data?.next_cursor} back={pager.back} onNext={pager.next} onBack={pager.previous} busy={parents.isFetching} />
    </>}
  </div>
  return <div className="space-y-2">
    {query.isPending ? <Skeleton className="h-16" /> : query.error && !query.data ? <p role="alert" className="text-xs text-destructive">{query.error.message}</p> : <>
      {!query.data?.items.length && <p className="text-xs text-muted-foreground">No executions planned in this scope.</p>}
      <ul className="space-y-1.5">{query.data?.items.map(row => <ReplicateRow key={row.slot_id} number={row.replicate_number} results={!actions}
        onView={row.run_id || row.status === 'completed' ? () => onSelect({ type: 'execution', slotId: row.slot_id, scope }) : undefined}
        detail={<>{row.row_index !== null && <span className="font-mono">Dataset row {row.row_index + 1} · </span>}{!!row.attempt_count && <span>Attempt {row.attempt_count}{row.status !== 'completed' && row.has_previous_success ? ' · Earlier attempt succeeded' : ''}</span>}<Usage usage={row.usage ?? undefined} />
          {row.result.observations?.map(observation => <span key={observation.metric_id} className="block max-w-full truncate font-mono" title={observation.preview_truncated ? 'Preview; inspect for the complete value.' : undefined}>{observation.metric_name}: {observation.status === 'measured' ? observation.preview : observation.status}{observation.preview_truncated && '…'}</span>)}</>}
        status={<><Badge variant="outline">{statusLabels[row.status] ?? row.status.replaceAll('_', ' ')}</Badge>{row.result.truncated && <Badge variant="outline" className="text-[color:var(--chart-4)]">Hit iteration limit</Badge>}{row.result.evaluation_state === 'failed' && <Badge variant="outline">Scoring failed</Badge>}</>}
        error={row.error} actions={actions?.(row)} />)}</ul>
      {query.error && query.data && <p role="alert" className="text-xs text-destructive">Refresh failed; showing the last loaded page.</p>}
      <CursorControls next={query.data?.next_cursor} back={pager.back} onNext={pager.next} onBack={pager.previous} busy={query.isFetching} />
      {summary?.unit === 'row' && !query.data?.items.length && <p className="text-xs text-muted-foreground">The published design forecasts {summary.expected ?? '?'} row executions. Rows appear when the batch is planned.</p>}
    </>}
  </div>
}

function useGenerationRefresh(refetch: () => unknown, generation?: number) {
  const previous = useRef(generation)
  useEffect(() => {
    if (previous.current !== generation && previous.current !== undefined) void refetch()
    previous.current = generation
  }, [generation, refetch])
}

function isStaleSortCursor(error: unknown) {
  return error instanceof ApiError && error.status === 422 && error.detail === 'stale_sort_cursor'
}

export function ExecutionBrowser({ experimentId, scope, experiment, mode, onSelect, controls, actions, cellActions,
  settings, onExpand, jobId, replicateActions }: {
  experimentId: string; scope: ResultsScope; experiment?: Experiment; mode: 'runs' | 'results'
  onSelect: (selection: ResultsSelection) => void; controls?: React.ReactNode
  actions?: (row: ExecutionSlot) => React.ReactNode
  cellActions?: (cell: ExecutionCell) => React.ReactNode
  settings?: RunBrowserSettings; onExpand?: (ids: string[]) => void; jobId?: string
  replicateActions?: (replicate: ExecutionReplicate) => React.ReactNode
}) {
  const summary = useExecutionSummary(experimentId, scope)
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [metricPreference, setMetricPreference] = useState<string>()
  const metric = mode === 'results' ? (metricPreference ?? summary.data?.primary_metric) || undefined : undefined
  const [directionPreference, setDirectionPreference] = useState<string>()
  const direction = directionPreference ?? summary.data?.metric_directions[metric ?? ''] ?? 'maximize'
  const sortDirection = direction === 'minimize' ? 'minimize' : 'maximize'
  const ranking = metric === summary.data?.primary_metric && sortDirection === summary.data?.primary_metric_direction
  const pager = useCursor()
  const filters = settings?.filters ?? emptyExecutionFilters
  const params = { cursor: pager.cursor, metric, direction: sortDirection, ...filterParams(filters),
    sort: settings?.sort, sort_direction: settings?.sortDirection, job_id: jobId }
  const cells = useQuery({
    queryKey: [...executionKeys.root(experimentId), 'cells', scope, params],
    queryFn: ({ signal }) => experimentsApi.listExecutionCells(experimentId, scope, params, signal),
    retry: (failureCount, error) => !isStaleSortCursor(error) && failureCount < 3,
  })
  // Progress updates do not change condition-order cursors. Ranked cursors may
  // expire, but restarting that pagination must be an explicit user action.
  useGenerationRefresh(cells.refetch, summary.data?.generation)
  const staleRanking = isStaleSortCursor(cells.error)
  const metricKeys = summary.data?.unit === 'replicate' ? Object.keys(summary.data.metric_types) : []
  const refresh = () => { void summary.refetch(); void cells.refetch() }
  const cellBody = <div className="space-y-2">
    {summary.data?.unit === 'row' && <p className="text-xs text-muted-foreground">Per-row outcomes are reported individually. Aggregation, ranking and factorial analysis are unavailable.</p>}
    {mode === 'results' && metric && <p className="text-xs text-muted-foreground">Comparisons use completed, untruncated measurements. Rankings are provisional while measured counts differ.</p>}
    {mode === 'results' && metricKeys.length > 0 && <div className="flex flex-wrap gap-2 text-xs">
      <label>Compare metric <select aria-label="Choose comparison metric" className="rounded border bg-background p-1" value={metric ?? ''} onChange={event => { setMetricPreference(event.target.value); setDirectionPreference(undefined); pager.reset() }}><option value="">Condition order</option>{metricKeys.map(key => <option key={key} value={key}>{summary.data?.metric_labels?.[key] ?? formatMetricLabel(key)}</option>)}</select></label>
      {metric && <select aria-label="Sort direction" className="rounded border bg-background p-1" value={sortDirection} onChange={event => { setDirectionPreference(event.target.value); pager.reset() }}><option value="maximize">Highest first</option><option value="minimize">Lowest first</option></select>}
    </div>}
    {staleRanking && <div role="status" className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span>The ranking has changed. Reload from the first page to continue.</span>
      <Button size="xs" variant="outline" onClick={pager.reset}>Reload ranking</Button>
    </div>}
    {cells.isPending ? <Skeleton className="h-20" /> : cells.error && !cells.data ? !staleRanking && <p role="alert" className="text-xs text-destructive">{cells.error.message}</p> : <>
      {!cells.data?.items.length && <p className="text-xs text-muted-foreground">{hasExecutionFilters(filters) ? 'No cells match these filters. Clear filters to see all cells.' : jobId ? 'No eligible executions in this batch.' : 'No generated cells in this version.'}</p>}
      {mode === 'results' && metric && <div className="overflow-x-auto rounded border"><table className="w-full text-left text-xs"><thead className="border-b text-muted-foreground"><tr><th className="p-2">{ranking ? 'Rank' : '—'}</th><th className="p-2">Condition</th><th className="p-2">{summary.data?.metric_labels?.[metric] ?? formatMetricLabel(metric)}</th><th className="p-2">Measured</th></tr></thead><tbody>{cells.data?.items.map(cell => <tr key={cell.cell_id} className="border-b"><td className="p-2 font-mono">{ranking ? cell.primary_rank ?? '—' : '—'}</td><td className="p-2 font-mono">{cell.cell_label}</td><td className="p-2 font-mono">{cell.metric_values?.[metric] !== undefined ? formatMetricValue(metric, cell.metric_values[metric]) : 'Unavailable'}</td><td className="p-2 font-mono">{cell.metric_counts?.[metric] ?? 0}/{cell.replicate_count ?? '?'}</td></tr>)}</tbody></table></div>}
      {cells.data?.items.map(cell => <CellCard key={cell.cell_id} label={cell.cell_label} results={mode === 'results'}
        conditions={Object.entries(cell.factor_values).map(([name, value]) => displayFactorCondition(name, value, summary.data?.design_spec ?? experiment?.design_spec, cell.cell_label))}
        replicateCount={jobId ? cell.matching_replicate_count ?? 0 : cell.replicate_count ?? 0} expanded={settings ? settings.expanded.includes(cell.cell_id) : expanded.has(cell.cell_id)} listId={`executions-${jobId ?? 'live'}-${cell.cell_id}`}
        onToggle={() => { if (settings && onExpand) { onExpand(settings.expanded.includes(cell.cell_id) ? settings.expanded.filter(id => id !== cell.cell_id) : [...settings.expanded, cell.cell_id]); return }
          setExpanded(current => { const next = new Set(current); if (next.has(cell.cell_id)) next.delete(cell.cell_id); else next.add(cell.cell_id); return next }) }}
        progress={<>{jobId ? <span>{cell.matching_count ?? 0} selected executions</span> : <span>{cell.counts?.completed ?? 0}/{cell.total ?? '?'} complete · {cell.counts?.failed ?? 0} failed</span>}{filters.status && <span>{cell.matching_count ?? 0} {statusLabels[filters.status]?.toLowerCase()} · {cell.replicate_count ?? '?'} replicates total</span>}<Usage usage={cell.usage} />{metric && cell.metric_values?.[metric] !== undefined && <span>{formatMetricValue(metric, cell.metric_values[metric])}</span>}</>}
        badges={cell.truncated ? <Badge variant="outline" className="text-[color:var(--chart-4)]">{cell.truncated} hit iteration limit</Badge> : undefined}
        actions={<>{cellActions?.(cell)}{!jobId && <Button size="xs" variant="outline" onClick={() => onSelect({ type: 'cell', cellId: cell.cell_id, cellLabel: cell.cell_label, scope })}>View results</Button>}</>}>
        {(settings ? settings.expanded.includes(cell.cell_id) : expanded.has(cell.cell_id)) && <FilteredCellRows key={`${summary.data?.scope_key}:${cell.cell_id}:${JSON.stringify(filters)}:${settings?.replicateSort}:${settings?.replicateDirection}`}
          experimentId={experimentId} scope={scope} cellId={cell.cell_id} generation={summary.data?.generation} summary={summary.data}
          onSelect={onSelect} actions={actions} filters={filters} sort={settings?.replicateSort} sortDirection={settings?.replicateDirection} jobId={jobId} replicateActions={replicateActions} />}
      </CellCard>)}
      {cells.error && cells.data && !staleRanking && <p role="alert" className="text-xs text-destructive">Refresh failed; showing the last loaded cells.</p>}
      <CursorControls next={cells.data?.next_cursor} back={pager.back} onNext={pager.next} onBack={pager.previous} busy={cells.isFetching || staleRanking} />
    </>}
  </div>
  const overview = <div className="space-y-2"><div className="flex flex-wrap items-center gap-2"><h2 className="text-sm font-medium">{mode === 'runs' ? 'Production runs' : 'Current results'}</h2><Button variant="outline" size="xs" onClick={refresh}>Refresh</Button>{controls}</div>
    {summary.isPending ? <p className="text-xs text-muted-foreground">Loading progress…</p> : summary.error && !summary.data ? <p role="alert" className="text-xs text-destructive">Progress unavailable. Execution records remain available below.</p> : !summary.data?.ready ? <p role="status" className="text-xs text-muted-foreground">Preparing the version summary. Execution records remain available below.</p> : <>
      <p className="font-mono text-xs">{summary.data.counts?.completed ?? 0}/{summary.data.expected ?? summary.data.total ?? 0} {summary.data.unit === 'row' ? 'row executions' : 'replicates'} complete · {summary.data.counts?.running ?? 0} running · {summary.data.counts?.pending ?? 0} queued · {summary.data.counts?.failed ?? 0} failed</p>
      <Usage usage={summary.data.usage} />
      {summary.data.all_attempt_usage?.cost_usd != null && <p className="font-mono text-xs text-muted-foreground">All-attempt spend: {new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(summary.data.all_attempt_usage.cost_usd)}</p>}
      <p className="text-xs text-muted-foreground">Usage reported for {summary.data.usage_complete_count ?? 0}/{summary.data.total ?? 0} current executions.{summary.data.refreshing && ' Updating summary…'}</p>
      {!!summary.data.usage_pending_count && <p role="status" className="text-xs text-muted-foreground">Preparing usage for {summary.data.usage_pending_count} executions. Execution records and evidence remain available.</p>}
    </>}
    {summary.error && summary.data && <p role="alert" className="text-xs text-destructive">Progress refresh failed; showing the last summary.</p>}
  </div>
  if (jobId) return <div className="@container space-y-3">{controls}{cellBody}</div>
  return mode === 'results' && experiment
    ? <ResultsPanel experimentId={experimentId} experimentName={experiment.name} scope={scope} summary={overview} cells={cellBody} />
    : <div className="@container space-y-3 p-3"><Card className="p-3">{overview}</Card>{cellBody}</div>
}

function FilteredCellRows(props: React.ComponentProps<typeof ExecutionRows>) {
  const [showAll, setShowAll] = useState(false)
  return <div className="space-y-2">
    {props.filters?.status && <Button size="xs" variant="ghost" onClick={() => setShowAll(value => !value)}>{showAll ? 'Show matching replicates' : 'Show all replicates'}</Button>}
    <ExecutionRows key={String(showAll)} {...props} filters={showAll ? { ...props.filters!, status: '' } : props.filters} />
  </div>
}
