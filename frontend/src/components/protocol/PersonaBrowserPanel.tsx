import { useId, useState } from 'react'
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
import { configForSavedPersona, usePersonaLibrary } from './usePersonaLibrary'

export function PersonaBrowserPanel({ onPick, onBack, onClose }: {
  onPick: (config: PersonaNodeConfig) => void
  onBack: () => void
  onClose: () => void
}) {
  const [search, setSearch] = useState('')
  const [managing, setManaging] = useState(false)
  const uploadId = useId()
  const client = useQueryClient()
  const { query, choices, selectionConfig } = usePersonaLibrary()
  const upload = useMutation({
    mutationFn: personasApi.upload,
    onSuccess: () => client.invalidateQueries({ queryKey: ['personas'] }),
  })
  const term = search.trim().toLowerCase()
  const filtered = choices.filter(choice => `${choice.label} ${query.data?.find(persona => persona.id === choice.id)?.description ?? ''}`.toLowerCase().includes(term))
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
    {upload.isSuccess && <p role="status" className="text-xs text-muted-foreground">Saved to the library. Select a persona below to add its node.</p>}
    {query.isLoading && <p className="text-xs text-muted-foreground">Loading saved personas…</p>}
    {query.isError && <p role="alert" className="text-xs text-destructive">Could not load saved personas. Built-in personas are still available.</p>}
    <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-1">
      {filtered.map(choice => {
        const saved = query.data?.find(persona => persona.id === choice.id)
        return <Card key={choice.id} style={cardAccent(nodeAccent('persona'))} className="gap-0 p-0">
          <button type="button" onClick={() => onPick(saved ? configForSavedPersona(saved) : selectionConfig([choice.id]))} className="flex w-full cursor-pointer items-start gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm transition-colors hover:bg-muted">
            <User className="mt-0.5 size-4 shrink-0 text-[color:var(--card-accent,var(--primary))]" />
            <div className="min-w-0 flex-1"><p className="truncate font-medium">{choice.label}</p><p className="truncate text-xs text-muted-foreground" title={saved?.description ?? undefined}>{saved?.description || (choice.prompt ? 'Personality instructions' : 'Unmodified system prompt')}</p><p className="truncate font-mono text-[11px] text-muted-foreground">{choice.prompt.length.toLocaleString()} chars{saved ? ` · revision ${saved.revision}` : ''}</p></div>
            {!saved && <Badge variant="outline">Built-in</Badge>}
          </button>
        </Card>
      })}
      {!filtered.length && <p className="py-4 text-center text-sm text-muted-foreground">No matching personas.</p>}
    </div>
    {managing && <PersonaLibraryDialog open onOpenChange={setManaging} onPick={persona => onPick(configForSavedPersona(persona))} />}
  </div>
}
