import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, CornerLeftUp, Folder, FolderCheck } from 'lucide-react'
import { ApiError, okfApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import type { OkfBundle } from '@/types/okf'

// Picks a directory on the SERVER and registers it as an OKF bundle.
//
// A server-side folder browser rather than a file input, because the two are
// answering different questions: a file input uploads bytes from the browser's
// machine, and a bundle isn't a file -- it's a live directory an agent keeps
// writing to for the length of a project. The server has to hold a path to it,
// so the path has to be one the SERVER can resolve. On the install this is
// built for (a researcher running ASAREE on their own machine) those are the
// same disk and the distinction never shows; on a remote server, a bundle
// sitting on a laptop is genuinely out of reach, and browsing what the server
// can actually see is how that stays obvious instead of failing later.
//
// The browse root is a deployment setting (ASAREE_OKF_BUNDLE_ROOT) and the
// whole tree below it is what's reachable -- there is no way to type a path
// past it.
export function RegisterOkfBundleDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  // Fires with the freshly-registered bundle so the caller can immediately
  // place a node for it, the same way picking an existing one does.
  onCreated?: (bundle: OkfBundle) => void
}) {
  // Root-relative; '' is the configured root itself.
  const [path, setPath] = useState('')
  const queryClient = useQueryClient()

  const browseQuery = useQuery({
    queryKey: ['okf-browse', path],
    queryFn: () => okfApi.browse(path),
    // Keeps the previous listing on screen while the next one loads, so
    // clicking through folders doesn't flash a skeleton at every level.
    placeholderData: (prev) => prev,
    enabled: open,
  })

  const createMutation = useMutation({
    mutationFn: () => okfApi.create(path),
    onSuccess: (bundle) => {
      queryClient.invalidateQueries({ queryKey: ['okf-bundles'] })
      onCreated?.(bundle)
      setPath('')
      onOpenChange(false)
    },
  })

  const listing = browseQuery.data
  const errorMessage = !createMutation.isError
    ? null
    : createMutation.error instanceof ApiError && typeof createMutation.error.detail === 'string'
      ? createMutation.error.detail
      : 'Could not register this folder. Please try again.'

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) createMutation.reset()
      }}
    >
      <DialogContent className={HUD_ACCENT_RING_CLASSNAME}>
        <DialogHeader>
          <DialogTitle>Register an OKF bundle</DialogTitle>
          <DialogDescription>
            Pick a folder on the machine running ASAREE. The agent reads and writes Markdown concept files there, so it
            has to be a path the server can reach -- not one on this browser&rsquo;s machine.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* The absolute path, front and centre and in mono: the one thing a
              user needs to confirm here is that the server is looking where
              they think it is. */}
          <div className="rounded-lg border px-3 py-2">
            <p className="text-xs text-muted-foreground">Current folder (on the server)</p>
            <p className="truncate font-mono text-sm" title={listing?.absolute_path ?? ''}>
              {listing?.absolute_path ?? '…'}
            </p>
          </div>

          <div className="min-h-48 space-y-1.5">
            {/* Only rendered when there IS a parent -- at the root there's
                nothing above to go to, and an "up" that 422s would be worse
                than none. */}
            {listing?.parent !== null && listing !== undefined && (
              <button
                type="button"
                onClick={() => setPath(listing.parent ?? '')}
                className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg border bg-background px-3 py-2 text-left text-sm transition-colors hover:bg-muted"
              >
                <CornerLeftUp className="size-4 shrink-0 text-muted-foreground" />
                <span className="text-muted-foreground">Up one level</span>
              </button>
            )}

            {browseQuery.isLoading ? (
              <>
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </>
            ) : browseQuery.isError ? (
              <p className="py-4 text-center text-sm text-destructive">Could not read that folder.</p>
            ) : listing && listing.entries.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted-foreground">
                No sub-folders here. Register this folder itself, or go up a level.
              </p>
            ) : (
              <div className="max-h-64 space-y-1.5 overflow-auto">
                {listing?.entries.map((entry) => (
                  <button
                    key={entry.path}
                    type="button"
                    onClick={() => setPath(entry.path)}
                    className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg border bg-background px-3 py-2 text-left text-sm transition-colors hover:bg-muted"
                  >
                    {/* A folder already holding OKF's reserved index.md/log.md
                        is almost certainly the one you want -- flagged, never
                        required: an empty folder is a valid place to start a
                        new knowledge base, and refusing it would make that
                        impossible. */}
                    {entry.is_bundle ? (
                      <FolderCheck className="size-4 shrink-0 text-primary" />
                    ) : (
                      <Folder className="size-4 shrink-0 text-muted-foreground" />
                    )}
                    <span className="min-w-0 flex-1 truncate font-mono">{entry.name}</span>
                    {entry.is_bundle && <span className="shrink-0 text-xs text-primary">OKF</span>}
                    <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                  </button>
                ))}
              </div>
            )}
          </div>

          {errorMessage && <p className="text-sm text-destructive">{errorMessage}</p>}

          <DialogFooter>
            {/* Registers the folder currently being VIEWED, not a separately
                selected row -- clicking a row navigates into it, so "where you
                are" is the only selection there is. */}
            <Button onClick={() => createMutation.mutate()} disabled={!listing || createMutation.isPending}>
              {createMutation.isPending ? 'Registering…' : 'Use this folder'}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  )
}
