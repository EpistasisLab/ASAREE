import type { Cell, Experiment } from '@/types/experiments'

export function factorCount(designSpec: Experiment['design_spec']): number | null {
  const factors = (designSpec as { factors?: unknown } | null)?.factors
  return Array.isArray(factors) ? factors.length : null
}

// A "whole node as a factor" level (LLM/Tool config, pattern override) is a
// plain object, not a scalar -- JS's default `String({...})` collapses every
// distinct object to the same "[object Object]", which would silently
// conflate different LLM/Tool/Pattern configs wherever this codebase
// compares/dedupes/sorts factor values by their string form (this module's own
// deriveFactors/cellsMatching). A canonical (recursively key-sorted) JSON
// string is stable across separately-deserialized-but-content-identical
// objects, which `new Set(...)`'s reference-based dedup and JS's own
// `String()` both are not.
function canonicalStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalStringify).join(',')}]`
  if (value !== null && typeof value === 'object') {
    const entries = Object.keys(value as Record<string, unknown>)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonicalStringify((value as Record<string, unknown>)[k])}`)
    return `{${entries.join(',')}}`
  }
  return JSON.stringify(value)
}

/** Stable equality/sort key for a factor level of ANY shape -- use this
 * instead of `String(value)` wherever two factor values are compared or
 * deduped (see canonicalStringify's own comment for why). */
export function factorValueKey(value: unknown): string {
  return value !== null && typeof value === 'object' ? canonicalStringify(value) : String(value)
}

// Mirrors services.design_generation.py's own _slugify priority-key
// heuristic -- for a dict-valued level, show whichever of these a human
// actually recognizes ("claude-sonnet-5") instead of "[object Object]",
// same reasoning, same key order, so a cell's label and its Cells-table
// column read consistently.
const FACTOR_VALUE_DISPLAY_PRIORITY_KEYS = ['model', 'provider', 'execution_pattern', 'server_name', 'enabled']

/** Human-readable rendering of a factor value for table/badge display --
 * scalars render as-is; a dict-valued "whole node" level (LLM/Tool config,
 * pattern override) picks its own most identifying field instead of
 * JS's default object stringification. */
export function displayFactorValue(value: unknown): string {
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    const v = value as Record<string, unknown>
    for (const key of FACTOR_VALUE_DISPLAY_PRIORITY_KEYS) {
      if (key in v && v[key] !== null && v[key] !== '') return String(v[key])
    }
    return canonicalStringify(value)
  }
  return String(value)
}

