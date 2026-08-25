import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, BookMarked, FolderUp, Trash2, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { okfApi } from '@/api/client'
import { RegisterOkfBundleDialog } from './RegisterOkfBundleDialog'
import type { OkfBundle } from '@/types/okf'

// The second level of the "Add OKF Bundle" drill-down, and the only place a
// user's registered bundles are visible at once -- same shape and rationale as
// SkillBrowserPanel: AddNodePanel's "OKF Bundle" entry swaps this in, and
// picking a bundle here creates a node already bound to it (nodeDataForBundle).
//
// It doubles as the management surface (upload, delete), since a bundle isn't
// reachable from anywhere else in the app. What deleting DOES depends on
// bundle.uploaded, and the confirmation has to say which one it is: an
// uploaded bundle is ASAREE's own copy, so removing it destroys those files
// (and anything the agent wrote into them); a bundle registered over the API
// by path is the user's own folder, so removing it only forgets the
// registration.
//
// No search box, unlike the skill and MCP-server browsers: a user has a handful
// of bundles, not a library of them, and filtering three rows is noise.
export function OkfBundleBrowserPanel({
  onPick,
  onBack,
  onClose,
}: {
  onPick: (bundle: OkfBundle) => void
  onBack: () => void
  onClose: () => void
}) {
  const [registerDialogOpen, setRegisterDialogOpen] = useState(false)
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const bundlesQuery = useQuery({ queryKey: ['okf-bundles'], queryFn: () => okfApi.list() })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => okfApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['okf-bundles'] })
      setConfirmingDeleteId(null)
    },
  })

  const bundles = bundlesQuery.data ?? []

  return (
    <div className="flex w-80 shrink-0 flex-col gap-3 border-l p-4">
      <div className="flex items-center justify-between">
        <div className="flex min-w-0 items-center gap-1.5">
          <Button variant="ghost" size="icon-sm" aria-label="Back" onClick={onBack}>
            <ArrowLeft className="size-4" />
          </Button>
          <p className="truncate text-sm font-semibold">OKF bundles</p>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
          <X className="size-4" />
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">
        A folder of Markdown concepts, uploaded from your machine, which the wired agent reads and writes as it works.
      </p>

      {/* Outside every branch below, same as SkillBrowserPanel's: an empty or
          failed list is exactly when you most need the way to add one. */}
      <Button variant="outline" size="sm" onClick={() => setRegisterDialogOpen(true)}>
        <FolderUp className="size-3.5" /> Upload a folder
      </Button>

      {bundlesQuery.isLoading ? (
        <div className="flex flex-col gap-1.5">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : bundlesQuery.isError ? (
        <p className="py-4 text-center text-sm text-destructive">Could not load your bundles.</p>
      ) : bundles.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">No bundles registered yet.</p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {bundles.map((bundle) => {
            const folder = bundle.path?.split('/').filter(Boolean).pop() ?? bundle.name
            const broken = bundle.status !== 'connected'
            return (
              // Same metrics as SkillBrowserPanel's rows (px-3 py-2.5, text-sm,
              // three single-line rows) so every browser reads as one list style.
              <div
                key={bundle.id}
                className="rounded-lg border bg-background px-3 py-2.5 text-sm shadow-[0_0_16px_-6px_var(--primary)] ring-1 ring-primary/20 transition-colors hover:bg-muted"
              >
                {confirmingDeleteId === bundle.id ? (
                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground">
                      {bundle.uploaded ? (
                        <>
                          Delete <span className="font-mono">{folder}</span>? ASAREE&rsquo;s copy of these concepts, and
                          any edits the agent made to them, are removed for good. The folder you uploaded from is
                          untouched.
                        </>
                      ) : (
                        <>
                          Stop using <span className="font-mono">{folder}</span>? The folder and its files stay on disk
                          -- only the registration is removed.
                        </>
                      )}
                    </p>
                    <div className="flex items-center gap-1.5">
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={deleteMutation.isPending}
                        onClick={() => deleteMutation.mutate(bundle.id)}
                      >
                        {deleteMutation.isPending ? 'Removing…' : bundle.uploaded ? 'Delete' : 'Remove'}
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
                      onClick={() => onPick(bundle)}
                      className="flex min-w-0 flex-1 cursor-pointer items-start gap-2.5 text-left"
                    >
                      <BookMarked className="mt-0.5 size-4 shrink-0 text-primary" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-mono font-medium">{folder}</p>
                        {/* The full server-side path, truncated from the LEFT
                            (direction:rtl) -- the tail is the part that
                            identifies the folder, and a left-truncated
                            "…/projects/spine/okf" is far more use than
                            "/home/researcher/wor…". */}
                        <p
                          dir="rtl"
                          className="truncate text-left font-mono text-xs text-muted-foreground"
                          title={bundle.path ?? ''}
                        >
                          {bundle.path ?? '(path unknown)'}
                        </p>
                        <p className="truncate font-mono text-[11px] text-muted-foreground/70">
                          {bundle.tool_names.length} tool{bundle.tool_names.length === 1 ? '' : 's'}
                        </p>
                      </div>
                    </button>
                    <div className="flex shrink-0 items-center gap-1">
                      {/* A bundle whose server didn't start contributes no
                          tools at all, so the state is worth flagging in the
                          list rather than only inside the node. */}
                      {broken && (
                        <Badge variant="outline" className="text-destructive" title={bundle.error_message ?? undefined}>
                          {bundle.status}
                        </Badge>
                      )}
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Remove ${folder}`}
                        onClick={() => setConfirmingDeleteId(bundle.id)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {deleteMutation.isError && <p className="text-sm text-destructive">Could not remove that bundle.</p>}

      {/* Registering refreshes the list rather than immediately creating a
          node, matching RegisterSkillDialog's use here: registering and placing
          are separate decisions. */}
      <RegisterOkfBundleDialog open={registerDialogOpen} onOpenChange={setRegisterDialogOpen} />
    </div>
  )
}
