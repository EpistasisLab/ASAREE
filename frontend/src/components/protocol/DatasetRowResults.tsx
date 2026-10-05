import { useEffect, useState } from 'react'
import { experimentsApi, type ResultsScope } from '@/api/client'
import { Button } from '@/components/ui/button'
import { reportedMetricText } from '@/lib/reportedMetrics'
import type { Experiment, ExperimentRunResults, RowResult } from '@/types/experiments'
import { RunStepTrace } from './NodeRunOutputPanel'

export function DatasetRowResults({ results, experiment, scope, onInspect }: { results: ExperimentRunResults; experiment: Experiment; scope: ResultsScope; onInspect: (rowId: string) => void }) {
  const [page, setPage] = useState(0)
  const [maximized, setMaximized] = useState(false)
  useEffect(() => {
    if (!maximized) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setMaximized(false) }
    document.addEventListener('keydown', close)
    return () => { document.body.style.overflow = previous; document.removeEventListener('keydown', close) }
  }, [maximized])
  const [error, setError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const rows = results.row_results ?? []
  const currentPage = Math.min(page, Math.max(0, Math.ceil(rows.length / 20) - 1))
  const visibleRows = rows.slice(currentPage * 20, (currentPage + 1) * 20)
  const factors = [...new Set(rows.flatMap(row => Object.keys(row.factor_values)))]
  const declared = experiment.design_spec?.metrics?.filter(metric => metric.kind === 'custom') ?? []
  const observed = rows.flatMap(row => row.measurement?.observations ?? []).filter(item => item.producer?.kind !== 'runtime')
  const metrics = [...new Map([...observed.map(item => ({ id: item.metric_id, name: item.metric_name })), ...declared].map(metric => [metric.id, metric])).values()]
  async function download() {
    setDownloading(true)
    try {
      const blob = await experimentsApi.downloadRunResultsCsv(experiment.id, scope)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'row-results.csv'
      link.click()
      URL.revokeObjectURL(url)
    } catch (error) { setError(error instanceof Error ? error.message : 'CSV unavailable') }
    finally { setDownloading(false) }
  }
  return <div className={maximized ? "@container fixed inset-0 z-50 space-y-3 overflow-auto bg-background p-4" : "@container space-y-3 p-3"}>
    <p className="text-xs text-muted-foreground">Per-row outcomes are reported individually. Aggregation, ranking and factorial analysis are unavailable.</p>
    {results.row_summary && <><p className="font-mono text-xs">{results.row_summary.planned} planned · {results.row_summary.completed} completed · {results.row_summary.failed} failed · {results.row_summary.missing_reported} missing reported</p><pre className="overflow-auto font-mono text-xs">{JSON.stringify(results.row_summary.metric_coverage, null, 2)}</pre></>}
    <Button variant="outline" size="sm" onClick={() => setMaximized(value => !value)}>{maximized ? 'Restore Results' : 'Maximize Results'}</Button>
    <Button variant="outline" size="sm" disabled={downloading} onClick={download}>Download CSV</Button>
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr>{[...factors, 'Source row', 'Replicate', 'Execution status', 'Reported coverage', ...metrics.map(metric => metric.name), 'Output'].map((heading, index) => <th key={index} className="whitespace-nowrap p-2 text-left">{heading}</th>)}</tr></thead><tbody>{visibleRows.map(row => {
      const observations = row.measurement?.observations ?? []
      const measured = metrics.filter(metric => observations.some(item => item.metric_id === metric.id && item.status === 'measured')).length
      return <tr key={row.row_result_id} className="border-t">{factors.map(factor => <td key={factor} className="p-2 font-mono">{String(row.factor_values[factor] ?? '')}</td>)}<td className="p-2 font-mono">{row.dataset_row.row_index + 1}</td><td className="p-2">{row.replicate_number}</td><td className="p-2">{row.status}</td><td className="p-2">{measured}/{metrics.length}</td>{metrics.map(metric => <td key={metric.id} className="max-w-64 break-words p-2 font-mono">{reportedMetricText(observations.find(item => item.metric_id === metric.id))}</td>)}<td className="p-2"><Button variant="outline" size="sm" disabled={!row.latest_attempt} onClick={() => onInspect(row.row_result_id)}>Inspect row</Button></td></tr>
    })}</tbody></table></div>
    {rows.length > 20 && <div className="flex items-center gap-2"><Button size="sm" variant="outline" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</Button><span className="font-mono text-xs">Page {currentPage + 1} of {Math.ceil(rows.length / 20)}</span><Button size="sm" variant="outline" disabled={(currentPage + 1) * 20 >= rows.length} onClick={() => setPage(currentPage + 1)}>Next</Button></div>}
    {!rows.length && <p className="text-xs text-muted-foreground">No row executions planned in this scope.</p>}
  </div>
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
