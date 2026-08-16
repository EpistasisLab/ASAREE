import type { NodeProps } from '@xyflow/react'
import { Atom, Cloud, Sparkles } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { LlmNodeData } from '@/types/protocols'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'
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
  const { requestMakeFactor } = useProtocolCanvasActions()

  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={accent}
      icon={meta.icon}
      label={data.label}
      placeholder={meta.label}
      handleId="llm"
      warning={data.config?.model ? undefined : 'No model set'}
      onMakeFactor={() => requestMakeFactor(id)}
    />
  )
}
