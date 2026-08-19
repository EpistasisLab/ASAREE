import { useQuery } from '@tanstack/react-query'
import { llmSettingsApi } from '@/api/client'

// The one place the provider model list is fetched. Every consumer -- the
// canvas node cards, the inspector, an llm_config factor level -- goes
// through this so they share a single cache entry rather than three
// near-identical useQuery calls that can drift apart (FactorEditorDialog was
// already missing the azure credential gate the other two had).
//
// Exported for nodeConfigIssues.ts, which reads this cache directly rather
// than subscribing: keep it the single definition of the key so a change
// here can't silently orphan that read.
//
// Note the key's SHAPE is load-bearing beyond identity: ['llm-settings'] is
// a prefix of it, so the credential mutations' existing
// invalidateQueries({queryKey: ['llm-settings']}) (CreateCredentialDialog,
// LlmCredentialsSection) already drops these lists too. Saving a key and
// immediately seeing the right models depends on that -- don't "tidy" this
// into a disjoint key like ['provider-models', provider].
export const providerModelsKey = (provider: string | undefined) => ['llm-settings', provider, 'models'] as const

// Model lists turn over on the order of weeks, but the default QueryClient
// (main.tsx) sets no staleTime, so every inspector open, canvas mount and
// window refocus refired this -- data already in cache, request still sent.
// Two LLM nodes for the same provider therefore cost two requests even
// though they render one identical list. That's also live against a real
// 10-per-60s limiter on GET /llm-settings/{provider}/models, so the old
// behaviour could 429 a canvas with a few nodes and some tab-switching.
//
// Kept deliberately shorter than the server-side cache TTL: a credential
// change busts both (via the prefix above and the server's own bust), so
// this only bounds how long a list edited *outside* this app -- a new Azure
// deployment, a model released mid-session -- stays hidden.
const MODEL_LIST_STALE_TIME_MS = 10 * 60 * 1000

export function useProviderModels(provider: string | undefined) {
  const credentialsQuery = useQuery({
    queryKey: ['llm-settings'],
    queryFn: () => llmSettingsApi.list(),
    enabled: !!provider,
    staleTime: MODEL_LIST_STALE_TIME_MS,
  })
  const hasCredential = (credentialsQuery.data ?? []).some((c) => c.provider === provider)

  // Azure Foundry alone needs a credential before there's anything to ask --
  // its list IS the resource's deployments. anthropic/openai answer from a
  // catalog, so they fetch regardless.
  const modelsQuery = useQuery({
    queryKey: providerModelsKey(provider),
    queryFn: () => llmSettingsApi.listModels(provider!),
    enabled: !!provider && (provider !== 'azure_foundry' || hasCredential),
    staleTime: MODEL_LIST_STALE_TIME_MS,
  })

  return {
    credentialsQuery,
    hasCredential,
    modelsQuery,
    models: modelsQuery.data?.models ?? [],
  }
}
