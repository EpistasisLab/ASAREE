import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, okfApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import type { OkfDocument } from '@/types/okf'
import { FileDropInput } from './FileDropInput'

// The same shallow preview parse as RegisterSkillDialog's, against OKF's
// frontmatter fields instead of a skill's -- and for the same reason: it only
// shows what the server is about to read, while the real parse and every rule
// (frontmatter required, `title` required, UTF-8, size cap) lives server-side
// in services/okf_documents.py, whose 422 is what actually surfaces below.
function previewFrontmatter(text: string): { title?: string; description?: string; conceptType?: string } {
  const match = /^---\s*\n([\s\S]*?)\n---/.exec(text)
  if (!match) return {}
  const fields: Record<string, string> = {}
  for (const line of match[1].split('\n')) {
    const sep = line.indexOf(':')
    if (sep <= 0) continue
    fields[line.slice(0, sep).trim()] = line
      .slice(sep + 1)
      .trim()
      .replace(/^["']|["']$/g, '')
  }
  return { title: fields.title, description: fields.description, conceptType: fields.type }
}

// Uploads one OKF concept .md, from the Knowledge connector's document
// browser. Deliberately the same flow as RegisterSkillDialog -- drop a file,
// see its frontmatter, register -- because it answers the same user question
// ("here's a Markdown file from my machine"), just for knowledge rather than
// instructions.
//
// No name/description fields to override the frontmatter with, unlike the
// skill dialog: a skill's frontmatter becomes columns ASAREE owns, so editing
// them is editing a record. A concept's frontmatter stays part of the file the
// agent reads and rewrites, so an override would mean quietly editing the
// user's document on the way in.
export function RegisterOkfDocumentDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  // Fires with the stored document so the caller can place it immediately.
  onCreated?: (document: OkfDocument) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ReturnType<typeof previewFrontmatter> | null>(null)
  const queryClient = useQueryClient()

  function reset() {
    setFile(null)
    setPreview(null)
    createMutation.reset()
  }

  async function pickFile(next: File | null) {
    setFile(next)
    setPreview(next ? previewFrontmatter(await next.text()) : null)
  }

  const createMutation = useMutation({
    mutationFn: () => okfApi.createDocument(file!),
    onSuccess: (document) => {
      queryClient.invalidateQueries({ queryKey: ['okf-documents'] })
      onCreated?.(document)
      reset()
      onOpenChange(false)
    },
  })

  const errorMessage = !createMutation.isError
    ? null
    : createMutation.error instanceof ApiError && typeof createMutation.error.detail === 'string'
      ? createMutation.error.detail
      : 'Could not store this document. Please try again.'

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) reset()
      }}
    >
      <DialogContent className={HUD_ACCENT_RING_CLASSNAME}>
        <DialogHeader>
          <DialogTitle>Upload an OKF document</DialogTitle>
          <DialogDescription>
            One concept <span className="font-mono">.md</span> file: YAML frontmatter with a <span className="font-mono">title</span>{' '}
            (and optionally <span className="font-mono">type</span>, <span className="font-mono">description</span>,{' '}
            <span className="font-mono">tags</span>), then the concept itself.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <FileDropInput
              id="okf-document-file"
              accept=".md,.markdown,text/markdown"
              file={file}
              onChange={(next) => void pickFile(next)}
              placeholder="Drop a .md file or click to browse"
            />
            <p className="text-xs text-muted-foreground">
              ASAREE keeps its own copy, so later edits to your original file won't show up here -- but the agent can read
              and rewrite this copy during a run, the same way it would a bundle's concepts.
            </p>
          </div>

          {preview && (
            <div className="space-y-1.5 rounded-lg border px-3 py-2 text-sm">
              {preview.title ? (
                <>
                  <p className="truncate font-mono text-xs" title={preview.title}>
                    {preview.title}
                  </p>
                  {preview.conceptType && (
                    <p className="font-mono text-[11px] text-muted-foreground/70">type={preview.conceptType}</p>
                  )}
                  <p className="text-xs text-muted-foreground">{preview.description ?? 'No description in the frontmatter.'}</p>
                </>
              ) : (
                <p className="text-xs text-muted-foreground">
                  No <span className="font-mono">title</span> found in this file's YAML frontmatter -- uploading will fail
                  without one, since it's what names the concept.
                </p>
              )}
            </div>
          )}

          {errorMessage && <p className="text-sm text-destructive">{errorMessage}</p>}

          <DialogFooter>
            <Button onClick={() => createMutation.mutate()} disabled={!file || createMutation.isPending}>
              {createMutation.isPending ? 'Uploading…' : 'Upload document'}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  )
}
