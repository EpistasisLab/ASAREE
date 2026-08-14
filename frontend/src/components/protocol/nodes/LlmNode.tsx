import { useState } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Atom, Cloud, Sparkles } from 'lucide-react'
import { cardAccent, hashToChartHue } from '@/lib/utils'
import type { LlmNodeData } from '@/types/protocols'
import { EditableNodeLabel } from './EditableNodeLabel'
import { NodeHoverToolbar } from './NodeHoverToolbar'
import { NodeSummaryLine } from './NodeSummaryLine'

// One shared card renderer for all three LLM provider node types
// (llm_anthropic/llm_openai/llm_azure_foundry -- see LlmNodeData's own
// comment in types/protocols.ts for why they're separate node types).
// Icon/accent/placeholder are derived from data.config.provider rather than
// the xyflow `type` prop, so this stays correct even if a node is ever
// duplicated -- hashToChartHue(provider) gives each provider its own
// distinct hue (CLAUDE.md's hash-driven tint rule: real category variety,
// not a uniform "LLM" hue for three actually-different things).
//
// A pure config source: no target handle at all -- it never receives
// input, only supplies model config to whichever agent/critic_gate node(s)
// plug into it, matching n8n's own Chat Model nodes having zero regular
// data ports. No run-status badge either: the executor gives it an
// instant, inert placeholder node_run (never a real execution), so a
// "Done"-style badge would misleadingly imply it did something. No
// "deactivate" toggle on its hover toolbar either, for the same reason --
// only Delete/Rename apply.
export const PROVIDER_META: Record<string, { label: string; icon: typeof Sparkles }> = {
  anthropic: { label: 'Anthropic', icon: Sparkles },
  openai: { label: 'OpenAI', icon: Atom },
  azure_foundry: { label: 'Azure AI Foundry', icon: Cloud },
}

export function LlmNode({ id, data, selected }: NodeProps & { data: LlmNodeData }) {
  const [renaming, setRenaming] = useState(false)
  const meta = PROVIDER_META[data.config?.provider] ?? { label: data.config?.provider || 'LLM', icon: Sparkles }
  const Icon = meta.icon
  const accent = hashToChartHue(data.config?.provider || 'llm')

  return (
    <div
      style={cardAccent(accent)}
      className={`group relative w-36 rounded-md border bg-card px-2 py-1.5 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
        selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''
      }`}
    >
      <NodeHoverToolbar nodeId={id} onRename={() => setRenaming(true)} />
      <div className="flex items-center gap-1.5">
        <Icon className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        <EditableNodeLabel nodeId={id} label={data.label} placeholder={meta.label} renaming={renaming} onRenamingChange={setRenaming} />
      </div>
      <NodeSummaryLine text={data.config?.model || null} emptyLabel="No model set" />
      <Handle
        type="source"
        id="llm"
        position={Position.Top}
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
    </div>
  )
}
