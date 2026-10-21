import { useQuery } from '@tanstack/react-query'
import { personasApi } from '@/api/client'
import type { Persona } from '@/types/personas'
import type { PersonaNodeConfig } from '@/types/protocols'
import { PERSONA_LIBRARY } from './personaLibrary'

export function configForSavedPersona(persona: Persona): PersonaNodeConfig {
  return {
    mode: 'library', persona_id: persona.id, persona_ids: [persona.id],
    persona_label: persona.name, persona_description: persona.description,
    persona_text: persona.prompt, persona_file: persona.source_filename,
    persona_sources: [{ id: persona.id, revision: persona.revision, prompt_sha256: persona.prompt_sha256 }],
  }
}

export function usePersonaLibrary() {
  const query = useQuery({ queryKey: ['personas'], queryFn: personasApi.list })
  const choices = [
    ...PERSONA_LIBRARY,
    ...(query.data ?? []).map(persona => ({ id: persona.id, label: persona.name, prompt: persona.prompt })),
  ]
  function selectionConfig(ids: string[]): PersonaNodeConfig {
    const selected = ids.flatMap(id => {
      const choice = choices.find(entry => entry.id === id)
      return choice ? [choice] : []
    })
    return {
      mode: 'library', persona_id: selected[0]?.id ?? null, persona_ids: selected.map(entry => entry.id),
      persona_text: selected.map(entry => entry.prompt).filter(Boolean).join('\n\n'),
      persona_label: selected.map(entry => entry.label).join(' + ') || null,
      persona_file: null, persona_description: null,
      persona_sources: ids.flatMap(id => {
        const saved = query.data?.find(entry => entry.id === id)
        return saved ? [{ id, revision: saved.revision, prompt_sha256: saved.prompt_sha256 }] : []
      }),
    }
  }
  return { query, choices, selectionConfig }
}
