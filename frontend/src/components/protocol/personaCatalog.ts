import type { PersonaNodeConfig, PersonaNodeData } from '@/types/protocols'

export const PERSONA_BROWSE = 'personas_browse'

export function nodeDataForPersona(config: PersonaNodeConfig): PersonaNodeData {
  return { label: config.persona_label || 'Persona', config: { ...config, enabled: true } }
}
