import { useQuery } from '@tanstack/react-query'
import type { NodeProps } from '@xyflow/react'
import { Atom, Cloud, Sparkles } from 'lucide-react'
import { llmSettingsApi } from '@/api/client'
import { hashToChartHue } from '@/lib/utils'
import type { LlmNodeData } from '@/types/protocols'
import { hasBoundFactor } from '../bindableFields'
import { CircleNode } from './CircleNode'

// One shared card renderer for all three LLM provider node types
// (llm_anthropic/llm_openai/llm_azure_foundry -- see LlmNodeData's own
// comment in types/protocols.ts for why they're separate node types).
// Icon/accent/placeholder are derived from data.config.provider rather than
// the xyflow `type` prop, so this stays correct even if a node is ever
// duplicated -- hashToChartHue(provider) gives each provider its own
// distinct hue (CLAUDE.md's hash-driven tint rule: real category variety,
// not a uniform "LLM" hue for three actually-different things).
//
// Rendered as n8n's own small circle-with-icon (see CircleNode) -- a pure
// config source, no target handle at all, model/temperature/etc. only show
// in the Inspector (n8n's own Chat Model nodes don't surface their model on
// canvas either). No run-status badge or "deactivate": the executor gives
// it an instant, inert placeholder node_run (never a real execution).
export const PROVIDER_META: Record<string, { label: string; icon: typeof Sparkles }> = {
  anthropic: { label: 'Anthropic', icon: Sparkles },
  openai: { label: 'OpenAI', icon: Atom },
  azure_foundry: { label: 'Azure AI Foundry', icon: Cloud },
}

export function LlmNode({ id, data, selected }: NodeProps & { data: LlmNodeData }) {
  const meta = PROVIDER_META[data.config?.provider] ?? { label: data.config?.provider || 'LLM', icon: Sparkles }
  const accent = hashToChartHue(data.config?.provider || 'llm')
  const provider = data.config?.provider

  // Same two queries LlmNodeInspector.tsx already runs (identical queryKeys,
  // so this shares its cache rather than duplicating requests when both are
  // mounted) -- "model is set" alone can't catch a stale/invalid model id
  // (every default LLM node config ships with a real-looking model string,
  // so that check can basically never fire in practice); this instead
  // validates against the provider's own actually-discovered model list.
  const credentialsQuery = useQuery({
    queryKey: ['llm-settings'],
    queryFn: () => llmSettingsApi.list(),
    enabled: !!provider,
  })
  const hasCredential = (credentialsQuery.data ?? []).some((c) => c.provider === provider)
  const modelsQuery = useQuery({
    queryKey: ['llm-settings', provider, 'models'],
    queryFn: () => llmSettingsApi.listModels(provider!),
    enabled: !!provider && (provider !== 'azure_foundry' || hasCredential),
  })
  const models = modelsQuery.data?.models ?? []
  // An empty list (still loading, discovery failed, or Azure with no
  // credential yet) means "can't tell," not "invalid" -- only warn once
  // there's an actual list to check against, same as the Inspector's own
  // "unrecognized model" fallback treats this case.
  const modelName = data.config?.model
  const warning = !modelName
    ? 'No model set'
    : models.length > 0 && !models.some((m) => m.id === modelName)
      ? `"${modelName}" isn't a known model for this provider`
      : undefined

  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={accent}
      icon={meta.icon}
      label={data.label}
      placeholder={meta.label}
      handleId="llm"
      warning={warning}
      hasFactor={hasBoundFactor(data)}
    />
  )
}
