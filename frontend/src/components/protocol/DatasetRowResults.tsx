import { useState } from 'react'
import type { ResultsScope } from '@/api/client'
import { Button } from '@/components/ui/button'
import { reportedMetricText } from '@/lib/reportedMetrics'
import { displayFactorLevel } from '@/lib/experiment'
import type { Experiment, ExperimentRunResults, RowResult } from '@/types/experiments'
import { RunStepTrace } from './NodeRunOutputPanel'
import { DatasetRowCells } from './DatasetRowCells'
import { ChevronRight, AlertTriangle, CircleCheck, CircleHelp } from 'lucide-react'
import { ResultsPanel, ResultsScorecard } from './ResultsPanel'

export function DatasetRowResults({ results, experiment, scope, onInspect }: { results: ExperimentRunResults; experiment: Experiment; scope: ResultsScope; onInspect: (rowId: string) => void }) {
  const [page, setPage] = useState(0)
  const [selectedCellId, setSelectedCellId] = useState('')
  const [selectedReplicateId, setSelectedReplicateId] = useState('')
  const rows = (results.row_results ?? []).filter(row => (!selectedCellId || row.cell_id === selectedCellId) && (!selectedReplicateId || row.replicate_result_id === selectedReplicateId))
  const currentPage = Math.min(page, Math.max(0, Math.ceil(rows.length / 20) - 1))
  const visibleRows = rows.slice(currentPage * 20, (currentPage + 1) * 20)
  const factors = [...new Set(rows.flatMap(row => Object.keys(row.factor_values)))]
  const declared = experiment.design_spec?.metrics?.filter(metric => metric.kind === 'custom') ?? []
  const observed = rows.flatMap(row => row.measurement?.observations ?? []).filter(item => item.producer?.kind !== 'runtime')
  const metrics = [...new Map([...observed.map(item => ({ id: item.metric_id, name: item.metric_name })), ...declared].map(metric => [metric.id, metric])).values()]
  const summary = results.row_summary
  return <ResultsPanel experimentId={experiment.id} experimentName={experiment.name} scope={scope}
    summary={<div className="min-w-0"><div className="text-xs font-medium text-primary">Per-row results</div><p className="mt-1 text-sm font-medium">Individual row outcomes</p><p className="mt-1 text-xs text-muted-foreground">Per-row outcomes are reported individually. Aggregation, ranking and factorial analysis are unavailable.</p></div>}
    scorecards={summary && <>
      <ResultsScorecard label="Row executions" help="Completed row executions out of the expected total for all generated replicates." value={`${summary.completed}/${summary.expected ?? '?'}`} note={`${summary.running} running · ${summary.pending} queued`} icon={ChevronRight} />
      <ResultsScorecard label="Completed" help="Rows whose latest execution attempt completed." value={String(summary.completed)} note={`${summary.planned} planned`} icon={CircleCheck} />
      <ResultsScorecard label="Failed" help="Row executions whose latest attempt failed. Inspect the row for its error and attempt history." value={String(summary.failed)} note={`${summary.cancelled} cancelled`} icon={AlertTriangle} />
      <ResultsScorecard label="Missing reported" help="Row executions missing one or more declared reported measurements." value={String(summary.missing_reported)} icon={CircleHelp} />
    </>}
    cells={<DatasetRowCells resultView results={results} designSpec={experiment.design_spec} selectedCellId={selectedCellId} onSelect={cellId => { setSelectedCellId(cellId); setSelectedReplicateId(''); setPage(0) }} onSelectReplicate={(cellId, replicateId) => { setSelectedCellId(cellId); setSelectedReplicateId(replicateId); setPage(0) }} />}
    details={<section className="space-y-2">
      <h3 className="text-sm font-medium">{selectedCellId ? 'Cell row outcomes' : 'Row outcomes'}</h3>
    <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr>{[...factors, 'Source row', 'Replicate', 'Execution status', 'Reported coverage', ...metrics.map(metric => metric.name), 'Output'].map((heading, index) => <th key={index} className="whitespace-nowrap p-2 text-left">{heading}</th>)}</tr></thead><tbody>{visibleRows.map(row => {
      const observations = row.measurement?.observations ?? []
      const measured = metrics.filter(metric => observations.some(item => item.metric_id === metric.id && item.status === 'measured')).length
      return <tr key={row.row_result_id} className="border-t">{factors.map(factor => <td key={factor} className="p-2 font-mono">{displayFactorLevel(experiment.design_spec, factor, row.factor_values[factor], row.cell_label)}</td>)}<td className="p-2 font-mono">{row.dataset_row.row_index + 1}</td><td className="p-2">{row.replicate_number}</td><td className="p-2">{row.status}</td><td className="p-2">{measured}/{metrics.length}</td>{metrics.map(metric => <td key={metric.id} className="max-w-64 break-words p-2 font-mono">{reportedMetricText(observations.find(item => item.metric_id === metric.id))}</td>)}<td className="p-2"><Button variant="outline" size="sm" disabled={!row.latest_attempt} onClick={() => onInspect(row.row_result_id)}>Inspect row</Button></td></tr>
    })}</tbody></table></div>
    {rows.length > 20 && <div className="flex items-center gap-2"><Button size="sm" variant="outline" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</Button><span className="font-mono text-xs">Page {currentPage + 1} of {Math.ceil(rows.length / 20)}</span><Button size="sm" variant="outline" disabled={(currentPage + 1) * 20 >= rows.length} onClick={() => setPage(currentPage + 1)}>Next</Button></div>}
    {!rows.length && <p className="text-xs text-muted-foreground">{selectedCellId ? 'No row executions planned for this cell.' : 'No row executions planned in this scope.'}</p>}
    </section>}
  />
}

