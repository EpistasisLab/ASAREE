import { useState } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { BrainCircuit } from 'lucide-react'
import { cardAccent, hashToChartHue } from '@/lib/utils'
import type { MemoryNodeData } from '@/types/protocols'
import { EditableNodeLabel } from './EditableNodeLabel'
import { NodeHoverToolbar } from './NodeHoverToolbar'

// Visual/validation scaffolding only -- see MemoryNodeData's own comment in
// types/protocols.ts. Same pure-config-source shape as LlmNode (one source
// handle, no run-status badge, no deactivate toggle).
const ACCENT = hashToChartHue('memory')

export function MemoryNode({ id, data, selected }: NodeProps & { data: MemoryNodeData }) {
  const [renaming, setRenaming] = useState(false)

  return (
    <div
      style={cardAccent(ACCENT)}
      className={`group relative w-36 rounded-md border border-dashed bg-card px-2 py-1.5 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
        selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''
      }`}
    >
      <NodeHoverToolbar nodeId={id} onRename={() => setRenaming(true)} />
      <div className="flex items-center gap-1.5">
        <BrainCircuit className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        <EditableNodeLabel nodeId={id} label={data.label} placeholder="Memory" renaming={renaming} onRenamingChange={setRenaming} />
      </div>
      <p className="truncate font-mono text-[0.65rem] text-muted-foreground" title="Not yet wired up to real memory storage">
        Not yet functional
      </p>
      <Handle
        type="source"
        id="memory"
        position={Position.Top}
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
    </div>
  )
}
