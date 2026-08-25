import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, FileText, Plus, Trash2, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { okfApi } from '@/api/client'
import { RegisterOkfDocumentDialog } from './RegisterOkfDocumentDialog'
import type { OkfDocument } from '@/types/okf'

// The second level of the "OKF Document" drill-down -- the sibling of
// OkfBundleBrowserPanel, same shape and same double duty (pick one to place a
// node, or upload/delete one here, since documents aren't reachable anywhere
// else in the app).
//
// The one real difference is delete: a bundle's delete forgets a registration
// and leaves the user's folder alone, but an uploaded document only ever
// existed inside ASAREE's storage, so deleting it really destroys the file --
// including whatever an agent wrote into it. The confirmation says so.
export function OkfDocumentBrowserPanel({
  onPick,
  onBack,
  onClose,
}: {
  onPick: (document: OkfDocument) => void
  onBack: () => void
  onClose: () => void
}) {
  const [registerDialogOpen, setRegisterDialogOpen] = useState(false)
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const documentsQuery = useQuery({ queryKey: ['okf-documents'], queryFn: () => okfApi.listDocuments() })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => okfApi.removeDocument(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['okf-documents'] })
      setConfirmingDeleteId(null)
    },
  })

  const documents = documentsQuery.data ?? []

  return (
    <div className="flex w-80 shrink-0 flex-col gap-3 border-l p-4">
      <div className="flex items-center justify-between">
        <div className="flex min-w-0 items-center gap-1.5">
          <Button variant="ghost" size="icon-sm" aria-label="Back" onClick={onBack}>
            <ArrowLeft className="size-4" />
          </Button>
          <p className="truncate text-sm font-semibold">OKF documents</p>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
          <X className="size-4" />
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">
        A single Markdown concept you upload, stored by ASAREE. The wired agent reads and rewrites it exactly as it would a
        concept inside a bundle.
      </p>

      <Button variant="outline" size="sm" onClick={() => setRegisterDialogOpen(true)}>
        <Plus className="size-3.5" /> Upload a document
      </Button>

      {documentsQuery.isLoading ? (
        <div className="flex flex-col gap-1.5">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : documentsQuery.isError ? (
        <p className="py-4 text-center text-sm text-destructive">Could not load your documents.</p>
      ) : documents.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">No documents uploaded yet.</p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {documents.map((document) => {
            const filename = document.path?.split('/').filter(Boolean).pop() ?? null
            // Title first, filename as the fallback: `title` is required at
            // upload, so a missing one means the agent has since rewritten the
            // file into something unparseable -- still worth listing, if only
            // to delete.
            const label = document.title ?? filename ?? document.name
            const broken = document.status !== 'connected'
            return (
              <div
                key={document.id}
                className="rounded-lg border bg-background px-3 py-2.5 text-sm shadow-[0_0_16px_-6px_var(--primary)] ring-1 ring-primary/20 transition-colors hover:bg-muted"
              >
                {confirmingDeleteId === document.id ? (
                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground">
                      Delete <span className="font-mono">{label}</span>? This removes the stored file for good, including any
                      edits an agent made to it.
                    </p>
                    <div className="flex items-center gap-1.5">
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={deleteMutation.isPending}
                        onClick={() => deleteMutation.mutate(document.id)}
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
                      onClick={() => onPick(document)}
                      className="flex min-w-0 flex-1 cursor-pointer items-start gap-2.5 text-left"
                    >
                      <FileText className="mt-0.5 size-4 shrink-0 text-primary" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium">{label}</p>
                        <p className="truncate text-xs text-muted-foreground">
                          {document.description ?? filename ?? 'No description in the frontmatter.'}
                        </p>
                        <p className="truncate font-mono text-[11px] text-muted-foreground/70">
                          {[document.concept_type, ...document.tags].filter(Boolean).join(' · ') || 'no type or tags'}
                        </p>
                      </div>
                    </button>
                    <div className="flex shrink-0 items-center gap-1">
                      {broken && (
                        <Badge variant="outline" className="text-destructive" title={document.error_message ?? undefined}>
                          {document.status}
                        </Badge>
                      )}
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Delete ${label}`}
                        onClick={() => setConfirmingDeleteId(document.id)}
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

      {deleteMutation.isError && <p className="text-sm text-destructive">Could not delete that document.</p>}

      <RegisterOkfDocumentDialog open={registerDialogOpen} onOpenChange={setRegisterDialogOpen} />
    </div>
  )
}
