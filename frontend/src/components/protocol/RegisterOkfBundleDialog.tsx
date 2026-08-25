import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FolderUp } from 'lucide-react'
import { ApiError, okfApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import type { OkfBundle } from '@/types/okf'

// `webkitdirectory` turns a file input into a folder picker. Not in React's
// InputHTMLAttributes (it's a non-standard attribute every browser
// nonetheless implements), so it's spread in rather than written as a prop --
// React passes unknown lowercase attributes straight through to the DOM.
const DIRECTORY_INPUT_ATTRS = { webkitdirectory: '', directory: '' } as Record<string, string>

// Only .md files are uploaded: the OKF server reads nothing else, and a
// picked folder can easily contain images, a .git directory, or an entire
// unrelated subtree. Hidden segments go too -- the server rejects them
// outright, and silently shipping a .git folder's contents would be worse
// than not offering to.
function conceptFilesIn(list: FileList | null): File[] {
  if (!list) return []
  return Array.from(list).filter((file) => {
    const path = file.webkitRelativePath || file.name
    const segments = path.split('/')
    return segments.every((segment) => !segment.startsWith('.')) && path.toLowerCase().endsWith('.md')
  })
}

function folderNameOf(files: File[]): string | null {
  const path = files[0]?.webkitRelativePath
  return path ? (path.split('/')[0] ?? null) : null
}

// Uploads a folder of OKF concepts from the user's own machine.
//
// A directory picker rather than the server-side folder browser this used to
// be: a browser will not tell a page where a file came from, so there is no
// path the user could hand over that the SERVER could resolve. The only thing
// that can actually cross the wire is the files themselves.
//
// The consequence is worth being blunt about, and the dialog says it: the
// agent reads and writes ASAREE's COPY. Edits it makes during a run live in
// that copy, and the folder on the user's machine never changes again after
// the upload. (An always-live, shared folder is still possible over the API
// -- POST /okf/bundles takes a path the server can already reach -- but
// that's not a question a browser can answer.)
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
  const [files, setFiles] = useState<File[]>([])
  // Tracked separately from `files.length`, which counts only the concepts:
  // "picked a folder that turned out to have no .md in it" and "hasn't picked
  // anything yet" are different states and need different copy.
  const [picked, setPicked] = useState(false)
  // The picker is a hidden input driven by a button, so the trigger can be a
  // normal glowing Button rather than a browser-styled file control.
  const inputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  function reset() {
    setFiles([])
    setPicked(false)
    if (inputRef.current) inputRef.current.value = ''
    createMutation.reset()
  }

  const createMutation = useMutation({
    mutationFn: () => okfApi.createFromUpload(files),
    onSuccess: (bundle) => {
      queryClient.invalidateQueries({ queryKey: ['okf-bundles'] })
      onCreated?.(bundle)
      reset()
      onOpenChange(false)
    },
  })

  const folder = folderNameOf(files)
  const errorMessage = !createMutation.isError
    ? null
    : createMutation.error instanceof ApiError && typeof createMutation.error.detail === 'string'
      ? createMutation.error.detail
      : 'Could not upload this folder. Please try again.'

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
          <DialogTitle>Upload an OKF bundle</DialogTitle>
          <DialogDescription>
            Pick a folder on this machine. Its <span className="font-mono">.md</span> concepts are copied to ASAREE,
            sub-folders and all, and served to the agent from there.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".md,.markdown,text/markdown"
            className="hidden"
            onChange={(event) => {
              setPicked((event.target.files?.length ?? 0) > 0)
              setFiles(conceptFilesIn(event.target.files))
              createMutation.reset()
            }}
            {...DIRECTORY_INPUT_ATTRS}
          />

          <div className="space-y-1.5">
            <Button variant="outline" className="w-full" onClick={() => inputRef.current?.click()}>
              <FolderUp className="size-4" /> {folder ? 'Choose a different folder' : 'Choose a folder'}
            </Button>
            <p className="text-xs text-muted-foreground">
              The agent reads and writes ASAREE&rsquo;s copy, so anything it writes during a run stays here -- your own
              folder isn&rsquo;t touched again after this upload.
            </p>
          </div>

          {/* Only after a pick: what was found, in mono, so "did it take the
              folder I meant" is answerable before uploading anything. */}
          {picked ? (
            <div className="space-y-1.5 rounded-lg border px-3 py-2 text-sm">
              {files.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No <span className="font-mono">.md</span> files in that folder -- an OKF bundle is a folder of Markdown
                  concepts.
                </p>
              ) : (
                <>
                  <p className="truncate font-mono text-xs" title={folder ?? ''}>
                    {folder}
                  </p>
                  <p className="font-mono text-[11px] text-muted-foreground/70">
                    {files.length} concept{files.length === 1 ? '' : 's'}
                  </p>
                </>
              )}
            </div>
          ) : null}

          {errorMessage && <p className="text-sm text-destructive">{errorMessage}</p>}

          <DialogFooter>
            <Button onClick={() => createMutation.mutate()} disabled={files.length === 0 || createMutation.isPending}>
              {createMutation.isPending ? 'Uploading…' : 'Upload folder'}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  )
}
