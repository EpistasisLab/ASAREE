import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { displayFactorCondition } from '@/lib/experiment'
import type { DesignSpec, ExperimentRunResults, RowCell } from '@/types/experiments'
import { CellCard, ReplicateRow, CellRunAction, ReplicateRunAction, ReplicateStatus } from './CellPresentation'

// Generated cells exist before any execution slots. Older API responses can
// still provide the cells represented by their rows, without fabricating slots.
function datasetRowCells(results: ExperimentRunResults): RowCell[] {
  if (results.row_cells) return results.row_cells
  const cells = new Map<string, RowCell>()
  const replicates = new Map<string, Set<string | number>>()
  for (const row of results.row_results ?? []) {
    if (!row.cell_id) continue
    cells.set(row.cell_id, { cell_id: row.cell_id, cell_label: row.cell_label, factor_values: row.factor_values ?? {}, replicate_count: 0 })
    const identities = replicates.get(row.cell_id) ?? new Set()
    identities.add(row.replicate_result_id ?? row.replicate_number)
    replicates.set(row.cell_id, identities)
  }
  return [...cells.values()].map(cell => ({ ...cell, replicate_count: replicates.get(cell.cell_id)?.size ?? 0 }))
}

export function DatasetRowCells({ results, selectedCellId, onSelect, designSpec, onSelectReplicate, onRun, onStop, runDisabled = false, headerActions, resultView = false }: {
  results: ExperimentRunResults; selectedCellId: string; onSelect: (cellId: string) => void; designSpec?: DesignSpec | null
  onSelectReplicate?: (cellId: string, replicateId: string) => void
  onRun?: (labels: string[], cellCount: number, rerunLabels: string[]) => void
  onStop?: (runIds: string[]) => React.ReactNode
  headerActions?: React.ReactNode
  runDisabled?: boolean
  resultView?: boolean
}) {
  const cells = datasetRowCells(results)
  const [page, setPage] = useState(0)
  const [expandedCells, setExpandedCells] = useState<Set<string>>(new Set())
  const currentPage = Math.min(page, Math.max(0, Math.ceil(cells.length / 20) - 1))
  if (!cells.length) return null
  return <section className="space-y-3" aria-label="Generated cells">
    <div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-medium">Cells</h3>{headerActions}<Button size="xs" variant="outline" aria-pressed={!selectedCellId} onClick={() => onSelect('')}>All cells</Button><span className="ml-auto font-mono text-xs text-muted-foreground">{cells.length}</span></div>
    <div className="space-y-2">{cells.slice(currentPage * 20, (currentPage + 1) * 20).map(cell => {
      const rows = (results.row_results ?? []).filter(row => row.cell_id === cell.cell_id)
      const rowCount = results.row_summary?.row_count
      const expected = rowCount == null ? null : rowCount * cell.replicate_count
      const completed = rows.filter(row => row.status === 'completed').length
      const failed = rows.filter(row => row.status === 'failed').length
      const expanded = expandedCells.has(cell.cell_id)
      const replicates = cell.replicates ?? [...new Map(rows.map(row => [row.replicate_result_id, {
        replicate_result_id: row.replicate_result_id, replicate_label: row.replicate_label,
        replicate_number: row.replicate_number,
      }])).values()]
      const orderedReplicates = [...replicates].sort((a, b) => a.replicate_number - b.replicate_number)
      const labels = orderedReplicates.map(replicate => replicate.replicate_label).filter(Boolean)
      const rerunLabels = orderedReplicates.filter(replicate => rows.some(row => row.replicate_result_id === replicate.replicate_result_id && ['completed', 'failed', 'cancelled', 'limit_reached'].includes(row.status))).map(replicate => replicate.replicate_label)
      const activeRuns = rows.filter(row => ['pending', 'running', 'finalizing'].includes(row.status) && row.latest_attempt).map(row => row.latest_attempt!.run_id)
      const conditions = Object.entries(cell.factor_values).map(([name, value]) => displayFactorCondition(name, value, designSpec, cell.cell_label))
      return <CellCard key={cell.cell_id} label={cell.cell_label} conditions={conditions} replicateCount={cell.replicate_count} results={resultView} expanded={expanded}
        listId={`row-cell-${cell.cell_id}-replicates`} toggleLabel={`View cell ${cell.cell_label}`}
        onToggle={() => { setExpandedCells(current => { const next = new Set(current); if (next.has(cell.cell_id)) next.delete(cell.cell_id); else next.add(cell.cell_id); return next }); onSelect(expanded ? '' : cell.cell_id) }}
        progress={<><span className="font-medium text-foreground">{completed}/{expected ?? '?'}</span> row executions complete · {rows.length} planned</>}
        badges={failed > 0 && <Badge variant="outline" className="shrink-0 border-destructive/50 text-destructive">{failed} failed</Badge>}
        actions={<>{resultView && <Button size="xs" variant="outline" onClick={() => onSelect(cell.cell_id)}>View results</Button>}{onRun && <CellRunAction disabled={runDisabled || !labels.length} onClick={() => onRun(labels, 1, rerunLabels)} label={`Run all replicates in ${cell.cell_label}`}>{rerunLabels.length === labels.length && labels.length ? 'Re-run all replicates' : 'Run all replicates'}</CellRunAction>}{onStop?.(activeRuns)}</>}>
          {resultView && <div className="mb-2 flex items-center justify-between"><p className="text-xs font-medium">Individual replicates</p><p className="text-[11px] text-muted-foreground">Select one for full output</p></div>}
          {!orderedReplicates.length && <p className="text-xs text-muted-foreground">Replicate details unavailable. Refresh after updating the server.</p>}
          <ul className="space-y-1.5" aria-label={`Replicates for ${cell.cell_label}`}>{orderedReplicates.map(replicate => {
            const replicateRows = rows.filter(row => row.replicate_result_id === replicate.replicate_result_id)
            const hasAttempt = replicateRows.some(row => Boolean(row.latest_attempt?.run_id))
            const done = replicateRows.filter(row => row.status === 'completed').length
            const active = replicateRows.filter(row => ['pending', 'running', 'finalizing'].includes(row.status)).length
            const errors = replicateRows.filter(row => ['failed', 'cancelled', 'limit_reached'].includes(row.status)).length
            const finished = rowCount != null && done === rowCount
            return <ReplicateRow key={replicate.replicate_result_id} number={replicate.replicate_number} results={resultView}
              detail={<>{done}/{rowCount ?? '?'} row executions complete{active > 0 && ` · ${active} active`}{errors > 0 && ` · ${errors} failed`}</>}
              status={<ReplicateStatus status={finished ? 'completed' : active ? 'running' : errors ? 'failed' : 'not_started'} />}
              onView={(hasAttempt || !!onRun) && onSelectReplicate ? () => onSelectReplicate(cell.cell_id, replicate.replicate_result_id) : undefined}
              viewAriaLabel={resultView ? `View ${cell.cell_label} replicate ${replicate.replicate_number}` : undefined}
              viewLabel={resultView ? 'View results' : 'View rows'}
              actions={<>{onRun && <ReplicateRunAction disabled={runDisabled || active > 0 || !replicate.replicate_label} onClick={() => onRun([replicate.replicate_label], 1, done > 0 || errors > 0 ? [replicate.replicate_label] : [])} label={`Run ${cell.cell_label} replicate ${replicate.replicate_number}`}>{done > 0 || errors > 0 ? 'Re-run' : 'Run'}</ReplicateRunAction>}{onStop?.(replicateRows.filter(row => ['pending', 'running', 'finalizing'].includes(row.status) && row.latest_attempt).map(row => row.latest_attempt!.run_id))}</>} />
          })}</ul>
      </CellCard>
    })}</div>
    {cells.length > 20 && <div className="flex items-center gap-2"><Button size="sm" variant="outline" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous cells</Button><span className="font-mono text-xs">{currentPage + 1}/{Math.ceil(cells.length / 20)}</span><Button size="sm" variant="outline" disabled={(currentPage + 1) * 20 >= cells.length} onClick={() => setPage(currentPage + 1)}>Next cells</Button></div>}
  </section>
}
