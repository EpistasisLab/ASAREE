import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { cellResultStatusAccents as accents, cellResultStatusLabels, STATUS_PILL_SIZE } from '@/lib/executionStatus'
import { cardAccent } from '@/lib/utils'
import type { CellResultStatus as ResultStatus, ExecutionCell, ExecutionSummary } from '@/types/executionResults'

export function CellResultStatusBadge({ status }: { status: ResultStatus }) {
  return <Badge variant="outline" style={cardAccent(accents[status])}
    className={`${STATUS_PILL_SIZE} border-current text-[color:var(--card-accent)] opacity-70`}>{cellResultStatusLabels[status]}</Badge>
}

export function CellResultStatus({ cell, unit }: { cell: ExecutionCell; unit?: ExecutionSummary['unit'] }) {
  const status = cell.result_status
  const singular = unit === 'row' ? 'row' : 'replicate'
  const plural = unit === 'row' ? 'rows' : 'replicates'
  const reasons = Object.entries(cell.result_issues ?? {}).filter(([, count]) => (count ?? 0) > 0)
  function describeIssue(reason: string, count: number) {
    const subject = `${count.toLocaleString()} ${count === 1 ? singular : plural}`
    switch (reason) {
      case 'failed': return `${subject} failed during the run.`
      case 'cancelled': return `${subject} ${count === 1 ? 'was' : 'were'} cancelled.`
      case 'limit_reached': return `${subject} hit the iteration limit.`
      case 'measurement_failed': return `Measurement processing failed for ${subject}.`
      case 'no_measurements': return `No measurements were configured for ${subject}.`
      default: return `${subject} ${count === 1 ? 'has' : 'have'} missing, unavailable, or unreported measurements.`
    }
  }
  return <section aria-label="Cell result status" className="mt-3">
    <Card size="sm" className="gap-2 p-3" style={cardAccent(status ? accents[status] : 'var(--muted-foreground)')}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Result status</h3>
        {status && <CellResultStatusBadge status={status} />}
      </div>
      {!status ? <p role="status" className="text-xs text-muted-foreground">Preparing cell result status…</p> : <>
        {cell.usable_results != null && cell.total != null && <p className="text-xs text-muted-foreground">{cell.usable_results.toLocaleString()} of {cell.total.toLocaleString()} {cell.total === 1 ? singular : plural} {cell.total === 1 ? 'has' : 'have'} usable results.</p>}
        {status === 'ready' && <p className="text-xs">All planned {plural} finished with the applicable measurements obtained.</p>}
        {status === 'awaiting_results' && <p className="text-xs">Waiting for remaining {plural} or measurement processing. No result problems have been identified.</p>}
        {status === 'needs_attention' && <>
          {cell.results_needing_attention != null && <p className="text-xs">{cell.results_needing_attention.toLocaleString()} {cell.results_needing_attention === 1 ? singular : plural} {cell.results_needing_attention === 1 ? 'needs' : 'need'} attention.</p>}
          {reasons.length ? <ul className="list-disc space-y-1 pl-4 text-xs">{reasons.map(([reason, count]) => <li key={reason}>{describeIssue(reason, count ?? 0)}</li>)}</ul>
            : <p className="text-xs">Some finished {plural} have run or measurement problems. Review their results below.</p>}
        </>}
      </>}
    </Card>
  </section>
}
