import type { Cell, Experiment } from '@/types/experiments'

export function factorCount(designSpec: Experiment['design_spec']): number | null {
  const factors = (designSpec as { factors?: unknown } | null)?.factors
  return Array.isArray(factors) ? factors.length : null
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
