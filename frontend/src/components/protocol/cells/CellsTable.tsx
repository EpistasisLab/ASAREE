import { useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ChevronsUpDown } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  deriveFactors,
  displayFactorValue,
  formatMetricLabel,
  formatMetricValue,
  groupReplicatesIntoCells,
  meanMetric,
  pickMetricColumns,
  type ExperimentalCell,
} from '@/lib/experiment'
import { cn } from '@/lib/utils'
import type { Experiment, Replicate } from '@/types/experiments'

type CellSort = { key: string; dir: 'asc' | 'desc' }

const CELLS_PAGE_SIZE = 20

function cellSortValue(cell: ExperimentalCell, key: string): string | number {
  if (key === 'cell_label') return cell.label.toLowerCase()
  if (key === 'updated_at') return new Date(cell.updatedAt).getTime()
  if (key === 'status') return cell.scoredReplicateCount / cell.replicates.length
  if (key.startsWith('factor:')) return displayFactorValue(cell.factorValues[key.slice(7)] ?? '').toLowerCase()
  if (key.startsWith('metric:')) {
    return meanMetric(cell.replicates, key.slice(7)) ?? Number.NEGATIVE_INFINITY
  }
  return ''
}

function SortableCellHead({
  label,
  sortKey,
  sort,
  onSort,
}: {
  label: string
  sortKey: string
  sort: CellSort
  onSort: (key: string) => void
}) {
  const active = sort.key === sortKey
  const Icon = active ? (sort.dir === 'asc' ? ArrowUp : ArrowDown) : ChevronsUpDown
  return (
    <th className="px-2 py-1.5 text-left font-semibold">
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          'flex cursor-pointer items-center gap-1 tracking-wide uppercase hover:text-foreground',
          active ? 'text-foreground' : 'text-muted-foreground',
        )}
      >
        <span className="whitespace-nowrap">{label}</span>
        <Icon className={cn('size-3 shrink-0', !active && 'opacity-40')} />
      </button>
    </th>
  )
}

/** A real table -- one column per derived factor, one per curated metric --
 * not two squished `key=value, key=value` string dumps styled like a table.
 * Every row is one true experimental cell (a unique factor combination),
 * with metrics averaged across its replicates. Every column is independently
 * sortable.
 *
 * Styled to the side panel's own compact table idiom (see RunsTab/ResultsTab:
 * a plain `text-xs` table in a bordered box, zebra-striped, font-mono for
 * technical values) rather than components/ui/table's roomier page-width
 * rows, since the panel is where this now lives. The column set is dynamic
 * and can still outgrow even the maximized overlay, so the CALLER wraps this
 * in the horizontal scroll container -- see CellsTab.
 */
export function CellsTable({ experiment, cells }: { experiment: Experiment; cells: Replicate[] }) {
  const [sort, setSort] = useState<CellSort>({ key: 'updated_at', dir: 'desc' })
  const [page, setPage] = useState(1)
  const factors = useMemo(() => deriveFactors(cells, experiment.design_spec) ?? [], [cells, experiment.design_spec])
  const metricColumns = useMemo(() => pickMetricColumns(experiment, cells), [experiment, cells])
  const groupedCells = useMemo(() => groupReplicatesIntoCells(cells), [cells])

  function handleSort(key: string) {
    setSort((prev) => (prev.key === key ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }))
    setPage(1)
  }

  const sorted = useMemo(() => {
    const rows = [...groupedCells]
    rows.sort((a, b) => {
      const av = cellSortValue(a, sort.key)
      const bv = cellSortValue(b, sort.key)
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return sort.dir === 'asc' ? cmp : -cmp
    })
    return rows
  }, [groupedCells, sort])

  const totalPages = Math.max(1, Math.ceil(sorted.length / CELLS_PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const paged = sorted.slice((currentPage - 1) * CELLS_PAGE_SIZE, currentPage * CELLS_PAGE_SIZE)

  return (
    <div className="space-y-2">
      <div className="overflow-hidden rounded-md border">
        <table className="w-full text-xs">
          <thead className="bg-muted/50 text-muted-foreground uppercase">
            <tr>
              <SortableCellHead label="Cell" sortKey="cell_label" sort={sort} onSort={handleSort} />
              {factors.map((f) => (
                <SortableCellHead key={f.name} label={f.name} sortKey={`factor:${f.name}`} sort={sort} onSort={handleSort} />
              ))}
              {metricColumns.map((m) => (
                <SortableCellHead key={m} label={formatMetricLabel(m)} sortKey={`metric:${m}`} sort={sort} onSort={handleSort} />
              ))}
              <SortableCellHead label="Status" sortKey="status" sort={sort} onSort={handleSort} />
              <SortableCellHead label="Updated" sortKey="updated_at" sort={sort} onSort={handleSort} />
            </tr>
          </thead>
          <tbody>
            {paged.map((cell, i) => (
              <tr key={cell.label} className={i % 2 === 1 ? 'bg-muted/20' : ''}>
                <td className="max-w-40 truncate px-2 py-1.5 font-mono font-medium" title={cell.label}>
                  {cell.label}
                </td>
                {factors.map((f) => (
                  <td key={f.name} className="max-w-32 truncate px-2 py-1.5 font-mono text-muted-foreground">
                    {f.name in cell.factorValues ? displayFactorValue(cell.factorValues[f.name]) : '—'}
                  </td>
                ))}
                {metricColumns.map((m) => (
                  <td key={m} className="px-2 py-1.5 font-mono whitespace-nowrap text-muted-foreground">
                    {formatMetricValue(m, meanMetric(cell.replicates, m))}
                  </td>
                ))}
                <td className="px-2 py-1.5">
                  <Badge variant={cell.scoredReplicateCount === cell.replicates.length ? 'default' : 'secondary'}>
                    {cell.scoredReplicateCount}/{cell.replicates.length} replicates scored
                  </Badge>
                </td>
                <td className="px-2 py-1.5 font-mono whitespace-nowrap text-muted-foreground">
                  {new Date(cell.updatedAt).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          Showing {(currentPage - 1) * CELLS_PAGE_SIZE + 1}–{Math.min(currentPage * CELLS_PAGE_SIZE, sorted.length)} of{' '}
          {sorted.length}
        </p>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="icon-sm"
            disabled={currentPage <= 1}
            onClick={() => setPage((p) => p - 1)}
            aria-label="Previous page"
          >
            <ChevronLeft className="size-4" />
          </Button>
          <span className="text-xs text-muted-foreground">
            Page {currentPage} of {totalPages}
          </span>
          <Button
            variant="outline"
            size="icon-sm"
            disabled={currentPage >= totalPages}
            onClick={() => setPage((p) => p + 1)}
            aria-label="Next page"
          >
            <ChevronRight className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
