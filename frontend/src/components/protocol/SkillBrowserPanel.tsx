import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Plus, ScrollText, Trash2, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { skillsApi } from '@/api/client'
import { RegisterSkillDialog } from './RegisterSkillDialog'
import type { Skill } from '@/types/skills'

// The second level of the "Add Skill" drill-down, and the only place the
// whole skill library is visible at once: AddNodePanel's "Skill" entry swaps
// this panel in, and picking a skill here creates a node already bound to it
// (nodeDataForSkill). Same ordering rationale as McpServerBrowserPanel --
// choosing the skill is how you add the node.
//
// It doubles as the library's own management surface (register, delete),
// since a skill isn't otherwise reachable from anywhere in the app. Editing
// a skill's TEXT deliberately isn't here: that's a document-editing job, not
// something to do in a 320px canvas rail, and PUT /skills/{id}/markdown
// already exists for whenever it gets a real home.
export function SkillBrowserPanel({
  onPick,
  onBack,
  onClose,
}: {
  onPick: (skill: Skill) => void
  onBack: () => void
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [registerDialogOpen, setRegisterDialogOpen] = useState(false)
  // Which row is showing its inline "really delete?" confirmation. Inline
  // rather than a Dialog because this panel is itself a transient overlay --
  // stacking a modal on top of it to confirm a two-click action reads as
  // heavier than the action deserves.
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const skillsQuery = useQuery({ queryKey: ['skills'], queryFn: () => skillsApi.list() })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => skillsApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['skills'] })
      setConfirmingDeleteId(null)
    },
  })

  const skills = skillsQuery.data ?? []
  const term = query.trim().toLowerCase()
  // Searches the description too, not just the name -- a description says
  // WHEN a skill applies, which is usually how you remember it.
  const filtered = skills.filter(
    (s) => s.name.toLowerCase().includes(term) || s.description.toLowerCase().includes(term),
  )

  return (
    <div className="flex w-80 shrink-0 flex-col gap-3 border-l p-4">
      <div className="flex items-center justify-between">
        <div className="flex min-w-0 items-center gap-1.5">
          <Button variant="ghost" size="icon-sm" aria-label="Back" onClick={onBack}>
            <ArrowLeft className="size-4" />
          </Button>
          <p className="truncate text-sm font-semibold">Skills</p>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
          <X className="size-4" />
        </Button>
      </div>

      <Input autoFocus placeholder="Search skills…" value={query} onChange={(e) => setQuery(e.target.value)} />
      {/* Outside every branch below on purpose: this is the only place in the
          app that can register a skill, and an empty or failed list is exactly
          when you most need the way to add one. */}
      <Button variant="outline" size="sm" onClick={() => setRegisterDialogOpen(true)}>
        <Plus className="size-3.5" /> Register new skill
      </Button>

      {skillsQuery.isLoading ? (
        <div className="flex flex-col gap-1.5">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : skillsQuery.isError ? (
        <p className="py-4 text-center text-sm text-destructive">Could not load skills.</p>
      ) : filtered.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">
          {skills.length === 0 ? 'No skills registered yet.' : 'No matching skills.'}
        </p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {filtered.map((skill) => (
            // Same metrics as McpServerBrowserPanel's rows (px-3 py-2.5,
            // text-sm, three single-line rows) so the two browsers read as one
            // list style. The card is a div rather than that panel's single
            // <button> only because it also holds the delete control -- a
            // button can't nest a button -- hence hover: here instead.
            <div
              key={skill.id}
              className="rounded-lg border bg-background px-3 py-2.5 text-sm shadow-[0_0_16px_-6px_var(--primary)] ring-1 ring-primary/20 transition-colors hover:bg-muted"
            >
              {confirmingDeleteId === skill.id ? (
                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground">
                    Delete <span className="font-mono">{skill.name}</span>? Any node already naming it keeps the id and
                    will report the skill as missing.
                  </p>
                  <div className="flex items-center gap-1.5">
                    <Button
                      variant="destructive"
                      size="sm"
                      disabled={deleteMutation.isPending}
                      onClick={() => deleteMutation.mutate(skill.id)}
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
                    onClick={() => onPick(skill)}
                    className="flex min-w-0 flex-1 cursor-pointer items-start gap-2.5 text-left"
                  >
                    <ScrollText className="mt-0.5 size-4 shrink-0 text-primary" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-mono font-medium">{skill.name}</p>
                      {/* Truncated to one line, same as McpServerBrowserPanel's
                          own rows: a description is up to 1024 chars and is
                          written as "what it does AND when to use it," so left
                          to wrap it turns every row into a paragraph. The full
                          text is one click away in the node's inspector, and
                          `title` gives it on hover here. */}
                      <p className="truncate text-xs text-muted-foreground" title={skill.description}>
                        {skill.description}
                      </p>
                      <p className="truncate font-mono text-[11px] text-muted-foreground/70">
                        {skill.body.length.toLocaleString()} chars
                        {skill.source_filename ? ` · ${skill.source_filename}` : ''}
                      </p>
                    </div>
                  </button>
                  {/* A built-in belongs to the deployment, not to this user --
                      the API refuses a non-owner mutation anyway, so the
                      control isn't offered rather than offered and denied. */}
                  {skill.is_system ? (
                    <Badge variant="outline" className="shrink-0">
                      Built-in
                    </Badge>
                  ) : (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="shrink-0"
                      aria-label={`Delete ${skill.name}`}
                      onClick={() => setConfirmingDeleteId(skill.id)}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {deleteMutation.isError && <p className="text-sm text-destructive">Could not delete that skill.</p>}

      {/* Registering just refreshes the list rather than immediately creating
          a node: uploading and placing are separate decisions, and you may
          well be stocking the library before wiring anything up. */}
      <RegisterSkillDialog open={registerDialogOpen} onOpenChange={setRegisterDialogOpen} />
    </div>
  )
}
