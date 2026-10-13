import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { experimentsApi, type ResultsScope } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Card } from '@/components/ui/card'
import { executionKeys, useExecutionSummary } from '@/lib/executionQueries'
import { useCursor } from '@/lib/executionPagination'
import { displayFactorCondition, formatMetricLabel, formatMetricValue } from '@/lib/experiment'
import type { ExecutionCell, ExecutionSlot, ExecutionSummary } from '@/types/executionResults'
import type { Experiment } from '@/types/experiments'
import type { ResultsSelection } from './ResultsTab'
import { ResultsPanel } from './ResultsPanel'
import { CellCard, ReplicateRow } from './CellPresentation'

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

export function ExecutionRows({ experimentId, scope, cellId, generation, summary, onSelect, actions }: {
  experimentId: string; scope: ResultsScope; cellId?: string; generation?: number; summary?: ExecutionSummary
  onSelect: (selection: ResultsSelection) => void; actions?: (row: ExecutionSlot) => React.ReactNode
}) {
  const pager = useCursor()
  const query = useQuery({
    queryKey: [...executionKeys.root(experimentId), 'executions', scope, cellId, pager.cursor],
    queryFn: ({ signal }) => experimentsApi.listExecutions(experimentId, scope, { cell_id: cellId, cursor: pager.cursor }, signal),
    // Refresh only visible pages when the summary publishes a newer generation.
    staleTime: 5000,
  })
  useGenerationRefresh(query.refetch, generation)
  return <div className="space-y-2">
    {query.isPending ? <Skeleton className="h-16" /> : query.error && !query.data ? <p role="alert" className="text-xs text-destructive">{query.error.message}</p> : <>
      {!query.data?.items.length && <p className="text-xs text-muted-foreground">No executions planned in this scope.</p>}
      <ul className="space-y-1.5">{query.data?.items.map(row => <ReplicateRow key={row.slot_id} number={row.replicate_number} results={!actions}
        onView={row.run_id || row.status === 'completed' ? () => onSelect({ type: 'execution', slotId: row.slot_id, scope }) : undefined}
        detail={<>{row.row_index !== null && <span className="font-mono">Source row {row.row_index + 1} · </span>}<Usage usage={row.usage ?? undefined} />
          {row.result.observations?.map(observation => <span key={observation.metric_id} className="block max-w-full truncate font-mono" title={observation.preview_truncated ? 'Preview; inspect for the complete value.' : undefined}>{observation.metric_name}: {observation.status === 'measured' ? observation.preview : observation.status}{observation.preview_truncated && '…'}</span>)}</>}
        status={<><Badge variant="outline">{row.status.replaceAll('_', ' ')}</Badge>{row.result.truncated && <Badge variant="outline" className="text-[color:var(--chart-4)]">Hit iteration limit</Badge>}</>}
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

export function ExecutionBrowser({ experimentId, scope, experiment, mode, onSelect, controls, actions, cellActions }: {
  experimentId: string; scope: ResultsScope; experiment?: Experiment; mode: 'runs' | 'results'
  onSelect: (selection: ResultsSelection) => void; controls?: React.ReactNode
  actions?: (row: ExecutionSlot) => React.ReactNode
  cellActions?: (cell: ExecutionCell) => React.ReactNode
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
  const cells = useQuery({
    queryKey: [...executionKeys.root(experimentId), 'cells', scope, pager.cursor, metric, sortDirection],
    queryFn: ({ signal }) => experimentsApi.listExecutionCells(experimentId, scope, { cursor: pager.cursor, metric, direction: sortDirection }, signal),
  })
  const previousGeneration = useRef(summary.data?.generation)
  const resetCells = pager.reset
  const refetchCells = cells.refetch
  useEffect(() => {
    if (previousGeneration.current !== summary.data?.generation) {
      resetCells()
      void refetchCells()
    }
    previousGeneration.current = summary.data?.generation
  }, [summary.data?.generation, resetCells, refetchCells])
  const metricKeys = summary.data?.unit === 'replicate' ? Object.keys(summary.data.metric_types) : []
  const refresh = () => { void summary.refetch(); void cells.refetch() }
  const cellBody = <div className="space-y-2">
    {summary.data?.unit === 'row' && <p className="text-xs text-muted-foreground">Per-row outcomes are reported individually. Aggregation, ranking and factorial analysis are unavailable.</p>}
    {mode === 'results' && metric && <p className="text-xs text-muted-foreground">Comparisons use completed, untruncated measurements. Rankings are provisional while measured counts differ.</p>}
    {mode === 'results' && metricKeys.length > 0 && <div className="flex flex-wrap gap-2 text-xs">
      <label>Compare metric <select aria-label="Choose comparison metric" className="rounded border bg-background p-1" value={metric ?? ''} onChange={event => { setMetricPreference(event.target.value); setDirectionPreference(undefined); pager.reset() }}><option value="">Condition order</option>{metricKeys.map(key => <option key={key} value={key}>{summary.data?.metric_labels?.[key] ?? formatMetricLabel(key)}</option>)}</select></label>
      {metric && <select aria-label="Sort direction" className="rounded border bg-background p-1" value={sortDirection} onChange={event => { setDirectionPreference(event.target.value); pager.reset() }}><option value="maximize">Highest first</option><option value="minimize">Lowest first</option></select>}
    </div>}
    {cells.isPending ? <Skeleton className="h-20" /> : cells.error && !cells.data ? <p role="alert" className="text-xs text-destructive">{cells.error.message}</p> : <>
      {!cells.data?.items.length && <p className="text-xs text-muted-foreground">No generated cells in this version.</p>}
      {mode === 'results' && metric && <div className="overflow-x-auto rounded border"><table className="w-full text-left text-xs"><thead className="border-b text-muted-foreground"><tr><th className="p-2">{ranking ? 'Rank' : '—'}</th><th className="p-2">Condition</th><th className="p-2">{summary.data?.metric_labels?.[metric] ?? formatMetricLabel(metric)}</th><th className="p-2">Measured</th></tr></thead><tbody>{cells.data?.items.map(cell => <tr key={cell.cell_id} className="border-b"><td className="p-2 font-mono">{ranking ? cell.primary_rank ?? '—' : '—'}</td><td className="p-2 font-mono">{cell.cell_label}</td><td className="p-2 font-mono">{cell.metric_values?.[metric] !== undefined ? formatMetricValue(metric, cell.metric_values[metric]) : 'Unavailable'}</td><td className="p-2 font-mono">{cell.metric_counts?.[metric] ?? 0}/{cell.replicate_count ?? '?'}</td></tr>)}</tbody></table></div>}
      {cells.data?.items.map(cell => <CellCard key={cell.cell_id} label={cell.cell_label} results={mode === 'results'}
        conditions={Object.entries(cell.factor_values).map(([name, value]) => displayFactorCondition(name, value, summary.data?.design_spec ?? experiment?.design_spec, cell.cell_label))}
        replicateCount={cell.replicate_count ?? 0} expanded={expanded.has(cell.cell_id)} listId={`executions-${cell.cell_id}`}
        onToggle={() => setExpanded(current => { const next = new Set(current); if (next.has(cell.cell_id)) next.delete(cell.cell_id); else next.add(cell.cell_id); return next })}
        progress={<><span>{cell.counts?.completed ?? 0}/{cell.total ?? '?'} complete</span><Usage usage={cell.usage} />{metric && cell.metric_values?.[metric] !== undefined && <span>{formatMetricValue(metric, cell.metric_values[metric])}</span>}</>}
        badges={cell.truncated ? <Badge variant="outline" className="text-[color:var(--chart-4)]">{cell.truncated} hit iteration limit</Badge> : undefined}
        actions={<>{cellActions?.(cell)}<Button size="xs" variant="outline" onClick={() => onSelect({ type: 'cell', cellId: cell.cell_id, cellLabel: cell.cell_label, scope })}>View results</Button></>}>
        {expanded.has(cell.cell_id) && <ExecutionRows key={`${summary.data?.scope_key}:${cell.cell_id}:${summary.data?.source_row_count}`} experimentId={experimentId} scope={scope} cellId={cell.cell_id} generation={summary.data?.generation} summary={summary.data} onSelect={onSelect} actions={actions} />}
      </CellCard>)}
      {cells.error && cells.data && <p role="alert" className="text-xs text-destructive">Refresh failed; showing the last loaded cells.</p>}
      <CursorControls next={cells.data?.next_cursor} back={pager.back} onNext={pager.next} onBack={pager.previous} busy={cells.isFetching} />
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
  return mode === 'results' && experiment
    ? <ResultsPanel experimentId={experimentId} experimentName={experiment.name} scope={scope} summary={overview} cells={cellBody} />
    : <div className="@container space-y-3 p-3"><Card className="p-3">{overview}</Card>{cellBody}</div>
}
