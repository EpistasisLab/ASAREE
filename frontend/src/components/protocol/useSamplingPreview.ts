import { useQuery } from '@tanstack/react-query'
import { llmSettingsApi } from '@/api/client'
import type { ModelCapabilities } from '@/types/llmSettings'

export function samplingKey(capabilities: ModelCapabilities | undefined, model: string, temperature?: number | null, effort?: string | null) {
  // Model-list responses contain labels/tool flags too; send only the shared contract.
  const metadata = capabilities ? {
    supports_temperature: capabilities.supports_temperature,
    supports_effort: capabilities.supports_effort,
    default_temperature: capabilities.default_temperature ?? null,
    effort_levels: capabilities.effort_levels,
    default_effort: capabilities.default_effort ?? null,
    temperature_effort_levels: capabilities.temperature_effort_levels ?? null,
    effort_levels_complete: capabilities.effort_levels_complete ?? true,
    unsupported_effort_levels: capabilities.unsupported_effort_levels ?? null,
  } : undefined
  return ['llm-settings', 'sampling-v2', {
    capabilities: capabilities?.model_metadata ? undefined : metadata,
    model_metadata: capabilities?.model_metadata,
    model, temperature: temperature ?? null, effort: effort ?? null,
  }] as const
}

export function useSamplingPreview(capabilities: ModelCapabilities | undefined, model: string, temperature?: number | null, effort?: string | null) {
  const queryKey = samplingKey(capabilities, model, temperature, effort)
  return useQuery({
    queryKey,
    queryFn: () => llmSettingsApi.sampling(queryKey[2]),
    enabled: !!capabilities && !!model,
    staleTime: Infinity,
    retry: false,
  })
}
