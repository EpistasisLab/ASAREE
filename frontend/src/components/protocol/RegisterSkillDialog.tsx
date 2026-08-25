import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FolderUp } from 'lucide-react'
import { ApiError, skillsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import type { Skill } from '@/types/skills'
import { FileDropInput } from './FileDropInput'

// `webkitdirectory` turns a file input into a folder picker. Not in React's
// InputHTMLAttributes (it's a non-standard attribute every browser nonetheless
// implements), so it's spread in rather than written as a prop -- same as
// RegisterOkfBundleDialog, the other folder upload in this app.
const DIRECTORY_INPUT_ATTRS = { webkitdirectory: '', directory: '' } as Record<string, string>

// Mirrors Motoro's BUNDLE_TEXT_SUFFIXES. Level 3 reaches an agent by being
// read into its context window, so a file that can't be read as text can't
// affect a run -- and a picked skill folder routinely carries scripts, images
// and a .git subtree the user never meant to hand over.
const BUNDLE_TEXT_SUFFIXES = ['.md', '.markdown', '.txt', '.json', '.yaml', '.yml', '.csv', '.toml']

function isBundleText(path: string): boolean {
  return BUNDLE_TEXT_SUFFIXES.some((suffix) => path.toLowerCase().endsWith(suffix))
}

// Split rather than filtered, so the count of what was left behind can be
// SHOWN. Silently dropping a folder's scripts/ directory would look like the
// upload took everything, and the reason they can't come (no shell behind an
// MCP tool call) is exactly what the user needs told.
function partitionBundle(list: FileList | null): { keep: File[]; skipped: number } {
  if (!list) return { keep: [], skipped: 0 }
  const visible = Array.from(list).filter((file) =>
    (file.webkitRelativePath || file.name).split('/').every((segment) => !segment.startsWith('.')),
  )
  const keep = visible.filter((file) => isBundleText(file.webkitRelativePath || file.name))
  return { keep, skipped: visible.length - keep.length }
}

function relativePathOf(file: File): string {
  const parts = (file.webkitRelativePath || file.name).split('/')
  return parts.slice(1).join('/')
}

function folderNameOf(files: File[]): string | null {
  const path = files[0]?.webkitRelativePath
  return path ? (path.split('/')[0] ?? null) : null
}

function skillMdIn(files: File[]): File | null {
  return files.find((file) => relativePathOf(file).toLowerCase() === 'skill.md') ?? null
}

// Frontmatter delimited by --- at the very top of the file, `key: value`
// lines inside it. Parsed here ONLY to preview what the server is about to
// accept -- the real parse (and every format rule: kebab-case name, <=64
// chars, non-empty description, <=1024, no XML tags, no "claude"/"anthropic"
// in a name) happens server-side in Motoro's skill_service, and a 422 from it
// is what actually surfaces below. Deliberately not a second implementation
// of the rules: one rule in one place beats two that agree until they don't.
function previewFrontmatter(text: string): { name?: string; description?: string } {
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
  return { name: fields.name, description: fields.description }
}

// Registers an Agent Skill from wherever a Skill node needs one
// (SkillNodeInspector) -- same scope call as RegisterDatasetDialog: nothing
// outside a protocol references a skill today, so a dialog at the one place it
// comes up beats a new top-level resource page.
//
// TWO shapes, because the format specifies a *directory* whose entry point is
// SKILL.md. The folder picker is the real one, and the single-file drop is the
// format's own degenerate case (a skill that bundles no level-3 resources is
// exactly one .md). Both are offered because a hand-written skill usually
// arrives as one file and a skill from the wild always arrives as a folder.
//
// What still can't come is a bundled SCRIPT -- a Motoro agent's only
// side-channel is an MCP tool call, so there is no shell to run one in. Those
// are left out of the upload and counted below rather than dropped in silence;
// a skill that needs to execute code belongs behind an MCP server node.
export function RegisterSkillDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  // Fires with the freshly-registered skill so the caller can immediately
  // select it, the same way picking an existing one already does.
  onCreated?: (skill: Skill) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [folderFiles, setFolderFiles] = useState<File[]>([])
  // Tracked separately from `folderFiles.length`: "picked a folder with no
  // readable files in it" and "hasn't picked anything" need different copy.
  const [pickedFolder, setPickedFolder] = useState(false)
  const [skipped, setSkipped] = useState(0)
  const [preview, setPreview] = useState<{ name?: string; description?: string } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  function reset() {
    setFile(null)
    setFolderFiles([])
    setPickedFolder(false)
    setSkipped(0)
    setPreview(null)
    if (inputRef.current) inputRef.current.value = ''
    createMutation.reset()
  }

  // The two pickers are mutually exclusive: whichever was used last is the
  // upload, so the other's state is cleared rather than left to make the
  // footer button ambiguous about what it is about to send.
  async function pickFile(next: File | null) {
    setFolderFiles([])
    setPickedFolder(false)
    setSkipped(0)
    setFile(next)
    setPreview(next ? previewFrontmatter(await next.text()) : null)
    createMutation.reset()
  }

  async function pickFolder(list: FileList | null) {
    const { keep, skipped: left } = partitionBundle(list)
    setFile(null)
    setPickedFolder((list?.length ?? 0) > 0)
    setFolderFiles(keep)
    setSkipped(left)
    const entry = skillMdIn(keep)
    setPreview(entry ? previewFrontmatter(await entry.text()) : null)
    createMutation.reset()
  }

  const folder = folderNameOf(folderFiles)
  const bundled = folderFiles.filter((f) => relativePathOf(f).toLowerCase() !== 'skill.md')
  const hasEntry = skillMdIn(folderFiles) !== null
  const canSubmit = file !== null || (folderFiles.length > 0 && hasEntry)

  const createMutation = useMutation({
    mutationFn: () => (file ? skillsApi.create({ file }) : skillsApi.createFromFolder(folderFiles)),
    onSuccess: (skill) => {
      queryClient.invalidateQueries({ queryKey: ['skills'] })
      onCreated?.(skill)
      reset()
      onOpenChange(false)
    },
  })

  const errorMessage = !createMutation.isError
    ? null
    : createMutation.error instanceof ApiError && typeof createMutation.error.detail === 'string'
      ? createMutation.error.detail
      : 'Could not register this skill. Please try again.'

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
          <DialogTitle>Register an Agent Skill</DialogTitle>
          <DialogDescription>
            A skill folder whose entry point is <span className="font-mono">SKILL.md</span> — YAML frontmatter with a{' '}
            <span className="font-mono">name</span> and a <span className="font-mono">description</span> (what it does and when
            to use it), then the instructions, plus any reference files it bundles.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <input
            ref={inputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => void pickFolder(event.target.files)}
            {...DIRECTORY_INPUT_ATTRS}
          />

          <div className="space-y-1.5">
            <Button variant="outline" className="w-full" onClick={() => inputRef.current?.click()}>
              <FolderUp className="size-4" /> {folder ? 'Choose a different folder' : 'Choose a skill folder'}
            </Button>
            <p className="text-xs text-muted-foreground">
              Bundled scripts aren&rsquo;t uploaded -- an agent here can only call MCP tools, so a skill that needs to run code
              belongs behind an MCP server instead.
            </p>
          </div>

          <div className="space-y-1.5">
            <p className="text-xs text-muted-foreground">Or, for a skill that bundles nothing, just its file:</p>
            <FileDropInput
              id="skill-file"
              accept=".md,.markdown,text/markdown"
              file={file}
              onChange={(next) => void pickFile(next)}
              placeholder="Drop a .md file or click to browse"
            />
          </div>

          {/* Only after a pick: what was found, in mono, so "did it take the
              folder I meant" is answerable before uploading anything. */}
          {pickedFolder && !hasEntry ? (
            <div className="rounded-lg border px-3 py-2 text-sm">
              <p className="text-xs text-muted-foreground">
                No <span className="font-mono">SKILL.md</span> at the top of that folder -- pick the skill&rsquo;s own folder,
                not the folder containing it.
              </p>
            </div>
          ) : preview ? (
            <div className="space-y-1.5 rounded-lg border px-3 py-2 text-sm">
              {preview.name ? (
                <>
                  <p className="truncate font-mono text-xs" title={preview.name}>
                    {preview.name}
                  </p>
                  <p className="text-xs text-muted-foreground">{preview.description ?? 'No description in the frontmatter.'}</p>
                </>
              ) : (
                <p className="text-xs text-muted-foreground">
                  No YAML frontmatter found at the top of this file -- registering will fail unless it has a{' '}
                  <span className="font-mono">name</span> and <span className="font-mono">description</span>.
                </p>
              )}
              {folder && (
                <p className="font-mono text-[11px] text-muted-foreground/70">
                  {folder}/ &middot; {bundled.length} bundled file{bundled.length === 1 ? '' : 's'}
                  {skipped > 0 && ` · ${skipped} skipped`}
                </p>
              )}
            </div>
          ) : null}

          {errorMessage && <p className="text-sm text-destructive">{errorMessage}</p>}

          <DialogFooter>
            <Button onClick={() => createMutation.mutate()} disabled={!canSubmit || createMutation.isPending}>
              {createMutation.isPending ? 'Registering…' : 'Register skill'}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  )
}
