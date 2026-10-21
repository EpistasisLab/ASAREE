export interface Persona {
  id: string
  name: string
  description: string | null
  prompt: string
  source_filename: string | null
  traits: Record<string, unknown>
  revision: number
  prompt_sha256: string
  created_at: string
  updated_at: string
}

export interface PersonaSource {
  id: string
  revision: number
  prompt_sha256: string
}
