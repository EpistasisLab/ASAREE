import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cardAccent } from '@/lib/utils'
import type { CellResultStatus, ExecutionSummary } from '@/types/executionResults'

export function ResultsContext({ summary, selectedStatus = '', onStatusChange }: {
  summary: ExecutionSummary; selectedStatus?: CellResultStatus | ''
  onStatusChange?: (status: CellResultStatus | '') => void
}) {
  const counts = summary.cell_result_counts
  const units = summary.unit === 'row' ? 'rows' : 'replicates'
  return <section aria-label="Results context">
    <section aria-label="Cell results overview" className="space-y-2">
      <TooltipProvider delay={200}><ul className="grid grid-cols-2 gap-2 @min-[600px]:grid-cols-4">
        {([
          ['', 'Total cells', summary.cell_count, 'All cells in this published design.', 'var(--muted-foreground)'],
          ['ready', 'Ready to review', counts?.ready, `All planned ${units} finished with at least one measured value and every applicable declared measurement obtained.`, 'var(--chart-3)'],
          ['needs_attention', 'Needs attention', counts?.needs_attention, `At least one finished ${summary.unit === 'row' ? 'row' : 'replicate'} failed, was cancelled, hit a limit, or has missing, unavailable, or failed measurements. Measurements marked not applicable are excluded. Other results in the cell may still be usable.`, 'var(--chart-4)'],
          ['awaiting_results', 'Awaiting results', counts?.awaiting_results, 'Remaining work or measurement processing is pending, with no result problems identified.', 'var(--primary)'],
        ] as const).map(([status, label, count, help, accent]) => <li key={label}>
          <Card size="sm" className="h-full gap-0 p-0" style={cardAccent(accent)}>
            <Tooltip><TooltipTrigger render={<Button variant="ghost" aria-label={`Filter cells by ${label}`} aria-pressed={selectedStatus === status}
              disabled={!onStatusChange || count == null}
              onClick={() => onStatusChange?.(selectedStatus === status ? '' : status)}
              className="h-full w-full flex-col gap-1 whitespace-normal p-2.5 text-[color:var(--card-accent)] hover:text-[color:var(--card-accent)] aria-pressed:border-current aria-pressed:bg-[color-mix(in_oklch,var(--card-accent),transparent_90%)]" />}>
              <span className="text-[0.65rem]">{label}</span>
              <span className="font-mono text-lg tabular-nums">{count == null ? '—' : count.toLocaleString()}</span>
            </TooltipTrigger>
              <TooltipContent className="max-w-72 flex-col items-start gap-1 text-left">
                <p>{help}</p>
                <p>{status ? 'Select to show matching cells. Select again to show all cells.' : 'Select to show all cells while keeping your search.'}</p>
              </TooltipContent>
            </Tooltip>
          </Card>
        </li>)}
      </ul></TooltipProvider>
      {!counts && <p role="status" className="text-xs text-muted-foreground">Preparing cell result counts…</p>}
    </section>
  </section>
}

export function ResultsMetadata({ summary }: { summary: ExecutionSummary }) {
  const timestamp = summary.as_of ?? summary.updated_at
  const updated = timestamp ? new Date(timestamp) : null
  const hasTimestamp = updated !== null && Number.isFinite(updated.getTime())
  return <dl aria-label="Results metadata" className="grid grid-cols-1 gap-x-4 gap-y-2 px-1 py-2 text-xs @min-[400px]:grid-cols-2">
      <div><dt className="text-muted-foreground">Run mode</dt><dd>{summary.unit === 'row' ? 'Per dataset row' : summary.datasets?.length ? 'Whole dataset per replicate' : 'Per replicate'}{summary.unit === 'row' && summary.source_row_count != null && ` · ${summary.source_row_count.toLocaleString()} source rows`}</dd></div>
      <div><dt className="text-muted-foreground">Last updated</dt><dd>{hasTimestamp ? <time dateTime={updated.toISOString()}>{updated.toLocaleString()}</time> : 'Not available'}</dd></div>
  </dl>
}
