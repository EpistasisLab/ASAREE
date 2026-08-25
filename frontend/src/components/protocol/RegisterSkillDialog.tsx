import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FolderUp, Search } from 'lucide-react'
import { ApiError, skillsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import type { DiscoveredSkill, Skill, SkillUrlPreview } from '@/types/skills'
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

function messageFor(error: unknown, fallback: string): string {
  return error instanceof ApiError && typeof error.detail === 'string' ? error.detail : fallback
}

// Registers an Agent Skill from wherever a Skill node needs one
// (SkillNodeInspector) -- same scope call as RegisterDatasetDialog: nothing
// outside a protocol references a skill today, so a dialog at the one place it
// comes up beats a new top-level resource page.
//
// THREE acquisition shapes, one per tab, because the tab is the mode: the
// pickers are mutually exclusive and a tab strip says so without any state
// having to clear the others out from under the footer button.
//
// - Folder is the real one: the format specifies a *directory* whose entry
//   point is SKILL.md.
// - GitHub is how skills are actually distributed -- the `npx` installers in
//   the wild copy a repo's SKILL.md and its bundled files onto disk, and this
//   library IS that disk. Two steps (fetch, then tick) because a skills repo
//   is usually a collection of a dozen; picking several at once is the part
//   those one-skill-per-invocation installers can't do.
// - Single file is the format's own degenerate case: a skill that bundles no
//   level-3 resources is exactly one .md, which is how a hand-written one
//   arrives. Last, because it's the narrowest.
//
// What still can't come, by any of the three, is a bundled SCRIPT -- a Motoro
// agent's only side-channel is an MCP tool call, so there is no shell to run
// one in. Those are left out and counted rather than dropped in silence; a
// skill that needs to execute code belongs behind an MCP server node.
export function RegisterSkillDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  // Fires with the freshly-registered skill so the caller can immediately
  // select it, the same way picking an existing one already does. On a
  // multi-skill GitHub register it fires with the first one and the rest land
  // in the library -- a node holds one skill, so there is nothing else to do
  // with the others.
  onCreated?: (skill: Skill) => void
}) {
  const [mode, setMode] = useState('folder')
  const [file, setFile] = useState<File | null>(null)
  const [folderFiles, setFolderFiles] = useState<File[]>([])
  // Tracked separately from `folderFiles.length`: "picked a folder with no
  // readable files in it" and "hasn't picked anything" need different copy.
  const [pickedFolder, setPickedFolder] = useState(false)
  const [skipped, setSkipped] = useState(0)
  const [preview, setPreview] = useState<{ name?: string; description?: string } | null>(null)
  const [url, setUrl] = useState('')
  const [found, setFound] = useState<SkillUrlPreview | null>(null)
  const [chosen, setChosen] = useState<string[]>([])
  const inputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  function clearPicks() {
    setFile(null)
    setFolderFiles([])
    setPickedFolder(false)
    setSkipped(0)
    setPreview(null)
    setUrl('')
    setFound(null)
    setChosen([])
    if (inputRef.current) inputRef.current.value = ''
    createMutation.reset()
    fetchMutation.reset()
  }

  function reset() {
    clearPicks()
    setMode('folder')
  }

  async function pickFile(next: File | null) {
    setFile(next)
    setPreview(next ? previewFrontmatter(await next.text()) : null)
    createMutation.reset()
  }

  async function pickFolder(list: FileList | null) {
    const { keep, skipped: left } = partitionBundle(list)
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

  const fetchMutation = useMutation({
    mutationFn: () => skillsApi.previewFromUrl(url),
    onSuccess: (result) => {
      setFound(result)
      // A repo holding one skill needs no choosing -- the URL already was the
      // choice. Only a collection gets an unticked list to work through.
      setChosen(result.skills.length === 1 ? result.skills.map((s) => s.subdirectory) : [])
      createMutation.reset()
    },
  })

  // One request per skill, sequentially: a repo's tenth skill failing core's
  // format rules shouldn't cost the nine that parsed, and reporting "3 of 5"
  // needs the failures counted rather than the whole batch rolled back.
  async function registerChosen(): Promise<Skill> {
    const created: Skill[] = []
    const failures: string[] = []
    for (const subdirectory of chosen) {
      try {
        created.push(await skillsApi.createFromUrl(url, subdirectory))
      } catch (error) {
        failures.push(`${subdirectory.split('/').pop() || subdirectory}: ${messageFor(error, 'could not be registered')}`)
      }
    }
    if (created.length === 0) throw new Error(failures.join('; ') || 'No skills were registered.')
    if (failures.length > 0) throw new Error(`Registered ${created.length}, but ${failures.join('; ')}`)
    return created[0]
  }

  const createMutation = useMutation({
    mutationFn: () => {
      if (mode === 'github') return registerChosen()
      if (mode === 'file' && file) return skillsApi.create({ file })
      return skillsApi.createFromFolder(folderFiles)
    },
    onSuccess: (skill) => {
      queryClient.invalidateQueries({ queryKey: ['skills'] })
      onCreated?.(skill)
      reset()
      onOpenChange(false)
    },
    // A partial batch still stored things, so the library is stale either way.
    onError: () => queryClient.invalidateQueries({ queryKey: ['skills'] }),
  })

  const canSubmit =
    mode === 'github' ? chosen.length > 0 : mode === 'file' ? file !== null : folderFiles.length > 0 && hasEntry
  const submitLabel =
    mode === 'github' && chosen.length > 1 ? `Register ${chosen.length} skills` : 'Register skill'

  const errorMessage = !createMutation.isError
    ? null
    : createMutation.error instanceof Error && !(createMutation.error instanceof ApiError)
      ? createMutation.error.message
      : messageFor(createMutation.error, 'Could not register this skill. Please try again.')

  function toggle(subdirectory: string) {
    setChosen((current) =>
      current.includes(subdirectory) ? current.filter((s) => s !== subdirectory) : [...current, subdirectory],
    )
    createMutation.reset()
  }

  // Shared by the folder and single-file tabs -- both end at one SKILL.md, and
  // "did it take the thing I meant" is the same question either way.
  const localPreview =
    pickedFolder && !hasEntry ? (
      <div className="rounded-lg border px-3 py-2 text-sm">
        <p className="text-xs text-muted-foreground">
          No <span className="font-mono">SKILL.md</span> at the top of that folder -- pick the skill&rsquo;s own folder, not the
          folder containing it.
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
    ) : null

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

        {/* Switching tabs clears what the other one had picked. The folder and
            single-file tabs share the `preview` box, so a leftover pick would
            otherwise show a folder's SKILL.md under "Single file" -- and the
            footer button would send something the visible tab never mentioned. */}
        <Tabs
          value={mode}
          onValueChange={(next) => {
            setMode(String(next))
            clearPicks()
          }}
        >
          <TabsList className="w-full">
            <TabsTrigger value="folder">Folder</TabsTrigger>
            <TabsTrigger value="github">GitHub</TabsTrigger>
            <TabsTrigger value="file">Single file</TabsTrigger>
          </TabsList>

          <TabsContent value="folder" className="space-y-4 pt-2">
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
                Bundled scripts aren&rsquo;t uploaded -- an agent here can only call MCP tools, so a skill that needs to run
                code belongs behind an MCP server instead.
              </p>
            </div>
            {localPreview}
          </TabsContent>

          <TabsContent value="github" className="space-y-4 pt-2">
            <div className="space-y-1.5">
              <div className="flex gap-2">
                <Input
                  value={url}
                  onChange={(event) => {
                    setUrl(event.target.value)
                    setFound(null)
                    setChosen([])
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && url.trim()) fetchMutation.mutate()
                  }}
                  placeholder="github.com/owner/repo"
                  className="font-mono text-xs"
                />
                <Button
                  variant="outline"
                  onClick={() => fetchMutation.mutate()}
                  disabled={!url.trim() || fetchMutation.isPending}
                >
                  <Search className="size-4" /> {fetchMutation.isPending ? 'Fetching…' : 'Fetch'}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                A public repo, or one skill&rsquo;s own folder inside it. Nothing runs -- the repo is read once, and only its
                text files are stored.
              </p>
            </div>

            {fetchMutation.isError && (
              <p className="text-sm text-destructive">
                {messageFor(fetchMutation.error, 'Could not read that repository. Please try again.')}
              </p>
            )}

            {found && (
              <div className="space-y-2">
                <p className="font-mono text-[11px] text-muted-foreground/70">
                  
                  {found.source}
                  {found.ref && `@${found.ref}`} &middot; {found.skills.length} skill{found.skills.length === 1 ? '' : 's'}
                </p>
                <div className="max-h-56 space-y-1.5 overflow-y-auto">
                  {found.skills.map((skill: DiscoveredSkill) => (
                    // A button rather than a <label>, so the whole row is one
                    // click: a label forwarding to a Checkbox that is itself a
                    // button toggles twice.
                    <button
                      key={skill.subdirectory}
                      type="button"
                      onClick={() => toggle(skill.subdirectory)}
                      className="flex w-full cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors hover:bg-muted/50"
                    >
                      <Checkbox
                        className="pointer-events-none mt-0.5"
                        tabIndex={-1}
                        checked={chosen.includes(skill.subdirectory)}
                      />
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-mono text-xs" title={skill.subdirectory}>
                          {skill.name}
                        </p>
                        <p className="line-clamp-2 text-xs text-muted-foreground">{skill.description}</p>
                        {skill.file_count > 0 && (
                          <p className="font-mono text-[11px] text-muted-foreground/70">
                            {skill.file_count} bundled file{skill.file_count === 1 ? '' : 's'}
                          </p>
                        )}
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </TabsContent>

          <TabsContent value="file" className="space-y-4 pt-2">
            <div className="space-y-1.5">
              <FileDropInput
                id="skill-file"
                accept=".md,.markdown,text/markdown"
                file={file}
                onChange={(next) => void pickFile(next)}
                placeholder="Drop a .md file or click to browse"
              />
              <p className="text-xs text-muted-foreground">
                For a skill that bundles nothing -- just its <span className="font-mono">SKILL.md</span>.
              </p>
            </div>
            {localPreview}
          </TabsContent>
        </Tabs>

        {errorMessage && <p className="text-sm text-destructive">{errorMessage}</p>}

        <DialogFooter>
          <Button onClick={() => createMutation.mutate()} disabled={!canSubmit || createMutation.isPending}>
            {createMutation.isPending ? 'Registering…' : submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
