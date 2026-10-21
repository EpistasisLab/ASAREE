import { useState } from 'react'
import { Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { PersonaNodeConfig } from '@/types/protocols'
import { PersonaLibraryDialog } from './PersonaLibraryDialog'
import { configForSavedPersona, usePersonaLibrary } from './usePersonaLibrary'

export function PersonaLibraryPicker({ value, onChange }: {
  value: PersonaNodeConfig
  onChange: (config: PersonaNodeConfig) => void
}) {
  const { query, choices, selectionConfig } = usePersonaLibrary()
  const [adding, setAdding] = useState(false)
  const [managing, setManaging] = useState(false)
  const selected = value.persona_ids?.length ? value.persona_ids : value.persona_id ? [value.persona_id] : []
  const ids = [...selected, ...(adding || selected.length === 0 ? [''] : [])]
  const unavailable = selected.some(id => !choices.some(choice => choice.id === id))
  return (
    <div className="space-y-2">
      <Label>Choose from library</Label>
      {ids.map((id, index) => (
        <div key={index} className="flex gap-2">
          <Select value={id || null} disabled={unavailable || query.isLoading} onValueChange={nextId => {
            if (!nextId) return
            const next = [...selected]
            next[index] = nextId
            onChange({ ...value, ...selectionConfig(next) })
            setAdding(false)
          }}>
            <SelectTrigger className="min-w-0 flex-1"><SelectValue placeholder="Select a persona…">{() => choices.find(choice => choice.id === id)?.label ?? value.persona_label ?? 'Select a persona…'}</SelectValue></SelectTrigger>
            <SelectContent>
              {choices.map(choice => <SelectItem key={choice.id} value={choice.id}>{choice.label}</SelectItem>)}
            </SelectContent>
          </Select>
          {ids.length > 1 && <Button type="button" variant="ghost" size="icon-sm" aria-label={`Remove persona ${index + 1}`} onClick={() => {
            if (index === selected.length) setAdding(false)
            else onChange({ ...value, ...selectionConfig(selected.filter((_, i) => i !== index)) })
          }}><X className="size-3" /></Button>}
        </div>
      ))}
      {unavailable && !query.isLoading && <p className="text-xs text-muted-foreground">A saved entry is unavailable. The stored persona text is preserved. Use the library manager to choose a replacement.</p>}
      {query.isError && <p className="text-xs text-destructive">Could not load saved personas. Existing persona text is preserved.</p>}
      <div className="flex flex-wrap gap-2">
        {selected.length > 0 && selected.length < 5 && !adding && !unavailable && <Button type="button" variant="outline" size="sm" onClick={() => setAdding(true)}><Plus className="size-3" />Add another (max 5)</Button>}
        <Button type="button" variant="outline" size="sm" onClick={() => setManaging(true)}>Manage personas / Save current</Button>
      </div>
      {managing && <PersonaLibraryDialog open onOpenChange={setManaging} initialPrompt={value.persona_text ?? ''} onPick={persona => onChange({ ...value, ...configForSavedPersona(persona) })} />}
    </div>
  )
}