export function DatasetRowDetail({ row, onClose }: { row: RowResult; onClose: () => void }) {
  const [attemptId, setAttemptId] = useState(row.latest_attempt?.run_id)
  const attempt = row.attempts.find(item => item.run_id === attemptId) ?? row.latest_attempt
  const provenance = attempt?.attempt_result?.row_provenance
  return <aside className="absolute inset-0 z-20 flex flex-col overflow-y-auto border-l bg-card p-3" aria-label="Row result details">
    <div className="flex items-center justify-between"><h2 className="text-sm">Source row {row.dataset_row.row_index + 1} · Replicate {row.replicate_number}</h2><Button variant="ghost" size="sm" onClick={onClose}>Close</Button></div>
    <dl className="my-3 break-all font-mono text-xs">{Object.entries({ 'Dataset UUID': row.dataset_row.dataset_id, 'Original SHA-256': row.dataset_row.raw_sha256, 'Design revision': row.design_revision_id, 'Protocol revision': row.protocol_revision_id, 'Row result': row.row_result_id }).map(([label,value]) => <div key={label}><dt className="text-muted-foreground">{label}</dt><dd>{value}</dd></div>)}</dl>
    <label className="space-y-1 text-xs">Attempt<select className="w-full rounded border bg-background p-2 font-mono" aria-label="Inspect attempt" value={attemptId ?? ''} onChange={event => setAttemptId(event.target.value)}>{row.attempts.map(item => <option key={item.run_id} value={item.run_id}>{item.status} · {item.run_id}{item.current ? ' · latest' : ''}</option>)}</select></label>
    {attempt && <><p className="my-2 font-mono text-xs">{attempt.run_id} · {attempt.status}</p>{attempt.error && <p role="alert" className="text-xs text-destructive">{attempt.error}</p>}<pre className="overflow-auto whitespace-pre-wrap break-all font-mono text-xs">{JSON.stringify(attempt.attempt_result, null, 2)}</pre>{provenance ? <pre className="font-mono text-xs">{JSON.stringify(provenance, null, 2)}</pre> : null}{Object.entries(attempt.node_runs).map(([id,node]) => <section key={id} className="my-3 space-y-2"><p className="font-mono text-xs">{id}</p><pre className="whitespace-pre-wrap font-mono text-xs">{node.output_text}</pre>{node.error && <p className="text-xs text-destructive">{node.error}</p>}{node.run_id && <RunStepTrace runId={node.run_id} />}</section>)}</>}
  </aside>
}
