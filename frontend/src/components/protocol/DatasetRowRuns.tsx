import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { protocolsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import type { ExperimentRunResults, RowSummary } from '@/types/experiments'
import type { Protocol } from '@/types/protocols'
import { RunConfirmDialog } from './RunConfirmDialog'

export function rowExecutionForecast(summary: RowSummary): string {
  return `${summary.cell_count} cells / ${summary.parent_replicate_count} replicates / ${summary.row_count ?? 'unknown'} rows / ${summary.expected ?? 'unknown'} executions`
}

export function DatasetRowRuns({ results, protocol, blocked, onInspect }: {
  results: ExperimentRunResults; protocol: Protocol; blocked: boolean; onInspect?: (rowId: string) => void
}) {
  const queryClient = useQueryClient()
  const [confirm, setConfirm] = useState(false)
  const [retrying, setRetrying] = useState<string | null>(null)
  const [page, setPage] = useState(0)
  const run = useMutation({
    mutationFn: () => protocolsApi.runCells(protocol.id),
    onSuccess: () => { setConfirm(false); queryClient.invalidateQueries({ queryKey: ['experiments', protocol.experiment_id] }) },
  })
  const summary = results.row_summary
  const rows = results.row_results ?? []
  const currentPage = Math.min(page, Math.max(0, Math.ceil(rows.length / 20) - 1))
  const visibleRows = rows.slice(currentPage * 20, (currentPage + 1) * 20)
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
    <Button disabled={blocked || !summary || summary.expected === null || !summary.row_count || !summary.parent_replicate_count || run.isPending} onClick={() => setConfirm(true)}>Run all cells</Button>
    {[run.error, cancel.error, retry.error].filter(Boolean).map((error, index) => <p key={index} role="alert" className="text-xs text-destructive">{error?.message}</p>)}
    <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr>{['Cell', 'Source row', 'Replicate', 'Status', 'Publication', 'Actions'].map(label => <th key={label} className="p-2 text-left">{label}</th>)}</tr></thead><tbody>{visibleRows.map(row => <tr key={row.row_result_id} className="border-t"><td className="p-2 font-mono">{row.cell_label}</td><td className="p-2 font-mono">{row.dataset_row.row_index + 1}</td><td className="p-2">{row.replicate_number}</td><td className="p-2">{row.status}</td><td className="max-w-28 truncate p-2 font-mono" title={row.protocol_revision_id}>{row.protocol_revision_id}</td><td className="space-x-1 p-2">
      <Button size="sm" variant="outline" disabled={!row.latest_attempt} onClick={() => onInspect?.(row.row_result_id)}>Output</Button>
      {row.latest_attempt && ['pending', 'running', 'finalizing'].includes(row.status) && <Button size="sm" variant="outline" disabled={cancel.isPending} onClick={() => cancel.mutate(row.latest_attempt!.run_id)}>Cancel</Button>}
      <Button size="sm" variant="outline" disabled={blocked || !!retrying || retry.isPending || !['failed', 'cancelled'].includes(row.status) || row.protocol_revision_id !== protocol.published_revision_id} onClick={() => retry.mutate(row.row_result_id)}>Retry</Button>
    </td></tr>)}</tbody></table></div>
    {rows.length > 20 && <div className="flex items-center gap-2"><Button size="sm" variant="outline" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</Button><span className="font-mono text-xs">Page {currentPage + 1} of {Math.ceil(rows.length / 20)}</span><Button size="sm" variant="outline" disabled={(currentPage + 1) * 20 >= rows.length} onClick={() => setPage(currentPage + 1)}>Next</Button></div>}
    {confirm && <RunConfirmDialog scope={{ type: 'all-cells', cellCount: summary?.cell_count ?? 0, replicateCount: summary?.parent_replicate_count ?? 0, pendingReplicateCount: summary?.parent_replicate_count ?? 0 }} nodes={protocol.graph.nodes} edges={protocol.graph.edges} queryClient={queryClient} onCancel={() => setConfirm(false)} onConfirm={() => run.mutate()} hasUnpublishedChanges={protocol.has_unpublished_changes} publishedRevision={protocol.published_revision} confirmDisabled={blocked || run.isPending} additionalContent={<p className="font-mono text-xs">{summary && rowExecutionForecast(summary)}</p>} />}
  </section>
}
