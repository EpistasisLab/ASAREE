import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { personasApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { cardAccent } from '@/lib/utils'
import type { Persona } from '@/types/personas'

export function PersonaLibraryDialog({ open, onOpenChange, initialPrompt = '', onPick }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  initialPrompt?: string
  onPick: (persona: Persona) => void
}) {
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['personas'], queryFn: personasApi.list, enabled: open })
  const [editing, setEditing] = useState<Persona | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [prompt, setPrompt] = useState(initialPrompt)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const save = useMutation({
    mutationFn: () => editing
      ? personasApi.update(editing.id, { expected_revision: editing.revision, name, description: description || null, prompt })
      : personasApi.create({ name: name || undefined, description: description || null, prompt }),
    onSuccess: (persona) => {
      client.setQueryData<Persona[]>(['personas'], previous => [
        ...(previous ?? []).filter(entry => entry.id !== persona.id), persona,
      ])
      client.invalidateQueries({ queryKey: ['personas'] })
      setEditing(persona)
      setName(persona.name)
    },
  })
  const remove = useMutation({
    mutationFn: personasApi.remove,
    onSuccess: (_data, id) => {
      client.setQueryData<Persona[]>(['personas'], previous => (previous ?? []).filter(entry => entry.id !== id))
      client.invalidateQueries({ queryKey: ['personas'] })
      setConfirmDelete(null)
      if (editing?.id === id) reset()
    },
  })
  function reset() {
    setEditing(null)
    setName('')
    setDescription('')
    setPrompt(initialPrompt)
    save.reset()
  }
  const error = save.error ?? remove.error
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader><DialogTitle>Persona library</DialogTitle></DialogHeader>
        <p className="text-sm text-muted-foreground">Save reusable personality instructions. Existing nodes and published experiments keep their text when a library entry is edited or deleted. Select a saved entry again to use its latest text.</p>
        <Input aria-label="Search saved personas" placeholder="Search personas…" value={search} onChange={event => setSearch(event.target.value)} />
        {query.isLoading ? <p>Loading personas…</p> : query.isError ? <p className="text-destructive">Could not load saved personas.</p> : (
          <div className="grid gap-2 sm:grid-cols-2">
            {(query.data ?? []).filter(persona => `${persona.name} ${persona.description ?? ''}`.toLowerCase().includes(search.toLowerCase())).map(persona => (
              <Card key={persona.id} style={cardAccent('var(--node-12)')} className="gap-2 p-3">
                <p className="truncate font-medium">{persona.name}</p>
                {persona.description && <p className="text-xs text-muted-foreground">{persona.description}</p>}
                <p className="font-mono text-xs text-muted-foreground">Revision {persona.revision}{persona.source_filename ? ` · ${persona.source_filename}` : ''}</p>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" onClick={() => { onPick(persona); onOpenChange(false) }}>Use {persona.name}</Button>
                  <Button size="sm" variant="outline" onClick={() => {
                    setEditing(persona); setName(persona.name); setDescription(persona.description ?? ''); setPrompt(persona.prompt); save.reset()
                  }}>Edit</Button>
                  <Button size="sm" variant="destructive" disabled={remove.isPending} onClick={() => {
                    if (confirmDelete === persona.id) remove.mutate(persona.id)
                    else setConfirmDelete(persona.id)
                  }}>{confirmDelete === persona.id ? 'Confirm delete' : 'Delete'}</Button>
                  {confirmDelete === persona.id && <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(null)}>Cancel</Button>}
                </div>
              </Card>
            ))}
            {query.data?.length === 0 && <p className="text-sm text-muted-foreground">No saved personas yet.</p>}
          </div>
        )}
        <div className="space-y-3 border-t pt-3">
          <div className="flex items-center justify-between"><p className="font-medium">{editing ? 'Edit saved persona' : 'New persona'}</p><Button size="sm" variant="ghost" onClick={reset}>New persona</Button></div>
          <div className="space-y-1"><Label htmlFor="saved-persona-name">Name (optional)</Label><Input id="saved-persona-name" maxLength={255} value={name} onChange={event => setName(event.target.value)} placeholder="Suggested from the text if omitted" /></div>
          <div className="space-y-1"><Label htmlFor="saved-persona-description">Description (optional)</Label><Input id="saved-persona-description" value={description} onChange={event => setDescription(event.target.value)} /></div>
          <div className="space-y-1"><Label htmlFor="saved-persona-prompt">Instructions</Label><Textarea id="saved-persona-prompt" className="min-h-32 font-mono text-xs" value={prompt} onChange={event => setPrompt(event.target.value)} /></div>
          <Button disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? 'Saving…' : editing ? 'Save library changes' : 'Save to library'}</Button>
          {save.isSuccess && <p className="text-sm text-muted-foreground">Saved. Choose “Use {save.data.name}” to apply it.</p>}
          {error && <p role="alert" className="text-sm text-destructive">{error.message}</p>}
        </div>
      </DialogContent>
    </Dialog>
  )
}
