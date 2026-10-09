import type { ModelCapabilities } from '@/types/llmSettings'

// Unresolved metadata exposes no sampling control; the server owns the fallback.
const UNRESOLVED_CAPABILITIES: ModelCapabilities = {
  supports_temperature: false,
  supports_effort: false,
  effort_levels: [],
  default_effort: null,
}

export function modelCapabilities(discovered?: ModelCapabilities, override?: ModelCapabilities | null) {
  const capabilities = override ?? discovered ?? UNRESOLVED_CAPABILITIES
  return {
    ...capabilities,
    default_effort: capabilities.supports_effort
      ? capabilities.default_effort ?? (capabilities.effort_levels.includes('medium') ? 'medium' : capabilities.effort_levels[0])
      : null,
  }
}
