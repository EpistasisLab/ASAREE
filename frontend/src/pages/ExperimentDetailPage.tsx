import { Fragment, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowDown,
  ArrowUp,
  Bot,
  ChevronsUpDown,
  Database,
  FlaskConical,
  Layers,
  Maximize2,
  Minimize2,
  Target,
  Trophy,
  type LucideIcon,
} from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { AppHeader } from '@/components/AppHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatDate, formatRelative } from '@/lib/format'
import { cellsStatusAccent, factorCount } from '@/lib/experiment'
import { cardAccent, cn, hashToChartHue } from '@/lib/utils'
import { agentsApi, datasetsApi, experimentsApi, runsApi } from '@/api/client'
import type { Agent } from '@/types/agents'
import type { Cell, Experiment } from '@/types/experiments'

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  accent,
}: {
  icon: LucideIcon
  label: string
  value: string
  sub?: string
  accent?: string
}) {
  return (
    <Card style={accent ? cardAccent(accent) : undefined}>
      <CardContent className="flex items-center gap-4">
        <div
          className={cn(
            'flex size-10 shrink-0 items-center justify-center rounded-lg',
            'bg-[color:var(--card-accent,var(--primary))]/10 text-[color:var(--card-accent,var(--primary))]',
          )}
        >
          <Icon className="size-5" />
        </div>
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
          <p className="truncate text-lg font-semibold">{value}</p>
          {sub && <p className="truncate text-xs text-muted-foreground">{sub}</p>}
        </div>
      </CardContent>
    </Card>
  )
}

/** task_brief.selection_metric, when present, is only ever a HINT for which
 * of the observed metric keys to default to below — never a requirement.
 * Most experiments won't have this persisted (it's a notebook-local variable
 * embedded straight into agent prompts, not something every user thinks to
 * also send to the experiment record), so nothing here may depend on it. */
function selectionMetricHint(experiment: Experiment | undefined): string | undefined {
  return (experiment?.task_brief as { selection_metric?: string } | undefined)?.selection_metric
}

/** Every top-level numeric key actually observed across scored cells --
 * what a metric "Color by" selector can offer, with zero setup required. */
function availableMetricKeys(cells: Cell[]): string[] {
  const keys = new Set<string>()
  for (const c of cells) {
    for (const [k, v] of Object.entries(c.metric_values ?? {})) {
      if (typeof v === 'number') keys.add(k)
    }
  }
  return Array.from(keys).sort()
}

const PREFERRED_METRICS = ['average_precision', 'roc_auc', 'accuracy', 'f1']

function pickDefaultMetric(experiment: Experiment | undefined, cells: Cell[]): string | null {
  const available = availableMetricKeys(cells)
  const hint = selectionMetricHint(experiment)
  if (hint && available.includes(hint)) return hint
  for (const p of PREFERRED_METRICS) if (available.includes(p)) return p
  return available[0] ?? null
}

/** Up to `max` metric columns for the Cells table -- NOT every numeric key
 * observed (a real scored cell can easily have 6-7: roc_auc, average_precision,
 * brier_score, n_pos_test, ...), which would make the table unreadably wide.
 * Same preference order as pickDefaultMetric (hint, then PREFERRED_METRICS),
 * filled out alphabetically so the table still has *some* metric columns
 * when nothing on the preferred list is present. */
function pickMetricColumns(experiment: Experiment | undefined, cells: Cell[], max = 4): string[] {
  const available = availableMetricKeys(cells)
  const hint = selectionMetricHint(experiment)
  const ordered: string[] = []
  if (hint && available.includes(hint)) ordered.push(hint)
  for (const p of PREFERRED_METRICS) if (available.includes(p) && !ordered.includes(p)) ordered.push(p)
  for (const k of available) if (!ordered.includes(k)) ordered.push(k)
  return ordered.slice(0, max)
}

function bestMetric(experiment: Experiment | undefined, cells: Cell[] | undefined): { key: string; value: number } | null {
  if (!cells) return null
  const key = pickDefaultMetric(experiment, cells)
  if (!key) return null
  const values = cells
    .map((c) => c.metric_values?.[key])
    .filter((v): v is number => typeof v === 'number')
  if (values.length === 0) return null
  return { key, value: Math.max(...values) }
}

interface FactorSpec {
  name: string
  levels: unknown[]
}

