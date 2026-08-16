import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { displayFactorValue } from '@/lib/experiment'
import type { Cell } from '@/types/experiments'

// Replaces a plain <Select> for the canvas Run button's cell picker
// (ProtocolCanvas.tsx) -- a dropdown truncates a real cell_label plus its
// factor summary the moment there's more than a couple of short factors,
// and base-ui's Select popup width is driven by its trigger, not its
// content. A full dialog has room for the whole label and a per-factor
// summary, plus a filter once a sweep has more than a handful of cells.
// Matches RunWithIssuesDialog/DeleteNodeConfirmDialog's own shell.
export function SelectCellDialog({
  cells,
  selectedCellLabel,
  onCancel,
  onSelect,
}: {
  cells: Cell[]
  selectedCellLabel: string | null
  onCancel: () => void
  onSelect: (cellLabel: string | null) => void
}) {
  const [filter, setFilter] = useState('')
  const needle = filter.trim().toLowerCase()
  const filtered = needle
    ? cells.filter((cell) => {
        const summary = Object.entries(cell.factor_values ?? {})
          .map(([k, v]) => `${k}=${displayFactorValue(v)}`)
          .join(' ')
        return cell.cell_label.toLowerCase().includes(needle) || summary.toLowerCase().includes(needle)
      })
    : cells

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onCancel()
      }}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Run a specific cell</DialogTitle>
          <DialogDescription>
            Picks one already-generated cell to run for real, its own factor values substituted in -- or keep today's
            ad-hoc, un-substituted whole-graph pass.
          </DialogDescription>
        </DialogHeader>
        {cells.length > 6 && (
          <Input autoFocus placeholder="Filter cells…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        )}
        <ul className="max-h-80 space-y-1.5 overflow-y-auto text-sm">
          <li>
            <button
              type="button"
              onClick={() => onSelect(null)}
              className={`w-full cursor-pointer rounded-md border px-2.5 py-1.5 text-left hover:bg-muted ${
                selectedCellLabel === null ? 'border-primary ring-1 ring-primary/40' : ''
              }`}
            >
              <p className="font-medium">Ad-hoc run</p>
              <p className="text-xs text-muted-foreground">Today's whole-graph pass, no factor substitution.</p>
            </button>
          </li>
          {filtered.map((cell) => (
            <li key={cell.cell_label}>
              <button
                type="button"
                onClick={() => onSelect(cell.cell_label)}
                className={`w-full cursor-pointer rounded-md border px-2.5 py-1.5 text-left hover:bg-muted ${
                  selectedCellLabel === cell.cell_label ? 'border-primary ring-1 ring-primary/40' : ''
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate font-mono font-medium" title={cell.cell_label}>
                    {cell.cell_label}
                  </p>
                  <span
                    className="shrink-0 text-[10px] tracking-wide uppercase"
                    style={{ color: cell.metric_values ? 'var(--chart-3)' : 'var(--chart-4)' }}
                  >
                    {cell.metric_values ? 'Scored' : 'Pending'}
                  </span>
                </div>
                {cell.factor_values && Object.keys(cell.factor_values).length > 0 && (
                  <p className="truncate text-xs text-muted-foreground">
                    {Object.entries(cell.factor_values)
                      .map(([k, v]) => `${k}=${displayFactorValue(v)}`)
                      .join(', ')}
                  </p>
                )}
              </button>
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="px-2.5 py-1.5 text-xs text-muted-foreground">No cells match "{filter}".</li>
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
