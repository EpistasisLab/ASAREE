// Matches src/asaree/api/llm_settings.py exactly -- ASAREE's own schema is
// much thinner than what a provider's real settings could hold (no
// display_name/is_default/delete yet, one row per (user, provider), PUT
// upserts it). api_key is write-only: never echoed back in any response.
export type LLMProvider = 'anthropic' | 'openai' | 'azure_foundry'

export interface LLMSetting {
  provider: LLMProvider
  api_base: string | null
}

// Matches ASAREE's backend SUPPORTED_PROVIDERS (credential_resolver.py) --
// every provider a per-user credential can be resolved for. The single
// shared source for provider display info -- CreateCredentialDialog's
// provider picker and the LLM node inspector's Credential section both read
// this instead of keeping their own copy.
export const LLM_PROVIDER_CATALOG: { id: LLMProvider; label: string; description: string }[] = [
  { id: 'anthropic', label: 'Anthropic', description: 'Route requests through your own Anthropic account' },
  { id: 'openai', label: 'OpenAI', description: 'Route requests through your own OpenAI account' },
  { id: 'azure_foundry', label: 'Azure AI Foundry', description: 'Route models through your own Azure resource' },
]

export const LLM_PROVIDER_LABELS: Record<LLMProvider, string> = Object.fromEntries(
  LLM_PROVIDER_CATALOG.map((p) => [p.id, p.label]),
) as Record<LLMProvider, string>

// Matches src/asaree/api/llm_settings.py's LLMModelInfoResponse/
// LLMSettingModelsResponse -- GET /llm-settings/{provider}/models.
// supports_temperature/supports_effort/effort_levels come straight from
// agentic-core's own model_capabilities registry (some newer models 400 on
// an explicit temperature and take an `effort` dial instead), so the same
// response tells the Inspector both which models to list AND which control
// to show for whichever one is selected.
export interface LLMModelInfo {
  id: string
  label: string | null
  supports_temperature: boolean
  supports_effort: boolean
  effort_levels: string[]
}

export interface LLMSettingModelsResponse {
  models: LLMModelInfo[]
  // "static" -- Anthropic/OpenAI's curated, provider-wide catalog, no
  // credential needed. "api" -- a live Azure Foundry deployment discovery
  // that succeeded. "error" -- discovery couldn't run at all (see `note`);
  // the Inspector falls back to a free-text Model field in this case.
  source: 'static' | 'api' | 'error'
  note: string | null
}
