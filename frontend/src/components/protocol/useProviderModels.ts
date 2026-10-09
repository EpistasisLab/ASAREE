import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { llmSettingsApi } from '@/api/client'
import type { ModelCapabilities } from '@/types/llmSettings'

// The one place the provider model list is fetched. Every consumer -- the
// canvas node cards, the inspector, a model_config factor level -- goes
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
export const modelCapabilitiesKey = (provider: string | undefined, model: string | undefined) =>
  ['llm-settings', provider, 'model-capabilities', model] as const

// Model lists turn over on the order of weeks, but the default QueryClient
// (main.tsx) sets no staleTime, so every inspector open, canvas mount and
// window refocus refired this -- data already in cache, request still sent.
// Two Model nodes for the same provider therefore cost two requests even
// though they render one identical list. That's also live against a real
// 10-per-60s limiter on GET /llm-settings/{provider}/models, so the old
// behaviour could 429 a canvas with a few nodes and some tab-switching.
//
// staleTime only marks data stale; it doesn't refresh a mounted menu.
// Poll at the same cadence, sharing the cache across consumers, so external
// changes eventually appear even without navigation or window refocus.
const MODEL_LIST_STALE_TIME_MS = 10 * 60 * 1000

export function useProviderModels(provider: string | undefined, model?: string, override?: ModelCapabilities | null) {
  const [settledModel, setSettledModel] = useState(model)
  useEffect(() => {
    const timer = setTimeout(() => setSettledModel(model), 300)
    return () => clearTimeout(timer)
  }, [model])
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
    refetchInterval: MODEL_LIST_STALE_TIME_MS,
  })

  const models = modelsQuery.data?.models ?? []
  const listedModel = models.find((entry) => entry.id === model)
  const listReady = modelsQuery.isFetched || (provider === 'azure_foundry' && credentialsQuery.isSuccess && !hasCredential)
  // Custom IDs must use execution's registry lookup too. Reuse the same query
  // across nodes, inspectors and factor editors; pause while the user types.
  const customCapabilitiesQuery = useQuery({
    queryKey: modelCapabilitiesKey(provider, settledModel),
    queryFn: () => llmSettingsApi.modelCapabilities(provider!, settledModel!),
    enabled: !!provider && !!model && settledModel === model && listReady && !listedModel && !override,
    staleTime: MODEL_LIST_STALE_TIME_MS,
    refetchInterval: MODEL_LIST_STALE_TIME_MS,
  })
  const capabilities = override ?? listedModel ?? (settledModel === model ? customCapabilitiesQuery.data : undefined)
  const capabilitiesPending = !!model && !capabilities
  const capabilitiesError = capabilitiesPending && settledModel === model && customCapabilitiesQuery.isError
    ? 'Could not resolve model capabilities. Try again.'
    : undefined

  return {
    credentialsQuery,
    hasCredential,
    modelsQuery,
    models,
    capabilities,
    capabilitiesPending,
    capabilitiesError,
    retryCapabilities: () => customCapabilitiesQuery.refetch(),
  }
}