function getFactors(designSpec: Experiment['design_spec']): FactorSpec[] | null {
  const factors = (designSpec as { factors?: unknown } | null)?.factors
  return Array.isArray(factors) ? (factors as FactorSpec[]) : null
}

/** Bookkeeping keys that ride along on factor_values but aren't themselves a
 * factor worth an axis -- excluded when deriving factors from observed data. */
const NON_FACTOR_KEYS = new Set(['replicate', 'seed', 'rep', 'trial', 'iteration'])

/** An explicit design_spec.factors declaration wins when present (more
 * authoritative -- it can order levels deliberately); otherwise derive
 * factors straight from what's actually on the cells, so this works with
 * zero setup for the common case where nothing was ever declared. */
function deriveFactors(cells: Cell[], designSpec: Experiment['design_spec']): FactorSpec[] | null {
  const declared = getFactors(designSpec)
  if (declared && declared.length > 0) return declared

  const keys: string[] = []
  for (const c of cells) {
    for (const k of Object.keys(c.factor_values ?? {})) {
      if (!NON_FACTOR_KEYS.has(k.toLowerCase()) && !keys.includes(k)) keys.push(k)
    }
  }
  const factors = keys.map((name) => ({
    name,
    levels: Array.from(new Set(cells.map((c) => c.factor_values?.[name]).filter((v) => v !== undefined))).sort((a, b) =>
      String(a).localeCompare(String(b)),
    ),
  }))
  return factors.length > 0 ? factors : null
}

function cellsMatching(cells: Cell[], match: Record<string, unknown>): Cell[] {
  return cells.filter((c) => Object.entries(match).every(([k, v]) => String(c.factor_values?.[k]) === String(v)))
}

function meanMetric(cells: Cell[], metricKey: string): number | null {
  const values = cells.map((c) => c.metric_values?.[metricKey]).filter((v): v is number => typeof v === 'number')
  if (values.length === 0) return null
  return values.reduce((a, b) => a + b, 0) / values.length
}

/** Low values read as a dim, muted box; high values glow in the theme's own
 * accent — keeps the heatmap inside the same limited cyan-forward palette
 * rather than introducing an unrelated rainbow scale. */
function heatColor(value: number, min: number, max: number): string {
  const t = max > min ? (value - min) / (max - min) : 0.5
  const pct = Math.round(10 + t * 80)
  return `color-mix(in oklch, var(--muted) 100%, var(--primary) ${pct}%)`
}

/** A factor-combination heatmap complementing (not replacing) the precise
 * Cells table below — 1-2 factors render as a single grid, 3 facet into one
 * grid per level of the third. Both the factors (axes) and the metric
 * (color) are derived from what's actually on the cells, with zero setup
 * required — an explicit design_spec.factors/task_brief.selection_metric is
 * used when present, but never required; most experiments won't have
 * either. Silently renders nothing for >3 derived factors, <2 cells, or no
 * numeric metric anywhere: those are exactly the cases where a heatmap
 * can't show anything the table doesn't already say better. Replicate cells
 * sharing one combination are averaged, not just the first one picked. */
