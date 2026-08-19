import { useQuery } from '@tanstack/react-query'
import type { NodeProps } from '@xyflow/react'
import { Atom, Cloud, Sparkles } from 'lucide-react'
import { llmSettingsApi } from '@/api/client'
import { hashToChartHue } from '@/lib/utils'
import type { LlmNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
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
// Rendered as a small circle-with-icon (see CircleNode) -- a pure
// config source, no target handle at all, model/temperature/etc. only show
// in the Inspector rather than on the canvas.
// No run-status badge or "deactivate": the executor gives
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
  //
  // And only when that list is *authoritative*. `source: 'api'` is a live
  // Azure Foundry deployment listing, so an id missing from it genuinely
  // can't be called. `source: 'static'` is the curated anthropic/openai
  // catalog, which is knowingly incomplete -- it can only ever name models
  // that existed when the pinned agentic-core version was tagged -- and the
  // inspector now offers "Custom model..." specifically so you can go past
  // it. Flagging that deliberate choice as a problem would make the escape
  // hatch look broken. The cost is that a typo'd anthropic/openai model is
  // no longer caught here; it surfaces as a provider error on the first real
  // call instead.
  const modelName = data.config?.model
  const listIsAuthoritative = modelsQuery.data?.source === 'api'
  const warning = !modelName
    ? 'No model set'
    : listIsAuthoritative && models.length > 0 && !models.some((m) => m.id === modelName)
      ? `"${modelName}" isn't a deployment on this Azure Foundry project`
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
      factorCount={boundFactorCount(data)}
    />
  )
}
