import { ChevronRight, CircleCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { formatTokens } from '@/lib/tokenFormatting'
import type { UsageMetric } from '@/lib/resultUsage'
import type { ExecutionCell, ExecutionSummary } from '@/types/executionResults'
import type { DesignSpec } from '@/types/experiments'
import { displayFactorCondition } from '@/lib/experiment'

const metricLabels: Record<UsageMetric, string> = {
  total_tokens: 'Tokens', cost_usd: 'Cost', duration_seconds: 'Duration',
}

export function ResultsCellsTable({ cells, metric, attemptScope, unit, startIndex = 0, onSelect, designSpec }: {
  cells: ExecutionCell[]; metric: UsageMetric; attemptScope: 'latest' | 'all'
  unit: ExecutionSummary['unit']; startIndex?: number; onSelect: (cell: ExecutionCell) => void
  designSpec?: DesignSpec | null
}) {
  return <div className="overflow-x-auto rounded-md border">
    <table aria-label="Cells" className="w-full text-xs">
      <thead className="border-b bg-muted/40 text-[10px] uppercase text-muted-foreground">
        <tr><th scope="col" className="px-2 py-2 text-left font-medium">Cell</th>
          <th scope="col" className="px-2 py-2 text-right font-medium">Progress</th>
          <th scope="col" className="px-2 py-2 text-right font-medium">{metricLabels[metric]}</th>
          <th scope="col" className="w-7"><span className="sr-only">Details</span></th></tr>
      </thead>
      <tbody className="divide-y">
        {cells.map((cell, index) => {
          const conditions = Object.entries(cell.factor_values).map(([name, value]) =>
            displayFactorCondition(name, value, designSpec, cell.cell_label)).join(' · ') || cell.cell_label
          const cellTitle = `Cell ${cell.cell_number ?? startIndex + index + 1}: ${conditions}`
          const completed = cell.completed_replicate_count ?? (unit === 'replicate' ? cell.counts?.completed ?? 0 : null)
          const total = cell.replicate_count ?? (unit === 'replicate' ? cell.total : null)
          const done = total != null && total > 0 && completed === total
          const usage = attemptScope === 'all' ? cell.all_attempt_usage : cell.usage
          const value = usage?.[metric]
          let formatted = '—'
          if (value != null && Number.isFinite(value)) {
            formatted = metric === 'total_tokens' ? formatTokens(value)
              : metric === 'cost_usd' ? new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: value > 0 && value < 0.01 ? 4 : 2 }).format(value)
                : value < 60 ? `${Math.round(value)} sec` : value < 3600 ? `${(value / 60).toFixed(1)} min` : `${(value / 3600).toFixed(1)} hr`
          }
          return <tr key={cell.cell_id} onClick={() => onSelect(cell)}
            className="cursor-pointer transition-colors even:bg-muted/10 hover:bg-primary/10 focus-within:bg-primary/10">
            <th scope="row" className="px-2 py-1.5 text-left font-normal">
              <Button variant="ghost" size="xs" className="h-auto min-h-6 max-w-full justify-start whitespace-normal text-left font-mono" title={cellTitle}>
                <span className="min-w-0 break-words"><span className="text-muted-foreground">Cell {cell.cell_number ?? startIndex + index + 1}:</span>{' '}{conditions}</span>
              </Button>
            </th>
            <td className="px-2 py-2 text-right">
              <span className="inline-flex items-center justify-end gap-1.5 whitespace-nowrap font-mono tabular-nums"
                title={`${completed ?? 'Unknown'} of ${total ?? 'unknown'} replicates completed`}>
                {completed?.toLocaleString() ?? '—'}/{total?.toLocaleString() ?? '—'}
                {done && <CircleCheck className="size-3.5 shrink-0 text-[color:var(--chart-3)]" aria-label="Completed" />}
              </span>
            </td>
            <td className="whitespace-nowrap px-2 py-2 text-right font-mono tabular-nums"
              title={value == null ? 'Not reported' : metric === 'total_tokens' ? `${value.toLocaleString()} tokens` : metric === 'duration_seconds' ? `${value.toLocaleString()} seconds` : `${value.toLocaleString(undefined, { maximumFractionDigits: 6 })} USD`}>{formatted}</td>
            <td className="px-1 py-2 text-muted-foreground"><ChevronRight className="size-3.5" aria-hidden="true" /></td>
          </tr>
        })}
      </tbody>
    </table>
  </div>
}
