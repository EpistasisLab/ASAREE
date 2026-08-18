import type { Cell, Experiment } from '@/types/experiments'

export function factorCount(designSpec: Experiment['design_spec']): number | null {
  const factors = (designSpec as { factors?: unknown } | null)?.factors
  return Array.isArray(factors) ? factors.length : null
}

// A "whole node as a factor" level (LLM/Tool config, pattern override) is a
// plain object, not a scalar -- JS's default `String({...})` collapses every
// distinct object to the same "[object Object]", which would silently
// conflate different LLM/Tool/Pattern configs wherever this codebase
// compares/dedupes/sorts factor values by their string form (ExperimentDetailPage's
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

/** An explicit design_spec.factors declaration wins when present (more
 * authoritative -- it can order levels deliberately); otherwise derive
 * factors straight from what's actually on the cells, so this works with
 * zero setup for the common case where nothing was ever declared. Shared by
 * the Cells table/heatmap (ExperimentDetailPage.tsx) and the Run button's
 * cell picker (SelectCellDialog.tsx) -- both need the same "what factors/
 * levels exist across these cells" answer. */
export function deriveFactors(cells: Cell[], designSpec: Experiment['design_spec']): FactorSpec[] | null {
  const declared = getFactors(designSpec)
  if (declared && declared.length > 0) return declared

  const keys: string[] = []
  for (const c of cells) {
    for (const k of Object.keys(c.factor_values ?? {})) {
      if (!NON_FACTOR_KEYS.has(k.toLowerCase()) && !keys.includes(k)) keys.push(k)
    }
  }
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
    return {
      name,
      levels: Array.from(seen.values()).sort((a, b) => factorValueKey(a).localeCompare(factorValueKey(b))),
    }
  })
  return factors.length > 0 ? factors : null
}

/** "chart-4" (amber, generated-but-unscored) / "chart-3" (emerald, fully scored) /
 * "primary" (cyan, partially scored) / "muted-foreground" (dim, no cells yet). */
export function cellsStatusAccent(cells: Cell[] | undefined): string {
  const total = cells?.length ?? 0
  if (total === 0) return 'var(--muted-foreground)'
  const scored = cells!.filter((c) => c.metric_values).length
  if (scored === 0) return 'var(--chart-4)'
  if (scored === total) return 'var(--chart-3)'
  return 'var(--primary)'
}

const UNTITLED_PATTERN = /^Untitled Experiment(?: (\d+))?$/

// n8n-style: no name/description gate before creating -- a placeholder name
// (bumped past whatever "Untitled Experiment [N]" the user already has, so
// it doesn't collide with uq_research_experiments_owner_name) lets creation
// be a single click, straight into the protocol canvas to rename and build
// the pipeline as nodes. That's the point of creating from the GUI at all,
// so there's no separate "empty experiment" landing state to design for.
// Global (AppHeader's "+" menu), not page-local, so it can be triggered from
// anywhere -- not just the Experiments list.
export function nextUntitledName(existing: Experiment[] | undefined): string {
  let maxN = 0
  let sawUntitled = false
  for (const e of existing ?? []) {
    const m = UNTITLED_PATTERN.exec(e.name)
    if (!m) continue
    sawUntitled = true
    maxN = Math.max(maxN, m[1] ? Number(m[1]) : 1)
  }
  return sawUntitled ? `Untitled Experiment ${maxN + 1}` : 'Untitled Experiment'
}
