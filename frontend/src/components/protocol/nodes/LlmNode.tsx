import { useState } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Cpu } from 'lucide-react'
import { cardAccent, hashToChartHue } from '@/lib/utils'
import type { LlmNodeData } from '@/types/protocols'
import { EditableNodeLabel } from './EditableNodeLabel'
import { NodeHoverToolbar } from './NodeHoverToolbar'

// ASAREE's own name for n8n's "Chat Model" connector (see LlmNodeData's own
// comment in types/protocols.ts for why). A pure config source: no target
// handle at all -- it never receives input, only supplies model config to
// whichever agent/critic_gate node(s) plug into it, matching n8n's own Chat
// Model nodes having zero regular data ports. No run-status badge either:
// the executor gives it an instant, inert placeholder node_run (never a
// real execution), so a "Done"-style badge would misleadingly imply it did
// something. No "deactivate" toggle on its hover toolbar either, for the
// same reason -- only Delete/Rename apply.
const ACCENT = hashToChartHue('llm')

export function LlmNode({ id, data, selected }: NodeProps & { data: LlmNodeData }) {
  const [renaming, setRenaming] = useState(false)
  const summary = [data.config?.provider, data.config?.model].filter(Boolean).join(' / ') || 'Not configured'

  return (
    <div
      style={cardAccent(ACCENT)}
      className={`group relative w-36 rounded-md border bg-card px-2 py-1.5 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
        selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''
      }`}
    >
      <NodeHoverToolbar nodeId={id} onRename={() => setRenaming(true)} />
      <div className="flex items-center gap-1.5">
        <Cpu className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        <EditableNodeLabel nodeId={id} label={data.label} placeholder="LLM" renaming={renaming} onRenamingChange={setRenaming} />
      </div>
      <p className="truncate font-mono text-[0.65rem] text-muted-foreground" title={summary}>
        {summary}
      </p>
      <Handle
        type="source"
        id="llm"
        position={Position.Top}
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
    </div>
  )
}
