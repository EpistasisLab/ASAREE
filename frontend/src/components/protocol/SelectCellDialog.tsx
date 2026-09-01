import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { deriveFactors, displayFactorValue, factorValueKey } from '@/lib/experiment'
import type { Experiment, Replicate } from '@/types/experiments'

// The API already supplies explicit cell labels and replicate numbers; sort
// by both so each cell's replicates read 1, 2, ..., 10.
function sortByReplicateOrder(replicates: Replicate[]): Replicate[] {
  return [...replicates].sort((a, b) => {
    const baseCompare = a.cell_label.localeCompare(b.cell_label)
    if (baseCompare !== 0) return baseCompare
    return a.replicate_number - b.replicate_number
  })
}

// Replaces a plain <Select> for the canvas Run button's replicate picker
// (ProtocolCanvas.tsx) -- a dropdown truncates a real cell_label plus its
// factor summary the moment there's more than a couple of short factors,
// and base-ui's Select popup width is driven by its trigger, not its
// content. A full dialog has room for the whole label and a per-factor
// summary, plus a filter once a sweep has more than a handful of cells.
// Matches RunConfirmDialog/DeleteNodeConfirmDialog's own shell.
export function SelectReplicateDialog({
  replicates,
  designSpec,
  selectedReplicateLabel,
  onCancel,
  onSelect,
}: {
  replicates: Replicate[]
  designSpec: Experiment['design_spec'] | undefined
  selectedReplicateLabel: string | null
  onCancel: () => void
  onSelect: (replicateLabel: string | null) => void
}) {
  const [filter, setFilter] = useState('')
  // factor name -> set of checked levels' factorValueKey. A factor with an
  // empty set is unconstrained (every level of it passes) -- checking a
  // level narrows to cells matching ANY checked level of that factor
  // (OR within a factor), while factors with something checked combine with
  // AND (a cell must satisfy every constrained factor at once) -- the
  // standard faceted-filter semantics (same shape a shopping-site sidebar
  // filter uses), the fastest way to land on "which one of the 10 replicates
  // of this exact combination" without typing the whole cell_label.
  const [checkedLevels, setCheckedLevels] = useState<Record<string, Set<string>>>({})

  // `?? null` because this prop is optional here but deriveFactors takes
  // `DesignSpec | null` -- an absent spec and an explicitly null one mean the
  // same thing to it (derive the factors from the replicates instead).
  const factors = useMemo(() => deriveFactors(replicates, designSpec ?? null) ?? [], [replicates, designSpec])

  function toggleLevel(factorName: string, levelKey: string) {
    setCheckedLevels((prev) => {
      const next = new Set(prev[factorName] ?? [])
      if (next.has(levelKey)) next.delete(levelKey)
      else next.add(levelKey)
      return { ...prev, [factorName]: next }
    })
  }

  const anyChecked = Object.values(checkedLevels).some((s) => s.size > 0)
  const needle = filter.trim().toLowerCase()

  const sortedReplicates = useMemo(() => sortByReplicateOrder(replicates), [replicates])

  const filtered = sortedReplicates.filter((replicate) => {
    for (const [factorName, levels] of Object.entries(checkedLevels)) {
      if (levels.size === 0) continue
      if (!levels.has(factorValueKey(replicate.factor_values?.[factorName]))) return false
    }
    if (!needle) return true
    const summary = Object.entries(replicate.factor_values ?? {})
      .map(([k, v]) => `${k}=${displayFactorValue(v)}`)
      .join(' ')
    return replicate.replicate_label.toLowerCase().includes(needle) || summary.toLowerCase().includes(needle)
  })

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onCancel()
      }}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Run a specific replicate</DialogTitle>
          <DialogDescription>
            Pick one planned replicate to run with its cell's factor values substituted in -- or keep today's
            ad-hoc, un-substituted whole-graph pass.
          </DialogDescription>
        </DialogHeader>
        {factors.length > 0 && (
          <div className="space-y-2 rounded-md border p-2.5">
            <div className="flex items-center justify-between">
              <p className="text-xs font-medium text-muted-foreground">Filter by factor</p>
              {anyChecked && (
                <button
                  type="button"
                  className="cursor-pointer text-xs text-muted-foreground underline hover:text-foreground"
                  onClick={() => setCheckedLevels({})}
                >
                  Clear
                </button>
              )}
            </div>
            {factors.map((factor) => (
              <div key={factor.name}>
                <p className="truncate text-xs text-muted-foreground" title={factor.name}>
                  {factor.name}
                </p>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                  {factor.levels.map((level) => {
                    const levelKey = factorValueKey(level)
                    const checked = checkedLevels[factor.name]?.has(levelKey) ?? false
                    return (
                      <label key={levelKey} className="flex cursor-pointer items-center gap-1.5 text-xs">
                        <Checkbox checked={checked} onCheckedChange={() => toggleLevel(factor.name, levelKey)} />
                        {displayFactorValue(level)}
                      </label>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
        {replicates.length > 6 && (
          <Input autoFocus placeholder="Filter replicates…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        )}
        <ul className="max-h-80 space-y-1.5 overflow-y-auto text-sm">
          <li>
            <button
              type="button"
              onClick={() => onSelect(null)}
              className={`w-full cursor-pointer rounded-md border px-2.5 py-1.5 text-left hover:bg-muted ${
                selectedReplicateLabel === null ? 'border-primary ring-1 ring-primary/40' : ''
              }`}
            >
              <p className="font-medium">Ad-hoc run</p>
              <p className="text-xs text-muted-foreground">Today's whole-graph pass, no factor substitution.</p>
            </button>
          </li>
          {filtered.map((replicateResult) => {
            const hasFactors = replicateResult.factor_values && Object.keys(replicateResult.factor_values).length > 0
            const summary = hasFactors
              ? Object.entries(replicateResult.factor_values!)
                  .map(([k, v]) => `${k}: ${displayFactorValue(v)}`)
                  .join(' · ')
              : null
            const replicateNumber = replicateResult.replicate_number
            return (
              <li key={replicateResult.replicate_label}>
                <button
                  type="button"
                  onClick={() => onSelect(replicateResult.replicate_label)}
                  className={`w-full cursor-pointer rounded-md border px-2.5 py-1.5 text-left hover:bg-muted ${
                    selectedReplicateLabel === replicateResult.replicate_label ? 'border-primary ring-1 ring-primary/40' : ''
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    {/* The human-readable factor summary is the headline -- the raw
                        replicate_label (SF-DC-Effort_medium__...__rep3-shaped, not meant for
                        reading) drops to a small font-mono line below, kept for anyone
                        cross-referencing the Cells table/CSV export by that exact string. */}
                    <p className="truncate font-medium" title={summary ?? replicateResult.replicate_label}>
                      {summary ?? replicateResult.replicate_label}
                    </p>
                    <span
                      className="shrink-0 text-[10px] tracking-wide uppercase"
                      style={{ color: replicateResult.metric_values ? 'var(--chart-3)' : 'var(--chart-4)' }}
                    >
                      {replicateResult.metric_values ? 'Scored' : 'Pending'}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate font-mono text-[11px] text-muted-foreground/70" title={replicateResult.replicate_label}>
                      {replicateResult.replicate_label}
                    </p>
                    {hasFactors && <span className="shrink-0 text-[11px] text-muted-foreground">Replicate {replicateNumber}</span>}
                  </div>
                </button>
              </li>
            )
          })}
          {filtered.length === 0 && (
            <li className="px-2.5 py-1.5 text-xs text-muted-foreground">No replicates match the current filters.</li>
          )}
        </ul>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
