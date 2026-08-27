import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, History, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { experimentsApi } from '@/api/client'
import { formatRelative } from '@/lib/format'
import type { DesignRevision } from '@/types/experiments'

function DeleteRevisionDialog({
  revision,
  pending,
  onCancel,
  onConfirm,
}: {
  revision: DesignRevision
  pending: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onCancel()
      }}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete design revision {revision.revision}?</DialogTitle>
          <DialogDescription>
            Its {revision.cell_count} {revision.cell_count === 1 ? 'cell' : 'cells'}
            {revision.scored_count > 0
              ? `, including ${revision.scored_count} with recorded results,`
              : ''}{' '}
            will be permanently deleted. This can't be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={pending}>
            {pending ? 'Deleting…' : 'Delete'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Every generation of this experiment's design, newest first.
 *
 * Regenerating a design that no longer yields the same set of cells doesn't
 * edit the old cells or throw them away -- it supersedes the whole revision
 * and opens a new one (see services.design_generation). That's what keeps a
 * shrunken design from reporting the old cell count, but it also means an
 * experiment quietly accumulates results the current view doesn't show. This
 * is where those become visible and, if the user decides they're noise,
 * deletable.
 *
 * Renders nothing until there IS history (a single, current revision is just
 * "the design", already the whole rest of the tab) -- a permanent "1 revision"
 * row would be noise in a narrow panel.
 *
 * Selecting a superseded revision swaps the heatmap/table over to its cells,
 * read-only; the caller owns that state so only one copy of it exists.
 */
export function DesignHistory({
  experimentId,
  selectedRevisionId,
  onSelect,
}: {
  experimentId: string
  selectedRevisionId: string | null
  onSelect: (revisionId: string | null) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [confirming, setConfirming] = useState<DesignRevision | null>(null)
  const queryClient = useQueryClient()

  const revisionsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'design-revisions'],
    queryFn: () => experimentsApi.listDesignRevisions(experimentId),
  })

  const deleteMutation = useMutation({
    mutationFn: (revisionId: string) => experimentsApi.deleteDesignRevision(experimentId, revisionId),
    onSuccess: (_data, revisionId) => {
      // Viewing the revision that just went away leaves the table pointed at
      // nothing -- fall back to the current design before its query refetches.
      if (selectedRevisionId === revisionId) onSelect(null)
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'design-revisions'] })
      setConfirming(null)
    },
  })

  const revisions = revisionsQuery.data ?? []
  if (revisions.length < 2) return null

  return (
    <div className="rounded-md border">
      <button
        type="button"
        className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left text-xs text-muted-foreground hover:text-foreground"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? <ChevronDown className="size-3.5 shrink-0" /> : <ChevronRight className="size-3.5 shrink-0" />}
        <History className="size-3.5 shrink-0" />
        <span className="font-medium">Design history</span>
        <span className="font-mono">({revisions.length})</span>
      </button>

      {expanded && (
        <ul className="border-t">
          {revisions.map((r) => {
            const isCurrent = r.superseded_at === null
            const isSelected = isCurrent ? selectedRevisionId === null : selectedRevisionId === r.id
            return (
              <li key={r.id} className="flex items-center gap-2 border-b px-2 py-1.5 last:border-b-0 odd:bg-muted/20">
                <button
                  type="button"
                  className={`flex min-w-0 flex-1 items-baseline gap-2 text-left text-xs ${
                    isSelected ? 'text-foreground' : 'text-muted-foreground hover:text-foreground'
                  }`}
                  onClick={() => onSelect(isCurrent ? null : r.id)}
                >
                  <span className={`font-mono ${isSelected ? 'text-primary' : ''}`}>rev {r.revision}</span>
                  <span className="font-mono">
                    {r.scored_count}/{r.cell_count} scored
                  </span>
                  <span className="truncate">
                    {isCurrent ? 'current' : `superseded ${formatRelative(r.superseded_at as string)}`}
                  </span>
                </button>
                {/* The current design has no delete: emptying it out isn't a
                    deletion but a reset, and the server refuses it (409).
                    Regenerating the design is how you replace it. */}
                {!isCurrent && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Delete design revision ${r.revision}`}
                    onClick={() => setConfirming(r)}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {confirming && (
        <DeleteRevisionDialog
          revision={confirming}
          pending={deleteMutation.isPending}
          onCancel={() => setConfirming(null)}
          onConfirm={() => deleteMutation.mutate(confirming.id)}
        />
      )}
    </div>
  )
}