function CellsHeatmap({ experiment, cells }: { experiment: Experiment; cells: Cell[] }) {
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
    <div className="mb-6 space-y-4">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          by {rowFactor.name}
          {colFactor.name ? ` × ${colFactor.name}` : ''}
        </span>
        <div className="flex items-center gap-2">
          {availableMetrics.length > 1 && (
            <Select value={activeMetric} onValueChange={(v) => v !== null && setMetricKey(v)}>
              <SelectTrigger size="sm" className="w-40">
                <SelectValue>{(v: string) => v.replace(/_/g, ' ')}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {availableMetrics.map((m) => (
                  <SelectItem key={m} value={m}>
                    {m.replace(/_/g, ' ')}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <span className="font-mono">{min.toFixed(3)}</span>
          <div
            className="h-2 w-16 rounded-full"
            style={{
              background:
                'linear-gradient(to right, color-mix(in oklch, var(--muted) 100%, var(--primary) 10%), color-mix(in oklch, var(--muted) 100%, var(--primary) 90%))',
            }}
          />
          <span className="font-mono">{max.toFixed(3)}</span>
        </div>
      </div>
      <div className={cn('grid gap-4', facetLevels.length > 1 && 'sm:grid-cols-2')}>
        {facetLevels.map((facetLevel, fi) => (
          <div key={fi} className="space-y-1.5">
            {facetFactor && (
              <p className="font-mono text-xs text-muted-foreground">
                {facetFactor.name} = {String(facetLevel)}
              </p>
            )}
            <div className="grid gap-1" style={{ gridTemplateColumns: `auto repeat(${colFactor.levels.length}, 1fr)` }}>
              <div />
              {colFactor.name
                ? colFactor.levels.map((colLevel, ci) => (
                    <div key={ci} className="truncate text-center font-mono text-xs text-muted-foreground">
                      {String(colLevel)}
                    </div>
                  ))
                : <div />}
              {rowFactor.levels.map((rowLevel, ri) => (
                <Fragment key={ri}>
                  <div className="flex items-center pr-2 font-mono text-xs text-muted-foreground">
                    {String(rowLevel)}
                  </div>
                  {colFactor.levels.map((_, ci) => {
                    const cellData = grid[fi][ri][ci]
                    return (
                      <div
                        key={ci}
                        className="flex aspect-square min-h-12 items-center justify-center rounded-md border border-border/50 font-mono text-xs"
                        style={cellData.value !== null ? { background: heatColor(cellData.value, min, max) } : undefined}
                        title={cellData.count > 0 ? `n=${cellData.count}` : 'no cell for this combination'}
                      >
                        {cellData.value !== null ? cellData.value.toFixed(3) : cellData.count > 0 ? '…' : ''}
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

type CellSort = { key: string; dir: 'asc' | 'desc' }

function cellSortValue(cell: Cell, key: string): string | number {
  if (key === 'cell_label') return cell.cell_label.toLowerCase()
  if (key === 'updated_at') return new Date(cell.updated_at).getTime()
  if (key === 'status') return cell.metric_values ? 1 : 0
  if (key.startsWith('factor:')) return String(cell.factor_values?.[key.slice(7)] ?? '').toLowerCase()
  if (key.startsWith('metric:')) {
    const v = cell.metric_values?.[key.slice(7)]
    return typeof v === 'number' ? v : Number.NEGATIVE_INFINITY
  }
  return ''
}

function SortableCellHead({ label, sortKey, sort, onSort }: { label: string; sortKey: string; sort: CellSort; onSort: (key: string) => void }) {
  const active = sort.key === sortKey
  const Icon = active ? (sort.dir === 'asc' ? ArrowUp : ArrowDown) : ChevronsUpDown
  return (
    <TableHead className="h-11">
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          'flex items-center gap-1 text-xs font-semibold tracking-wide uppercase hover:text-foreground',
          active ? 'text-foreground' : 'text-muted-foreground',
        )}
      >
        {label}
        <Icon className={cn('size-3.5', !active && 'opacity-40')} />
      </button>
    </TableHead>
  )
}

function formatMetricValue(value: unknown): string {
  return typeof value === 'number' ? value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '') : '—'
}

/** A real table -- one column per derived factor, one per curated metric --
 * not two squished `key=value, key=value` string dumps styled like a table.
 * Every column is independently sortable. */
function CellsTable({ experiment, cells }: { experiment: Experiment; cells: Cell[] }) {
  const [sort, setSort] = useState<CellSort>({ key: 'updated_at', dir: 'desc' })
  const factors = useMemo(() => deriveFactors(cells, experiment.design_spec) ?? [], [cells, experiment.design_spec])
  const metricColumns = useMemo(() => pickMetricColumns(experiment, cells), [experiment, cells])

  function handleSort(key: string) {
    setSort((prev) => (prev.key === key ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }))
  }

  const sorted = useMemo(() => {
    const rows = [...cells]
    rows.sort((a, b) => {
      const av = cellSortValue(a, sort.key)
      const bv = cellSortValue(b, sort.key)
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return sort.dir === 'asc' ? cmp : -cmp
    })
    return rows
  }, [cells, sort])

  return (
    <Table>
      <TableHeader className="bg-muted/40">
        <TableRow>
          <SortableCellHead label="Cell" sortKey="cell_label" sort={sort} onSort={handleSort} />
          {factors.map((f) => (
            <SortableCellHead key={f.name} label={f.name} sortKey={`factor:${f.name}`} sort={sort} onSort={handleSort} />
          ))}
          {metricColumns.map((m) => (
            <SortableCellHead key={m} label={m.replace(/_/g, ' ')} sortKey={`metric:${m}`} sort={sort} onSort={handleSort} />
          ))}
          <SortableCellHead label="Status" sortKey="status" sort={sort} onSort={handleSort} />
          <SortableCellHead label="Updated" sortKey="updated_at" sort={sort} onSort={handleSort} />
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.map((cell) => (
          <TableRow key={cell.id} className="even:bg-muted/15">
            <TableCell className="py-3.5 font-mono text-sm font-medium">{cell.cell_label}</TableCell>
            {factors.map((f) => (
              <TableCell key={f.name} className="py-3.5 font-mono text-xs text-muted-foreground">
                {cell.factor_values && f.name in cell.factor_values ? String(cell.factor_values[f.name]) : '—'}
              </TableCell>
            ))}
            {metricColumns.map((m) => (
              <TableCell key={m} className="py-3.5 font-mono text-xs text-muted-foreground">
                {formatMetricValue(cell.metric_values?.[m])}
              </TableCell>
            ))}
            <TableCell className="py-3.5">
              <Badge variant={cell.metric_values ? 'default' : 'secondary'}>{cell.metric_values ? 'Scored' : 'Pending'}</Badge>
            </TableCell>
            <TableCell className="py-3.5 text-muted-foreground">{new Date(cell.updated_at).toLocaleString()}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** The table only (not the heatmap) is togglable into a full-viewport
 * overlay -- a real factorial sweep's table can get wide (several factor
 * columns + up to 4 metric columns), and the card's own max-w-5xl column is
 * not where you want to read it. The heatmap stays put on the page either
 * way; it's not what gets wide. Not the browser Fullscreen API (that hides
 * browser chrome entirely, a bigger commitment than "let me see this table
 * properly" calls for) -- a fixed, full-viewport overlay with an
 * Escape/button close is the same "maximize" pattern most dashboards use.
 * The Card (with the heatmap) always renders normally; the overlay is an
 * extra layer on top, not a replacement for it -- so the table itself is
 * only ever mounted once at a time, never duplicated with its own
 * independent, out-of-sync sort state in two places at once. */
function CellsSection({
  experiment,
  cells,
  isLoading,
}: {
  experiment: Experiment
  cells: Cell[] | undefined
  isLoading: boolean
}) {
  const [fullscreen, setFullscreen] = useState(false)

  useEffect(() => {
    if (!fullscreen) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFullscreen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [fullscreen])

  const canExpand = !isLoading && !!cells && cells.length > 0

  const table = isLoading ? (
    <div className="space-y-2">
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-8 w-full" />
    </div>
  ) : !cells || cells.length === 0 ? (
    <p className="py-6 text-center text-sm text-muted-foreground">
      No cells yet — this experiment's design hasn't been generated.
    </p>
  ) : (
    <CellsTable experiment={experiment} cells={cells} />
  )

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Cells</CardTitle>
          <CardDescription>One row per design point in this experiment's factorial grid.</CardDescription>
        </CardHeader>
        <CardContent>
          {cells && <CellsHeatmap experiment={experiment} cells={cells} />}
          {canExpand && (
            <div className="mb-2 flex justify-end">
              <Button variant="outline" size="icon-sm" onClick={() => setFullscreen(true)} aria-label="View table full screen">
                <Maximize2 className="size-4" />
              </Button>
            </div>
          )}
          {!fullscreen && table}
        </CardContent>
      </Card>

      {fullscreen && (
        <div className="fixed inset-0 z-50 overflow-auto bg-background p-6">
          <div className="mx-auto max-w-[95vw] space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">Cells table</h2>
              <Button variant="outline" size="icon" onClick={() => setFullscreen(false)} aria-label="Exit full screen">
                <Minimize2 className="size-4" />
              </Button>
            </div>
            {table}
          </div>
        </div>
      )}
    </>
  )
}

function DatasetCard({ datasetId }: { datasetId: string }) {
  const { data: dataset, isLoading } = useQuery({
    queryKey: ['datasets', datasetId],
    queryFn: () => datasetsApi.get(datasetId),
  })

  if (isLoading) return <Skeleton className="h-40 w-full" />
  if (!dataset) return null

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Database className="size-4 text-primary" />
          <CardTitle className="font-mono">{dataset.name}</CardTitle>
        </div>
        <CardDescription>{dataset.description ?? 'Registered dataset for this experiment.'}</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          <div className="flex items-center justify-between sm:col-span-2">
            <dt className="text-muted-foreground">Target column</dt>
            <dd className="font-mono">{dataset.target_column ?? '—'}</dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-muted-foreground">Train split</dt>
            <dd className="flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-primary shadow-[0_0_6px_var(--primary)]" />
              <span className="font-mono text-xs text-muted-foreground">{dataset.train_sha256.slice(0, 10)}</span>
            </dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-muted-foreground">Test split</dt>
            <dd className="flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-primary shadow-[0_0_6px_var(--primary)]" />
              <span className="font-mono text-xs text-muted-foreground">{dataset.test_sha256.slice(0, 10)}</span>
            </dd>
          </div>
          {dataset.dictionary_json && (
            <div className="flex items-center justify-between sm:col-span-2">
              <dt className="text-muted-foreground">Data dictionary</dt>
              <dd>
                <Badge variant="outline" className="font-normal text-muted-foreground">
                  attached
                </Badge>
              </dd>
            </div>
          )}
          <div className="flex items-center justify-between sm:col-span-2">
            <dt className="text-muted-foreground">Registered</dt>
            <dd title={dataset.created_at ? new Date(dataset.created_at).toLocaleString() : undefined}>
              {dataset.created_at ? formatDate(dataset.created_at) : '—'}
            </dd>
          </div>
        </dl>
      </CardContent>
    </Card>
  )
}

function AgentCard({ agent, runCount, lastUsed }: { agent: Agent; runCount: number; lastUsed: string }) {
  // Tinted by model, not status -- there's no notion of "done"/"pending" for an
  // agent, but coloring by model gives an at-a-glance "which agents share an
  // LLM" read once more than a couple of agents show up here.
  const accent = agent.model_config.model ? hashToChartHue(agent.model_config.model) : undefined
  return (
    <Card style={accent ? cardAccent(accent) : undefined}>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <Bot className="size-4 shrink-0 text-[color:var(--card-accent,var(--primary))]" />
            <CardTitle className="truncate">{agent.name}</CardTitle>
          </div>
          {agent.model_config.model && (
            <Badge
              variant="outline"
              className="shrink-0 font-mono font-normal text-[color:var(--card-accent,var(--primary))]"
            >
              {agent.model_config.model}
            </Badge>
          )}
        </div>
        <CardDescription className="line-clamp-2 min-h-10">{agent.goal || agent.description || 'No goal set.'}</CardDescription>
      </CardHeader>
      <CardContent className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {runCount} run{runCount === 1 ? '' : 's'} in this experiment
        </span>
        <span className="font-mono" title={new Date(lastUsed).toLocaleString()}>
          {formatRelative(lastUsed)}
        </span>
      </CardContent>
    </Card>
  )
}

function AgentsSection({ experimentId }: { experimentId: string }) {
  const runsQuery = useQuery({ queryKey: ['runs'], queryFn: () => runsApi.list() })
  const agentsQuery = useQuery({ queryKey: ['agents'], queryFn: () => agentsApi.list() })

  const experimentAgents = useMemo(() => {
    if (!runsQuery.data || !agentsQuery.data) return null
    const agentsById = new Map(agentsQuery.data.map((a) => [a.id, a]))
    const stats = new Map<string, { count: number; lastUsed: string }>()
    for (const run of runsQuery.data) {
      if (run.run_metadata?.experiment_id !== experimentId) continue
      const existing = stats.get(run.agent_id)
      if (existing) {
        existing.count += 1
        if (run.created_at > existing.lastUsed) existing.lastUsed = run.created_at
      } else {
        stats.set(run.agent_id, { count: 1, lastUsed: run.created_at })
      }
    }
    return Array.from(stats, ([agentId, s]) => ({ agent: agentsById.get(agentId), ...s }))
      .filter((x): x is { agent: Agent; count: number; lastUsed: string } => !!x.agent)
      .sort((a, b) => b.count - a.count)
  }, [runsQuery.data, agentsQuery.data, experimentId])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Agents</CardTitle>
        <CardDescription>Agents that have run at least one step in this experiment.</CardDescription>
      </CardHeader>
      <CardContent>
        {runsQuery.isLoading || agentsQuery.isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-28 w-full" />
          </div>
        ) : !experimentAgents || experimentAgents.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-10 text-center">
            <Bot className="size-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">No agent runs recorded for this experiment yet.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {experimentAgents.map(({ agent, count, lastUsed }) => (
              <AgentCard key={agent.id} agent={agent} runCount={count} lastUsed={lastUsed} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function ExperimentDetailPage() {
  const { experimentId } = useParams<{ experimentId: string }>()

  const experimentQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId!),
    enabled: !!experimentId,
  })
  const cellsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'cells'],
    queryFn: () => experimentsApi.listCells(experimentId!),
    enabled: !!experimentId,
  })

  return (
    <div className="min-h-svh bg-muted/30">
      <AppHeader />

      <main className="mx-auto max-w-5xl space-y-6 px-6 py-10">
        <div>
          <Link to="/experiments" className="text-sm text-muted-foreground hover:underline">
            ← Experiments
          </Link>
        </div>

        {experimentQuery.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : experimentQuery.isError || !experimentQuery.data ? (
          <p className="text-sm text-muted-foreground">Could not load this experiment.</p>
        ) : (
          <div className="flex items-center gap-2.5">
            <FlaskConical className="size-6 text-primary" />
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">{experimentQuery.data.name}</h1>
              {experimentQuery.data.description && (
                <p className="text-sm text-muted-foreground">{experimentQuery.data.description}</p>
              )}
            </div>
          </div>
        )}

        {experimentQuery.data &&
          (() => {
            // Cells/factor_values/metric_values are a FactorialCellResult concept --
            // the only experiment type ASAREE's backend actually implements today
            // (ab_experiments/discoveries/etc. are explicitly out of scope on the
            // model itself), but design_type is a plain string specifically so
            // another type COULD exist later. Gate on it now rather than silently
            // assuming every experiment has cells, so a future non-factorial type
            // doesn't render a nonsensical "0/0 scored" stat.
            const isFactorial = experimentQuery.data.design_type === 'factorial'
            const factors = factorCount(experimentQuery.data.design_spec)
            const scored = cellsQuery.data?.filter((c) => c.metric_values).length
            const best = bestMetric(experimentQuery.data, cellsQuery.data)
            return (
              <div className={cn('grid grid-cols-1 gap-4', isFactorial && 'sm:grid-cols-3')}>
                <StatCard
                  icon={Layers}
                  label="Design"
                  value={experimentQuery.data!.design_type}
                  sub={factors !== null ? `${factors} factor${factors === 1 ? '' : 's'}` : undefined}
                />
                {isFactorial && (
                  <>
                    <StatCard
                      icon={Target}
                      label="Cells"
                      value={cellsQuery.data ? `${scored}/${cellsQuery.data.length} scored` : '—'}
                      accent={cellsStatusAccent(cellsQuery.data)}
                    />
                    <StatCard
                      icon={Trophy}
                      label={best ? best.key.replace(/_/g, ' ') : 'Best metric'}
                      value={best ? best.value.toFixed(4) : '—'}
                      accent={best ? 'var(--chart-3)' : undefined}
                    />
                  </>
                )}
              </div>
            )
          })()}

        {experimentQuery.data?.dataset_id ? (
          <DatasetCard datasetId={experimentQuery.data.dataset_id} />
        ) : experimentQuery.data ? (
          <Card>
            <CardContent className="flex items-center gap-3 text-sm text-muted-foreground">
              <Database className="size-4 shrink-0" />
              No dataset attached to this experiment.
            </CardContent>
          </Card>
        ) : null}

        {experimentId && <AgentsSection experimentId={experimentId} />}

        {experimentQuery.data && experimentQuery.data.design_type !== 'factorial' ? (
          <Card>
            <CardContent className="flex items-center gap-3 text-sm text-muted-foreground">
              <Target className="size-4 shrink-0" />
              Cell-based results aren't available for &ldquo;{experimentQuery.data.design_type}&rdquo; experiments.
            </CardContent>
          </Card>
        ) : experimentQuery.data ? (
          <CellsSection experiment={experimentQuery.data} cells={cellsQuery.data} isLoading={cellsQuery.isLoading} />
        ) : (
          <Skeleton className="h-40 w-full" />
        )}
      </main>
    </div>
  )
}
