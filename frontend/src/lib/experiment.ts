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
