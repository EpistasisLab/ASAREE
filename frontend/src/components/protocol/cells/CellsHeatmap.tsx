import { Fragment, useMemo, useState } from 'react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  availableMetricKeys,
  replicatesMatching,
  deriveFactors,
  displayFactorValue,
  formatMetricLabel,
  groupReplicatesIntoCells,
  heatColor,
  meanMetric,
  metricValueSuffix,
  pickDefaultMetric,
  scaledMetricValue,
  type FactorSpec,
} from '@/lib/experiment'
import { cn } from '@/lib/utils'
import type { Experiment, Replicate } from '@/types/experiments'

/** Why there's no grid here, said out loud in one dim line rather than
 * rendering nothing at all. The table below still carries every number, so
 * this is a footnote, not an error -- but a heatmap that can vanish for five
 * different data-shaped reasons and never says which one is indistinguishable
 * from a heatmap that's broken, and that ambiguity cost a real debugging
 * round-trip once already. Keep any new bail-out routed through here.
 */
function HeatmapUnavailable({ reason }: { reason: string }) {
  return <p className="text-xs text-muted-foreground">No heatmap — {reason}.</p>
}

/** A factor-combination heatmap complementing (not replacing) the precise
 * Cells table below — 1-2 factors render as a single grid, 3 facet into one
 * grid per level of the third. Both the factors (axes) and the metric
 * (color) are derived from what's actually on the cells, with zero setup
 * required — an explicit design_spec.factors/task_brief.selection_metric is
 * used when present, but never required; most experiments won't have
 * either. Bails out to a one-line HeatmapUnavailable note for >3 derived
 * factors, <2 cells, or no numeric metric anywhere: those are exactly the
 * cases where a heatmap can't show anything the table doesn't already say
 * better. Replicates in each cell are averaged, not just the first one picked.
 *
 * Sized by CONTAINER query (`@lg`/`@2xl`), not by a compact/roomy prop or the
 * viewport: this renders both inside the drag-resizable side panel and in the
 * maximize overlay, so "how much room do I have" is a question about the box
 * it's in, which is exactly what a container query answers. Dragging the
 * panel wider un-stacks the header, widens the metric select, grows the
 * squares and eventually pairs up the facets, with no width plumbed through
 * three components to make that happen. The parent marks the container --
 * see CellsTab's own `@container`.
 */
export function CellsHeatmap({ experiment, cells }: { experiment: Experiment; cells: Replicate[] }) {
  const availableMetrics = useMemo(() => availableMetricKeys(cells), [cells])
  const defaultMetric = useMemo(() => pickDefaultMetric(experiment, cells), [experiment, cells])
  const [metricKey, setMetricKey] = useState<string | null>(defaultMetric)
  const activeMetric = metricKey && availableMetrics.includes(metricKey) ? metricKey : defaultMetric

  const factors = useMemo(() => deriveFactors(cells, experiment.design_spec), [cells, experiment.design_spec])
  const cellCount = useMemo(() => groupReplicatesIntoCells(cells).length, [cells])

  if (!factors || factors.length < 1)
    return <HeatmapUnavailable reason="these cells carry no factor values to lay out as axes" />
  if (factors.length > 3)
    return <HeatmapUnavailable reason={`${factors.length} factors here — a heatmap only reads for up to 3`} />
  if (cellCount < 2) return <HeatmapUnavailable reason="there's only one cell" />
  if (!activeMetric) return <HeatmapUnavailable reason="no replicate has a numeric metric recorded yet" />

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
        const matched = replicatesMatching(cells, match)
        return { value: meanMetric(matched, activeMetric), count: matched.length }
      }),
    ),
  )

  const allValues = grid.flat(2).map((c) => c.value).filter((v): v is number => v !== null)
  if (allValues.length === 0)
    return <HeatmapUnavailable reason={`no replicate has been scored on ${formatMetricLabel(activeMetric)} yet`} />
  const min = Math.min(...allValues)
  const max = Math.max(...allValues)

  return (
    <div className="space-y-3 @lg:space-y-4">
      <div className="flex flex-col gap-2 @lg:flex-row @lg:items-center @lg:justify-between">
        <span className="text-sm font-semibold text-foreground">
          {rowFactor.name}
          {colFactor.name ? ` × ${colFactor.name}` : ''}
        </span>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {availableMetrics.length > 1 && (
            <Select value={activeMetric} onValueChange={(v) => v !== null && setMetricKey(v)}>
              <SelectTrigger size="sm" className="min-w-0 flex-1 @lg:w-64 @lg:flex-none">
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
      {/* Facets stack while the box is narrow and pair up once there's real
          width to spend on them. */}
      <div className={cn('grid gap-4', facetLevels.length > 1 && '@2xl:grid-cols-2')}>
        {facetLevels.map((facetLevel, fi) => (
          <div key={fi} className="space-y-1.5">
            {facetFactor && (
              <p className="font-mono text-xs text-muted-foreground">
                {facetFactor.name} = {displayFactorValue(facetLevel)}
              </p>
            )}
            {/* The grid scrolls sideways rather than being squeezed. It used
                to live in a ~1024px page column where `auto repeat(N, 1fr)`
                always fit; in a 320px+ panel it does not, and a grid will not
                shrink an `auto` track below its content -- so the squares got
                pushed out past the panel Card's own overflow-hidden edge and
                the whole heatmap read as missing. Now: the label track is
                capped at 7rem and its labels truncate (so one long level name
                can't eat the row), every square has a 2.5rem floor so it can
                never collapse to a sliver, and whatever doesn't fit is
                reachable by scrolling instead of invisible. */}
            <div className="overflow-x-auto">
              <div
                className="grid gap-1"
                style={{
                  gridTemplateColumns: `fit-content(7rem) repeat(${colFactor.levels.length}, minmax(2.5rem, 1fr))`,
                }}
              >
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
                <div className="flex items-center overflow-hidden pr-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                  <span className="truncate" title={rowFactor.name}>
                    {rowFactor.name}
                  </span>
                </div>
                {colFactor.name
                  ? colFactor.levels.map((colLevel, ci) => (
                      <div
                        key={ci}
                        className="truncate text-center font-mono text-xs text-muted-foreground"
                        title={displayFactorValue(colLevel)}
                      >
                        {displayFactorValue(colLevel)}
                      </div>
                    ))
                  : <div />}
                {rowFactor.levels.map((rowLevel, ri) => (
                  <Fragment key={ri}>
                    {/* overflow-hidden on the flex row + truncate on the text:
                        together these drop the label's min-content contribution
                        to zero, which is what actually lets the 7rem cap above
                        bind. Without it a single long level name (a model id, a
                        JSON-ish dict value) sizes the whole track and shoves the
                        squares off the edge of the panel. */}
                    <div className="flex items-center overflow-hidden pr-2 font-mono text-xs text-muted-foreground">
                      <span className="truncate" title={displayFactorValue(rowLevel)}>
                        {displayFactorValue(rowLevel)}
                      </span>
                    </div>
                    {colFactor.levels.map((_, ci) => {
                      const cellData = grid[fi][ri][ci]
                      return (
                        <div
                          key={ci}
                          className="flex aspect-square min-h-8 items-center justify-center rounded-md border border-border/50 font-mono text-[0.65rem] @lg:min-h-12 @lg:text-xs"
                          style={cellData.value !== null ? { background: heatColor(cellData.value, min, max) } : undefined}
                          title={cellData.count > 0 ? `${cellData.count} planned replicates` : 'no cell for this combination'}
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
          </div>
        ))}
      </div>
    </div>
  )
}
