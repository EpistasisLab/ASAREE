import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { protocolsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import type { DesignSpec, ExperimentRunResults, RowSummary } from '@/types/experiments'
import type { Protocol } from '@/types/protocols'
import { RunConfirmDialog } from './RunConfirmDialog'
import { DatasetRowCells } from './DatasetRowCells'
import { StopRowRuns } from './StopRowRuns'
import { ReplicateStatus } from './CellPresentation'
import { displayFactorCondition } from '@/lib/experiment'

function rowExecutionForecast(summary: RowSummary): string {
  return `${summary.cell_count} cells / ${summary.parent_replicate_count} replicates / ${summary.row_count ?? 'unknown'} rows / ${summary.expected ?? 'unknown'} executions`
}

export function DatasetRowRuns({ results, protocol, blocked, onInspect, designSpec }: {
  results: ExperimentRunResults; protocol: Protocol; blocked: boolean; onInspect?: (rowId: string) => void; designSpec?: DesignSpec | null
}) {
  const queryClient = useQueryClient()
  const [confirm, setConfirm] = useState<{ labels?: string[]; cellCount: number; rerunLabels: string[] } | null>(null)
  const [retrying, setRetrying] = useState<string | null>(null)
  const [page, setPage] = useState(0)
  const [selectedCellId, setSelectedCellId] = useState('')
  const [selectedReplicateId, setSelectedReplicateId] = useState('')
  const run = useMutation({
    mutationFn: () => protocolsApi.runCells(protocol.id, { replicateLabels: confirm?.labels, rerunReplicateLabels: confirm?.rerunLabels }),
    onSuccess: () => { setConfirm(null); queryClient.invalidateQueries({ queryKey: ['experiments', protocol.experiment_id] }) },
  })
  const summary = results.row_summary
  const rows = (results.row_results ?? []).filter(row => (!selectedCellId || row.cell_id === selectedCellId) && (!selectedReplicateId || row.replicate_result_id === selectedReplicateId))
  const currentPage = Math.min(page, Math.max(0, Math.ceil(rows.length / 20) - 1))
  const visibleRows = rows.slice(currentPage * 20, (currentPage + 1) * 20)
  const selectedReplicateCount = confirm?.labels?.length ?? summary?.parent_replicate_count ?? 0
  const selectedRows = (results.row_results ?? []).filter(row => !confirm?.labels || confirm.labels.includes(row.replicate_label))
  const runnableExecutions = Math.max(0, selectedReplicateCount * (summary?.row_count ?? 0) - selectedRows.filter(row => ['pending', 'running', 'finalizing'].includes(row.status) || !confirm?.rerunLabels.includes(row.replicate_label)).length)
  const cancel = useMutation({ mutationFn: (runId: string) => protocolsApi.cancelRun(protocol.id, runId), onSuccess: () => queryClient.invalidateQueries({ queryKey: ['experiments', protocol.experiment_id] }) })
  const retry = useMutation({
    mutationFn: (rowId: string) => protocolsApi.runCells(protocol.id, { retry_row_result_ids: [rowId] }),
    onMutate: rowId => setRetrying(rowId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['experiments', protocol.experiment_id] }),
    onSettled: () => setRetrying(null),
  })
  return <section className="@container space-y-3 p-3">
    <p className="font-mono text-xs">{summary ? rowExecutionForecast(summary) : 'Execution forecast unavailable: original source metadata unavailable.'}</p>
    {summary && <p className="text-xs text-muted-foreground" aria-live="polite">{summary.planned} planned · {summary.pending} pending · {summary.running} running · {summary.completed} completed · {summary.failed} failed · {summary.cancelled} cancelled · {summary.missing_reported} missing reported</p>}
    {[run.error, cancel.error, retry.error].filter(Boolean).map((error, index) => <p key={index} role="alert" className="text-xs text-destructive">{error?.message}</p>)}
    <DatasetRowCells results={results} designSpec={designSpec} selectedCellId={selectedCellId} onSelect={cellId => { setSelectedCellId(cellId); setSelectedReplicateId(''); setPage(0) }} onSelectReplicate={(cellId, replicateId) => { setSelectedCellId(cellId); setSelectedReplicateId(replicateId); setPage(0) }} onRun={(labels, cellCount, rerunLabels) => setConfirm({ labels, cellCount, rerunLabels })} runDisabled={blocked || !summary?.row_count || run.isPending}
      onStop={runIds => <StopRowRuns protocol={protocol} runIds={runIds} />}
      headerActions={<><Button size="sm" disabled={blocked || !summary?.row_count || !summary?.parent_replicate_count || run.isPending} onClick={() => {
        const labels = results.row_cells?.flatMap(cell => cell.replicates?.map(replicate => replicate.replicate_label) ?? [])
        const rerunLabels = [...new Set((results.row_results ?? []).filter(row => ['completed', 'failed', 'cancelled', 'limit_reached'].includes(row.status)).map(row => row.replicate_label))]
        setConfirm({ cellCount: summary?.cell_count ?? 0, labels: labels?.length ? labels : undefined, rerunLabels })
      }}>Run all cells</Button><StopRowRuns protocol={protocol} runIds={(results.row_results ?? []).filter(row => ['pending', 'running', 'finalizing'].includes(row.status) && row.latest_attempt).map(row => row.latest_attempt!.run_id)} /></>}
    />
    <h3 className="text-sm font-medium">{selectedCellId ? 'Cell row executions' : 'Row executions'}</h3>
    {!rows.length && <p className="text-xs text-muted-foreground">{selectedCellId ? 'No row executions planned for this cell.' : 'No row executions planned. Run all cells to create them.'}</p>}
    <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr>{['Cell', 'Source row', 'Replicate', 'Status', 'Actions'].map(label => <th key={label} className="p-2 text-left">{label}</th>)}</tr></thead><tbody>{visibleRows.map(row => <tr key={row.row_result_id} className="border-t"><td className="max-w-48 truncate p-2 font-mono" title={row.cell_label}>{Object.entries(row.factor_values ?? {}).map(([name, value]) => displayFactorCondition(name, value, designSpec, row.cell_label)).join(' · ') || 'No varying factors'}</td><td className="p-2 font-mono">{row.dataset_row.row_index + 1}</td><td className="p-2">{row.replicate_number}</td><td className="p-2"><ReplicateStatus status={row.status} /></td><td className="space-x-1 p-2">
      <Button size="xs" variant="outline" className="h-5 px-1.5 text-[0.65rem]" disabled={!row.latest_attempt || !onInspect} onClick={() => onInspect?.(row.row_result_id)}>View results</Button>
      {row.latest_attempt && ['pending', 'running', 'finalizing'].includes(row.status) && <Button size="xs" variant="outline" className="h-5 border-destructive/40 px-1.5 text-[0.65rem] text-destructive hover:bg-destructive/10 hover:text-destructive" disabled={cancel.isPending} onClick={() => cancel.mutate(row.latest_attempt!.run_id)}>Stop</Button>}
      {['failed', 'cancelled'].includes(row.status) && <Button size="xs" className="h-5 px-1.5 text-[0.65rem]" disabled={blocked || !!retrying || retry.isPending || row.protocol_revision_id !== protocol.published_revision_id} onClick={() => retry.mutate(row.row_result_id)}>Retry</Button>}
    </td></tr>)}</tbody></table></div>
    {rows.length > 20 && <div className="flex items-center gap-2"><Button size="sm" variant="outline" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</Button><span className="font-mono text-xs">Page {currentPage + 1} of {Math.ceil(rows.length / 20)}</span><Button size="sm" variant="outline" disabled={(currentPage + 1) * 20 >= rows.length} onClick={() => setPage(currentPage + 1)}>Next</Button></div>}
    {confirm && <RunConfirmDialog scope={{ type: 'selected-cells', rerunReplicateCount: confirm.rerunLabels.length, title: confirm.labels ? `Run ${confirm.labels.length} selected replicate${confirm.labels.length === 1 ? '' : 's'} across all rows?` : 'Run all cells across all rows?', cellCount: confirm.cellCount, replicateCount: selectedReplicateCount, pendingReplicateCount: Math.max(0, selectedReplicateCount - confirm.rerunLabels.length) }} confirmLabel={confirm.labels ? 'Run selected replicates' : 'Run all cells'} nodes={protocol.graph.nodes} edges={protocol.graph.edges} queryClient={queryClient} onCancel={() => setConfirm(null)} onConfirm={() => run.mutate()} hasUnpublishedChanges={protocol.has_unpublished_changes} publishedRevision={protocol.published_revision} confirmDisabled={blocked || run.isPending || !runnableExecutions} isConfirming={run.isPending} confirmError={run.error?.message} additionalContent={<div className="space-y-2"><p className="font-mono text-xs">{confirm.labels ? `${confirm.labels.length} replicates × ${summary?.row_count ?? '?'} rows = ${(summary?.row_count ?? 0) * confirm.labels.length} executions` : summary && rowExecutionForecast(summary)}</p><p className="text-xs text-muted-foreground">{runnableExecutions} executions will start. Active rows are skipped.{confirm.rerunLabels.length > 0 && ' Finished rows in the selected replicates will run again; previous attempts remain available.'}</p></div>} />}
  </section>
}