export interface FactorSpec {
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

export function replicateNumberForLabel(label: string): number {
  const match = /__rep(\d+)$/.exec(label)
  return match ? Number(match[1]) : 1
}

export function cellLabelForReplicateLabel(label: string): string {
  return label.replace(/__rep\d+$/, '')
}

export interface ExperimentalCell {
  label: string
  factorValues: Record<string, unknown>
  replicates: Cell[]
  scoredReplicateCount: number
  updatedAt: string
}

/** Group the persistence layer's one-row-per-replicate results into the
 * user-facing cell they belong to: one factor combination and all of its
 * replicates. Generated labels provide a stable fallback for legacy rows
 * without factor values. */
export function groupReplicatesIntoCells(replicates: Cell[]): ExperimentalCell[] {
  const groups = new Map<string, Cell[]>()
  for (const replicate of replicates) {
    const factorEntries = Object.entries(replicate.factor_values ?? {})
      .filter(([key]) => !NON_FACTOR_KEYS.has(key.toLowerCase()))
      .sort(([a], [b]) => a.localeCompare(b))
    const key = replicate.cell_id || (factorEntries.length > 0
      ? canonicalStringify(Object.fromEntries(factorEntries))
      : replicate.factorial_cell_label)
    const group = groups.get(key)
    if (group) group.push(replicate)
    else groups.set(key, [replicate])
  }

  return Array.from(groups.values(), (group) => {
    const sorted = [...group].sort(
      (a, b) => a.replicate_number - b.replicate_number,
    )
    const latest = sorted.reduce((a, b) => (a.updated_at > b.updated_at ? a : b))
    return {
      label: sorted[0].factorial_cell_label,
      factorValues: Object.fromEntries(
        Object.entries(sorted[0].factor_values ?? {}).filter(([key]) => !NON_FACTOR_KEYS.has(key.toLowerCase())),
      ),
      replicates: sorted,
      scoredReplicateCount: sorted.filter((replicate) => !!replicate.metric_values).length,
      updatedAt: latest.updated_at,
    }
  })
}

/** The CELLS decide which factors exist; design_spec.factors only refines the
 * level ordering of names that actually appear on them. It used to win
 * outright, which broke the heatmap silently and completely: a real
 * experiment here declared factors named "Azure Foundry:Model"/"Azure
 * Foundry:Effort"/"Critic enabled" while its cells were keyed
 * tier/effort/critic, so every cellsMatching() lookup against a declared name
 * matched zero cells, every square came out null, and the grid vanished with
 * no error anywhere. Declared names are human-authored labels for canvas
 * nodes; cell keys come from the design generator -- there is nothing keeping
 * the two in sync, so nothing may assume they are. Derive
 * factors straight from what's actually on the cells, so this works with
 * zero setup for the common case where nothing was ever declared. Shared by
 * the Cells table/heatmap (components/protocol/cells/) and the Run button's
 * cell picker (SelectCellDialog.tsx) -- both need the same "what factors/
 * levels exist across these cells" answer. */
export function deriveFactors(cells: Cell[], designSpec: Experiment['design_spec']): FactorSpec[] | null {
  const declared = getFactors(designSpec)

  const keys: string[] = []
  for (const c of cells) {
    for (const k of Object.keys(c.factor_values ?? {})) {
      if (!NON_FACTOR_KEYS.has(k.toLowerCase()) && !keys.includes(k)) keys.push(k)
    }
  }

  // Nothing observed to go on (no cells yet, or none carrying factor_values) --
  // a declared spec is then the only description of the design there is.
  if (keys.length === 0) return declared && declared.length > 0 ? declared : null

  const declaredByName = new Map((declared ?? []).map((f) => [f.name, f]))

  const factors = keys.map((name) => {
    const values = cells.map((c) => c.factor_values?.[name]).filter((v) => v !== undefined)
    // Dedup by content (factorValueKey), not by reference -- `new Set` would
    // treat every separately-deserialized dict-valued level (e.g. two cells
    // both bound to the same LLM config) as distinct, since object identity
    // never survives a JSON round-trip.
    const seen = new Map<string, unknown>()
    for (const v of values) {
      const key = factorValueKey(v)
      if (!seen.has(key)) seen.set(key, v)
    }

    // Declared levels first, in their declared order, then anything observed
    // that wasn't declared. This keeps both halves of what a declaration is
    // good for: deliberate level ORDER, and levels that are planned but have
    // no cell yet (an empty column is informative -- it's the run you haven't
    // done). It just no longer decides which factors EXIST.
    const declaredLevels = declaredByName.get(name)?.levels ?? []
    const declaredKeys = new Set(declaredLevels.map(factorValueKey))
    const extras = Array.from(seen.entries())
      .filter(([key]) => !declaredKeys.has(key))
      .map(([, v]) => v)
      .sort((a, b) => factorValueKey(a).localeCompare(factorValueKey(b)))

    return { name, levels: [...declaredLevels, ...extras] }
  })
  return factors.length > 0 ? factors : null
}

/** task_brief.selection_metric, when present, is only ever a HINT for which
 * of the observed metric keys to default to below — never a requirement.
 * Most experiments won't have this persisted (it's a notebook-local variable
 * embedded straight into agent prompts, not something every user thinks to
 * also send to the experiment record), so nothing here may depend on it. */
function selectionMetricHint(experiment: Experiment | undefined): string | undefined {
  return (experiment?.task_brief as { selection_metric?: string } | undefined)?.selection_metric
}

/** Every top-level numeric key actually observed across scored replicates --
 * what a metric "Color by" selector can offer, with zero setup required. */
export function availableMetricKeys(cells: Cell[]): string[] {
  const keys = new Set<string>()
  for (const c of cells) {
    for (const [k, v] of Object.entries(c.metric_values ?? {})) {
      if (typeof v === 'number') keys.add(k)
    }
  }
  return Array.from(keys).sort()
}

const PREFERRED_METRICS = ['average_precision', 'roc_auc', 'accuracy', 'f1']

/** Unit suffixes baked into a metric key's own name (cost_usd, duration_s)
 * read better as "cost (USD)"/"duration (minutes)" than a literal underscore
 * swap -- everything else still falls back to that. */
const METRIC_LABEL_OVERRIDES: Record<string, string> = {
  cost_usd: 'cost (USD)',
  duration_s: 'duration (minutes)',
  n_features_created: 'n eng. feat.',
  n_created_selected: 'n eng. feat. selected',
  frac_created_selected: '% eng. feat. selected',
}

export function formatMetricLabel(key: string): string {
  return METRIC_LABEL_OVERRIDES[key] ?? key.replace(/_/g, ' ')
}

/** duration_s (seconds -> minutes) and frac_created_selected (a 0-1 fraction
 * -> a 0-100 number, paired with the "%" suffix below) are stored/averaged/
 * sorted in their raw units -- this only rescales what's displayed. Sort
 * order is unaffected either way (a positive linear scale), so
 * cellSortValue/meanMetric stay on the raw values untouched. */
export function scaledMetricValue(key: string, value: number): number {
  if (key === 'duration_s') return value / 60
  if (key === 'frac_created_selected') return value * 100
  return value
}

/** Appended after the scaled, formatted number -- kept separate from
 * scaledMetricValue so a metric can add a unit suffix without needing its
 * own numeric rescale (or vice versa). */
const METRIC_VALUE_SUFFIXES: Record<string, string> = {
  frac_created_selected: '%',
}

export function metricValueSuffix(key: string): string {
  return METRIC_VALUE_SUFFIXES[key] ?? ''
}

export function pickDefaultMetric(experiment: Experiment | undefined, cells: Cell[]): string | null {
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
export function pickMetricColumns(experiment: Experiment | undefined, cells: Cell[], max = 4): string[] {
  const available = availableMetricKeys(cells)
  const hint = selectionMetricHint(experiment)
  const ordered: string[] = []
  if (hint && available.includes(hint)) ordered.push(hint)
  for (const p of PREFERRED_METRICS) if (available.includes(p) && !ordered.includes(p)) ordered.push(p)
  for (const k of available) if (!ordered.includes(k)) ordered.push(k)
  return ordered.slice(0, max)
}

/** The single headline number for an experiment -- the best observed value of
 * whichever metric pickDefaultMetric settles on. Drives the canvas top bar's
 * own "best metric" readout. */
export function bestMetric(experiment: Experiment | undefined, cells: Cell[] | undefined): { key: string; value: number } | null {
  if (!cells) return null
  const key = pickDefaultMetric(experiment, cells)
  if (!key) return null
  const values = groupReplicatesIntoCells(cells)
    .map((cell) => meanMetric(cell.replicates, key))
    .filter((value): value is number => value !== null)
  if (values.length === 0) return null
  return { key, value: Math.max(...values) }
}

/** Formatted for display: scaled into its display unit, trailing zeros
 * trimmed, unit suffix appended, non-numbers rendered as an em dash. */
export function formatMetricValue(key: string, value: unknown): string {
  if (typeof value !== 'number') return '—'
  const formatted = scaledMetricValue(key, value).toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
  return `${formatted}${metricValueSuffix(key)}`
}

/** Every cell whose factor_values match `match` on all of its keys -- i.e. one
 * combination's replicates. Compared via factorValueKey so dict-valued levels
 * match by content, not by reference. */
export function cellsMatching(cells: Cell[], match: Record<string, unknown>): Cell[] {
  return cells.filter((c) => Object.entries(match).every(([k, v]) => factorValueKey(c.factor_values?.[k]) === factorValueKey(v)))
}

/** Replicates sharing one factor combination are AVERAGED for the heatmap,
 * not "first one wins" -- see the heatmap's own comment. */
export function meanMetric(cells: Cell[], metricKey: string): number | null {
  const values = cells.map((c) => c.metric_values?.[metricKey]).filter((v): v is number => typeof v === 'number')
  if (values.length === 0) return null
  return values.reduce((a, b) => a + b, 0) / values.length
}

/** Low values read as a dim, muted box; high values glow in the theme's own
 * accent — keeps the heatmap inside the same limited cyan-forward palette
 * rather than introducing an unrelated rainbow scale. */
export function heatColor(value: number, min: number, max: number): string {
  const t = max > min ? (value - min) / (max - min) : 0.5
  const pct = Math.round(10 + t * 80)
  return `color-mix(in oklch, var(--muted) 100%, var(--primary) ${pct}%)`
}

/** "chart-4" (amber, generated-but-unscored) / "chart-3" (emerald, all replicates scored) /
 * "primary" (cyan, some replicates scored) / "muted-foreground" (dim, no cells yet). */
export function cellsStatusAccent(cells: Cell[] | undefined): string {
  const total = cells?.length ?? 0
  if (total === 0) return 'var(--muted-foreground)'
  const scored = cells!.filter((c) => c.metric_values).length
  if (scored === 0) return 'var(--chart-4)'
  if (scored === total) return 'var(--chart-3)'
  return 'var(--primary)'
}

// The placeholder-name generator that used to live here is gone: naming a new
// experiment is the server's job now (POST /experiments with no name), because
// no client can pick a name safely across the gap between reading the list and
// inserting. See services/experiments.py's create_untitled_experiment.
