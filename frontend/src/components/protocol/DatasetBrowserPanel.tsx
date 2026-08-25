import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Database, Plus, Trash2, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { datasetsApi } from '@/api/client'
import { RegisterDatasetDialog } from './RegisterDatasetDialog'
import type { Dataset } from '@/types/datasets'

// The second level of the "Add Dataset" drill-down, and the only place a
// user's whole dataset library is visible at once -- same shape and rationale
// as SkillBrowserPanel: AddNodePanel's "Datasets" entry swaps this in, and
// picking a dataset here creates a node already bound to it
// (nodeDataForDataset).
//
// It doubles as the library's management surface (register, delete), since
// there's no Datasets page/route in this app -- registering used to be
// reachable only from inside a Dataset node's own inspector, which meant you
// had to place a blank node before you could see what you already had.
//
// Splitting deliberately isn't here, unlike registering: a split is a
// per-experiment scientific decision made against one specific dataset (see
// RegisteredDataset's own comment in the backend model), so it stays in the
// node inspector next to the dictionary summary it's read alongside. This
// panel only flags a not-yet-split dataset, so picking one is an informed
// choice.
export function DatasetBrowserPanel({
  onPick,
  onBack,
  onClose,
}: {
  onPick: (dataset: Dataset) => void
  onBack: () => void
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [registerDialogOpen, setRegisterDialogOpen] = useState(false)
  // Inline rather than a Dialog, same as SkillBrowserPanel: this panel is
  // itself a transient overlay, and stacking a modal on it reads heavier than
  // a two-click action deserves.
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: () => datasetsApi.list() })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => datasetsApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['datasets'] })
      setConfirmingDeleteId(null)
    },
  })

  const datasets = datasetsQuery.data ?? []
  const term = query.trim().toLowerCase()
  // Searches the description and target column too, not just the name -- the
  // outcome you're modelling is often how you remember which cohort a file is.
  const filtered = datasets.filter(
    (d) =>
      d.name.toLowerCase().includes(term) ||
      (d.description ?? '').toLowerCase().includes(term) ||
      (d.target_column ?? '').toLowerCase().includes(term),
  )

  return (
    <div className="flex w-80 shrink-0 flex-col gap-3 border-l p-4">
      <div className="flex items-center justify-between">
        <div className="flex min-w-0 items-center gap-1.5">
          <Button variant="ghost" size="icon-sm" aria-label="Back" onClick={onBack}>
            <ArrowLeft className="size-4" />
          </Button>
          <p className="truncate text-sm font-semibold">Datasets</p>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
          <X className="size-4" />
        </Button>
      </div>

      <Input autoFocus placeholder="Search datasets…" value={query} onChange={(e) => setQuery(e.target.value)} />
      {/* Outside every branch below, same as SkillBrowserPanel's: an empty or
          failed list is exactly when you most need the way to add one. */}
      <Button variant="outline" size="sm" onClick={() => setRegisterDialogOpen(true)}>
        <Plus className="size-3.5" /> Register new dataset
      </Button>

      {datasetsQuery.isLoading ? (
        <div className="flex flex-col gap-1.5">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : datasetsQuery.isError ? (
        <p className="py-4 text-center text-sm text-destructive">Could not load your datasets.</p>
      ) : filtered.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">
          {datasets.length === 0 ? 'No datasets registered yet.' : 'No matching datasets.'}
        </p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {filtered.map((dataset) => (
            // Same metrics as SkillBrowserPanel's rows (px-3 py-2.5, text-sm,
            // three single-line rows) so every browser reads as one list style.
            <div
              key={dataset.id}
              className="rounded-lg border bg-background px-3 py-2.5 text-sm shadow-[0_0_16px_-6px_var(--primary)] ring-1 ring-primary/20 transition-colors hover:bg-muted"
            >
              {confirmingDeleteId === dataset.id ? (
                <div className="space-y-2">
                  {/* Blunter than the skill/bundle confirmations on purpose --
                      this one deletes the uploaded file and any split derived
                      from it, not just a registration. */}
                  <p className="text-xs text-muted-foreground">
                    Delete <span className="font-mono">{dataset.name}</span>? The uploaded file and its train/test split
                    are removed for good. Any node already naming it keeps the id and will report it as missing.
                  </p>
                  <div className="flex items-center gap-1.5">
                    <Button
                      variant="destructive"
                      size="sm"
                      disabled={deleteMutation.isPending}
                      onClick={() => deleteMutation.mutate(dataset.id)}
                    >
                      {deleteMutation.isPending ? 'Deleting…' : 'Delete'}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setConfirmingDeleteId(null)}>
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start gap-2">
                  <button
                    type="button"
                    onClick={() => onPick(dataset)}
                    className="flex min-w-0 flex-1 cursor-pointer items-start gap-2.5 text-left"
                  >
                    <Database className="mt-0.5 size-4 shrink-0 text-primary" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-mono font-medium">{dataset.name}</p>
                      {/* One line, same as the other browsers' rows -- the
                          full text is a hover away here and spelled out in
                          the node's inspector. */}
                      <p
                        className="truncate text-xs text-muted-foreground"
                        title={dataset.description ?? undefined}
                      >
                        {dataset.description || 'No description'}
                      </p>
                      <p className="truncate font-mono text-[11px] text-muted-foreground/70">
                        {dataset.target_column ? `target: ${dataset.target_column}` : 'no target column'}
                        {dataset.raw_sha256 ? ` · ${dataset.raw_sha256.slice(0, 10)}…` : ''}
                      </p>
                    </div>
                  </button>
                  <div className="flex shrink-0 items-center gap-1">
                    {/* An unsplit dataset can't be opened as a workspace, so
                        it's worth flagging before you wire it up rather than
                        only once you're inside the node. */}
                    {!dataset.train_path && (
                      <Badge variant="outline" className="text-muted-foreground" title="No train/test split yet">
                        Unsplit
                      </Badge>
                    )}
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Delete ${dataset.name}`}
                      onClick={() => setConfirmingDeleteId(dataset.id)}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {deleteMutation.isError && <p className="text-sm text-destructive">Could not delete that dataset.</p>}

      {/* onCreated goes straight to onPick, unlike the skill/bundle browsers,
          which only refresh their list: you register a dataset because you're
          about to use it, and it arrives unsplit -- so landing in the new
          node's inspector is exactly where the next step (Split dataset) is. */}
      <RegisterDatasetDialog open={registerDialogOpen} onOpenChange={setRegisterDialogOpen} onCreated={onPick} />
    </div>
  )
}
