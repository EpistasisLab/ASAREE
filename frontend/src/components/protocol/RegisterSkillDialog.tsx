import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, skillsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import type { Skill } from '@/types/skills'
import { FileDropInput } from './FileDropInput'

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

// Registers an Agent Skill from its .md file, from wherever a Skill node
// needs one (SkillNodeInspector) -- same scope call as RegisterDatasetDialog:
// nothing outside a protocol references a skill today, so a dialog at the one
// place it comes up beats a new top-level resource page.
//
// One FILE, not a folder, and that isn't a simplification of the format: the
// spec's directory exists to bundle OPTIONAL scripts/templates/reference docs
// next to the SKILL.md, so a skill with none of those is exactly one .md
// file. The genuinely unsupported part is a bundled script -- a Motoro
// agent's only side-channel is an MCP tool call, so there's no shell to run
// one in; a skill that needs to execute code belongs behind an MCP server
// (which this canvas already has a node type for).
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
  const [preview, setPreview] = useState<{ name?: string; description?: string } | null>(null)
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
    mutationFn: () => skillsApi.create({ file: file! }),
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
            One <span className="font-mono">SKILL.md</span> file: YAML frontmatter with a <span className="font-mono">name</span>{' '}
            and a <span className="font-mono">description</span> (what it does and when to use it), then the instructions.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <FileDropInput
              id="skill-file"
              accept=".md,.markdown,text/markdown"
              file={file}
              onChange={(next) => void pickFile(next)}
              placeholder="Drop a .md file or click to browse"
            />
            <p className="text-xs text-muted-foreground">
              Bundled scripts aren't supported -- an agent here can only call MCP tools, so a skill that needs to run code
              belongs behind an MCP server instead.
            </p>
          </div>

          {preview && (
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
            </div>
          )}

          {errorMessage && <p className="text-sm text-destructive">{errorMessage}</p>}

          <DialogFooter>
            <Button onClick={() => createMutation.mutate()} disabled={!file || createMutation.isPending}>
              {createMutation.isPending ? 'Registering…' : 'Register skill'}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  )
}
