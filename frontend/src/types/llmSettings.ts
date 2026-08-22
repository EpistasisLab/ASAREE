// Matches src/asaree/api/llm_settings.py exactly -- ASAREE's own schema is
// much thinner than what a provider's real settings could hold (no
// display_name/is_default/delete yet, one row per (user, provider), PUT
// upserts it). api_key is write-only: never echoed back in any response.
export type LLMProvider = 'anthropic' | 'openai' | 'azure_foundry' | 'openrouter' | 'local'

export interface LLMSetting {
  provider: LLMProvider
  api_base: string | null
  // azure_foundry only -- the *project*-scoped endpoint
  // (https://{resource}.services.ai.azure.com/api/projects/{project}), a
  // genuinely different piece of connection info than api_base's resource
  // host: the resource host authenticates inference, but listing what's
  // actually deployed is a project-scoped call that 404s against the bare
  // resource host. Optional -- inference works with just api_base; only
  // the Model dropdown's live discovery needs this too.
  azure_project_endpoint: string | null
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
  { id: 'openrouter', label: 'OpenRouter', description: 'Route requests through your own OpenRouter account' },
  {
    id: 'local',
    label: 'Local',
    description: 'A self-hosted OpenAI-compatible server (LM Studio, vLLM, llama.cpp, …)',
  },
]

export const LLM_PROVIDER_LABELS: Record<LLMProvider, string> = Object.fromEntries(
  LLM_PROVIDER_CATALOG.map((p) => [p.id, p.label]),
) as Record<LLMProvider, string>

// Matches src/asaree/api/llm_settings.py's LLMModelInfoResponse/
// LLMSettingModelsResponse -- GET /llm-settings/{provider}/models.
// supports_temperature/supports_effort/effort_levels come straight from
// Motoro's own model_capabilities registry (some newer models 400 on
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

// Matches src/asaree/api/llm_settings.py's LLMConnectionCheckResponse --
// GET /llm-settings/{provider}/connection, a zero-token liveness check
// (every provider has a free authenticated list endpoint; see
// services/llm_connection_check.py).
//
// Three states, not two, and the distinction is load-bearing: "unknown"
// covers an Azure credential with no project endpoint (so no free listing
// call exists for it at all) and a provider that answered 429.
// Neither says the credential is bad, and rendering either as a failure
// would send people rotating a key that works. `ok` also deliberately does
// NOT mean "ready to run" -- quota, billing and per-project model
// permissions are only enforced at inference time, so the UI label stays
// "Key valid".
export type LLMConnectionStatus = 'ok' | 'failed' | 'unknown'

export interface LLMConnectionCheck {
  provider: LLMProvider
  status: LLMConnectionStatus
  detail: string
  // The URL actually contacted, so a failure is debuggable without guessing
  // which base the credential resolved to. Never contains the key.
  endpoint: string | null
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
