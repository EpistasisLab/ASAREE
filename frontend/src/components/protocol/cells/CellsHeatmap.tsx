import { Fragment, useMemo, useState } from 'react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  availableMetricKeys,
  cellsMatching,
  deriveFactors,
  displayFactorValue,
  formatMetricLabel,
  heatColor,
  meanMetric,
  metricValueSuffix,
  pickDefaultMetric,
  scaledMetricValue,
  type FactorSpec,
} from '@/lib/experiment'
import { cn } from '@/lib/utils'
import type { Cell, Experiment } from '@/types/experiments'

/** A factor-combination heatmap complementing (not replacing) the precise
 * Cells table below — 1-2 factors render as a single grid, 3 facet into one
 * grid per level of the third. Both the factors (axes) and the metric
 * (color) are derived from what's actually on the cells, with zero setup
 * required — an explicit design_spec.factors/task_brief.selection_metric is
 * used when present, but never required; most experiments won't have
 * either. Silently renders nothing for >3 derived factors, <2 cells, or no
 * numeric metric anywhere: those are exactly the cases where a heatmap
 * can't show anything the table doesn't already say better. Replicate cells
 * sharing one combination are averaged, not just the first one picked.
 *
 * `compact` is the side-panel rendering (a ~384px column): same grid, same
 * data, just a stacked header and smaller squares, since the panel can't
 * afford the title and the metric selector side by side. The maximized
 * overlay renders the non-compact form.
 */
export function CellsHeatmap({
  experiment,
  cells,
  compact = false,
}: {
  experiment: Experiment
  cells: Cell[]
  compact?: boolean
}) {
  const availableMetrics = useMemo(() => availableMetricKeys(cells), [cells])
  const defaultMetric = useMemo(() => pickDefaultMetric(experiment, cells), [experiment, cells])
  const [metricKey, setMetricKey] = useState<string | null>(defaultMetric)
  const activeMetric = metricKey && availableMetrics.includes(metricKey) ? metricKey : defaultMetric

  const factors = useMemo(() => deriveFactors(cells, experiment.design_spec), [cells, experiment.design_spec])
  if (!factors || factors.length < 1 || factors.length > 3 || cells.length < 2 || !activeMetric) return null

  const rowFactor = factors[0]
  const colFactor: FactorSpec = factors[1] ?? { name: '', levels: [null] }
  const facetFactor = factors[2]
  const facetLevels = facetFactor ? facetFactor.levels : [null]

  const grid = facetLevels.map((facetLevel) =>
    rowFactor.levels.map((rowLevel) =>
      colFactor.levels.map((colLevel) => {
        const match: Record<string, unknown> = { [rowFactor.name]: rowLevel }
        if (colFactor.name) match[colFactor.name] = colLevel
        if (facetFactor) match[facetFactor.name] = facetLevel
        const matched = cellsMatching(cells, match)
        return { value: meanMetric(matched, activeMetric), count: matched.length }
      }),
    ),
  )

  const allValues = grid.flat(2).map((c) => c.value).filter((v): v is number => v !== null)
  if (allValues.length === 0) return null
  const min = Math.min(...allValues)
  const max = Math.max(...allValues)

  return (
    <div className={cn('space-y-3', !compact && 'space-y-4')}>
      <div className={cn('gap-2', compact ? 'flex flex-col' : 'flex items-center justify-between')}>
        <span className="text-sm font-semibold text-foreground">
          {rowFactor.name}
          {colFactor.name ? ` × ${colFactor.name}` : ''}
        </span>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {availableMetrics.length > 1 && (
            <Select value={activeMetric} onValueChange={(v) => v !== null && setMetricKey(v)}>
              <SelectTrigger size="sm" className={compact ? 'min-w-0 flex-1' : 'w-64'}>
                <SelectValue>{(v: string) => formatMetricLabel(v)}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {availableMetrics.map((m) => (
                  <SelectItem key={m} value={m}>
                    {formatMetricLabel(m)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <span className="font-mono">
            {scaledMetricValue(activeMetric, min).toFixed(3)}
            {metricValueSuffix(activeMetric)}
          </span>
          <div
            className="h-2 w-16 shrink-0 rounded-full"
            style={{
              background:
                'linear-gradient(to right, color-mix(in oklch, var(--muted) 100%, var(--primary) 10%), color-mix(in oklch, var(--muted) 100%, var(--primary) 90%))',
            }}
          />
          <span className="font-mono">
            {scaledMetricValue(activeMetric, max).toFixed(3)}
            {metricValueSuffix(activeMetric)}
          </span>
        </div>
      </div>
      {/* Facets stack in the panel (no room for two side by side) and pair up
          once there's real width to spend. */}
      <div className={cn('grid gap-4', !compact && facetLevels.length > 1 && 'sm:grid-cols-2')}>
        {facetLevels.map((facetLevel, fi) => (
          <div key={fi} className="space-y-1.5">
            {facetFactor && (
              <p className="font-mono text-xs text-muted-foreground">
                {facetFactor.name} = {displayFactorValue(facetLevel)}
              </p>
            )}
            <div className="grid gap-1" style={{ gridTemplateColumns: `auto repeat(${colFactor.levels.length}, 1fr)` }}>
              {/* Axis names, attached directly to the axis they label -- the
                  title above names both factors, but only in "by X × Y"
                  order, which a reader has to remember maps to rows-then-
                  columns. These remove the need to remember that. The column
                  axis name gets its own row (above the column headers); the
                  row axis name sits directly beside those same headers, one
                  row above its own values -- not stacked above them with an
                  empty row in between, which read as disconnected from what
                  it was labeling. */}
              <div />
              <div
                className="truncate text-center text-xs font-semibold tracking-wide text-muted-foreground uppercase"
                style={{ gridColumn: '2 / -1' }}
              >
                {colFactor.name}
              </div>
              <div className="flex items-center pr-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                {rowFactor.name}
              </div>
              {colFactor.name
                ? colFactor.levels.map((colLevel, ci) => (
                    <div key={ci} className="truncate text-center font-mono text-xs text-muted-foreground">
                      {displayFactorValue(colLevel)}
                    </div>
                  ))
                : <div />}
              {rowFactor.levels.map((rowLevel, ri) => (
                <Fragment key={ri}>
                  <div className="flex items-center pr-2 font-mono text-xs text-muted-foreground">
                    {displayFactorValue(rowLevel)}
                  </div>
                  {colFactor.levels.map((_, ci) => {
                    const cellData = grid[fi][ri][ci]
                    return (
                      <div
                        key={ci}
                        className={cn(
                          'flex aspect-square items-center justify-center rounded-md border border-border/50 font-mono',
                          compact ? 'min-h-8 text-[0.65rem]' : 'min-h-12 text-xs',
                        )}
                        style={cellData.value !== null ? { background: heatColor(cellData.value, min, max) } : undefined}
                        title={cellData.count > 0 ? `n=${cellData.count}` : 'no cell for this combination'}
                      >
                        {cellData.value !== null
                          ? `${scaledMetricValue(activeMetric, cellData.value).toFixed(3)}${metricValueSuffix(activeMetric)}`
                          : cellData.count > 0
                            ? '…'
                            : ''}
                      </div>
                    )
                  })}
                </Fragment>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
