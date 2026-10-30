import type { ModelCapabilities } from '@/types/llmSettings'

// Unresolved metadata exposes no sampling control; the server owns the fallback.
const UNRESOLVED_CAPABILITIES: ModelCapabilities = {
  supports_temperature: null,
  supports_effort: null,
  effort_levels: null,
  default_effort: null,
}

export function modelCapabilities(discovered?: ModelCapabilities, override?: ModelCapabilities | null) {
  const capabilities = override ?? discovered ?? UNRESOLVED_CAPABILITIES
  return {
    ...capabilities,
    effort_levels: capabilities.effort_levels ?? [],
  }
}
