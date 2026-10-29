import { useEffect, useId, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Plus, Upload, User, X } from 'lucide-react'
import { personasApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { nodeAccent } from '@/lib/nodeAccent'
import { cardAccent } from '@/lib/utils'
import type { PersonaNodeConfig } from '@/types/protocols'
import { PersonaLibraryDialog } from './PersonaLibraryDialog'
import { BASELINE_PERSONA_ID, PERSONA_LIBRARY } from './personaLibrary'
import { configForSavedPersona, usePersonaLibrary, type PersonaChoice } from './usePersonaLibrary'

const GROUPS: { kind: PersonaChoice['kind']; title: string }[] = [
  { kind: 'persona', title: 'Personas' },
  { kind: 'trait', title: 'Traits' },
  { kind: 'saved', title: 'Saved' },
]

// Tiles toggle a selection; one node is added only on confirm, with the
// selected prompts concatenated in click order. Baseline means "no persona",
// so it is exclusive with everything else.
export function PersonaBrowserPanel({ onPick, onBack, onClose }: {
  onPick: (config: PersonaNodeConfig) => void
  onBack: () => void
  onClose: () => void
}) {
  const [search, setSearch] = useState('')
  const [managing, setManaging] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const uploadId = useId()
  const client = useQueryClient()
  const { query, choices, selectionConfig } = usePersonaLibrary()
  useEffect(() => {
    // A library deletion must also update the pending selection and its order.
    // Wait for library data so loading/errors don't discard saved selections.
    if (!query.data) return
    const available = new Set([...PERSONA_LIBRARY, ...query.data].map(entry => entry.id))
    setSelected(current => {
      const next = current.filter(id => available.has(id))
      return next.length === current.length ? current : next
    })
  }, [query.data])
  const upload = useMutation({
    mutationFn: personasApi.upload,
    onSuccess: () => client.invalidateQueries({ queryKey: ['personas'] }),
  })
  const term = search.trim().toLowerCase()
  const filtered = choices.filter(choice => `${choice.label} ${query.data?.find(persona => persona.id === choice.id)?.description ?? ''}`.toLowerCase().includes(term))

  function toggle(id: string) {
    setSelected(current => {
      if (id === BASELINE_PERSONA_ID) return current.includes(id) ? [] : [id]
      const rest = current.filter(entry => entry !== BASELINE_PERSONA_ID)
      return rest.includes(id) ? rest.filter(entry => entry !== id) : [...rest, id]
    })
  }

  const picked = selected.flatMap(id => {
    const choice = choices.find(entry => entry.id === id)
    return choice ? [choice] : []
  })
  const previewLabel = picked.map(choice => choice.label).join(' + ')
  const previewChars = picked.reduce((sum, choice) => sum + choice.prompt.length, 0)

  function confirm() {
    if (!picked.length || picked.length !== selected.length) return
    const only = selected.length === 1 ? query.data?.find(persona => persona.id === selected[0]) : undefined
    onPick(only ? configForSavedPersona(only) : selectionConfig(selected))
  }

  return <div className="flex min-h-0 min-w-0 w-full shrink-0 flex-col gap-3 overflow-hidden border-l p-4">
    <div className="flex shrink-0 items-center justify-between">
      <div className="flex min-w-0 items-center gap-1.5"><Button variant="ghost" size="icon-sm" aria-label="Back" onClick={onBack}><ArrowLeft className="size-4" /></Button><p className="truncate text-sm font-semibold">Personas</p></div>
      <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}><X className="size-4" /></Button>
    </div>
    <Input autoFocus placeholder="Search personas…" value={search} onChange={event => setSearch(event.target.value)} />
    <Button variant="outline" size="sm" onClick={() => setManaging(true)}><Plus className="size-3.5" />Create / manage personas</Button>
    <Button variant="outline" size="sm" disabled={upload.isPending} onClick={() => document.getElementById(uploadId)?.click()}><Upload className="size-3.5" />{upload.isPending ? 'Saving persona…' : 'Upload persona (.md)'}</Button>
    <input id={uploadId} aria-label="Upload persona (.md)" type="file" accept=".md" className="hidden" onChange={event => {
      const file = event.target.files?.[0]
      if (file) upload.mutate(file)
      event.target.value = ''
    }} />
    {upload.error && <p role="alert" className="text-xs text-destructive">{upload.error.message}</p>}
    {upload.isSuccess && <p role="status" className="text-xs text-muted-foreground">Saved to the library. Select it below, then Add persona node.</p>}
    {query.isLoading && <p className="text-xs text-muted-foreground">Loading saved personas…</p>}
    {query.isError && <p role="alert" className="text-xs text-destructive">Could not load saved personas. Built-in personas are still available.</p>}
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-1">
      {GROUPS.map(group => {
        const entries = filtered.filter(choice => choice.kind === group.kind)
        if (!entries.length) return null
        return <section key={group.kind} aria-label={group.title} className="space-y-1.5">
          <h3 className="px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{group.title}</h3>
          {entries.map(choice => {
            const saved = query.data?.find(persona => persona.id === choice.id)
            const order = selected.indexOf(choice.id)
            return <Card key={choice.id} style={cardAccent(nodeAccent('persona'))} className="gap-0 p-0">
              <button type="button" aria-pressed={order >= 0} onClick={() => toggle(choice.id)} className="flex w-full cursor-pointer items-start gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm transition-colors hover:bg-muted aria-pressed:bg-muted aria-pressed:ring-1 aria-pressed:ring-[color:var(--card-accent,var(--primary))]">
                <User className="mt-0.5 size-4 shrink-0 text-[color:var(--card-accent,var(--primary))]" />
                <div className="min-w-0 flex-1"><p className="truncate font-medium">{choice.label}</p><p className="truncate text-xs text-muted-foreground" title={saved?.description ?? undefined}>{saved?.description || (choice.prompt ? 'Personality instructions' : 'Unmodified system prompt')}</p><p className="truncate font-mono text-[11px] text-muted-foreground">{choice.prompt.length.toLocaleString()} chars{saved ? ` · revision ${saved.revision}` : ''}</p></div>
                {order >= 0 && <Badge variant="secondary" className="font-mono tabular-nums" aria-label={`Selected ${order + 1}`}>{order + 1}</Badge>}
                {!saved && <Badge variant="outline">Built-in</Badge>}
              </button>
            </Card>
          })}
        </section>
      })}
      {!filtered.length && <p className="py-4 text-center text-sm text-muted-foreground">No matching personas.</p>}
    </div>
    <div className="shrink-0 space-y-2 border-t pt-3">
      <p className="truncate font-mono text-xs text-muted-foreground" title={previewLabel || undefined}>
        {selected.length ? `${selected.length} selected · ${previewChars.toLocaleString()} chars · ${previewLabel}` : 'Select one or more personas or traits to combine into a node.'}
      </p>
      <div className="flex items-center justify-end gap-2">
        <Button variant="ghost" size="sm" disabled={!selected.length} onClick={() => setSelected([])}>Clear</Button>
        <Button size="sm" disabled={!picked.length || picked.length !== selected.length} onClick={confirm}>Add persona node</Button>
      </div>
    </div>
    {managing && <PersonaLibraryDialog open onOpenChange={setManaging} onPick={persona => { toggle(persona.id); setManaging(false) }} />}
  </div>
}
