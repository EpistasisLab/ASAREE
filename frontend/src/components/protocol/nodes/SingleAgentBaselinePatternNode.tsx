import { useState } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { ArrowRight } from 'lucide-react'
import { cardAccent, hashToChartHue } from '@/lib/utils'
import type { SingleAgentBaselinePatternNodeData } from '@/types/protocols'
import { EditableNodeLabel } from './EditableNodeLabel'
import { NodeHoverToolbar } from './NodeHoverToolbar'
import { NodeSummaryLine } from './NodeSummaryLine'

// The other Architectural Pattern connector node type (see
// SingleAgentBaselinePatternNodeData's own comment in types/protocols.ts)
// -- visual/validation scaffolding only, same pure-config-source shape as
// MemoryNode (one source handle, no run-status badge, no deactivate
// toggle). Permanently inert (see NodeSummaryLine usage below), unlike
// AgentNode's goal -- no amount of config makes this do anything yet.
const ACCENT = hashToChartHue('pattern_single_agent_baseline')

export function SingleAgentBaselinePatternNode({
  id,
  data,
  selected,
}: NodeProps & { data: SingleAgentBaselinePatternNodeData }) {
  const [renaming, setRenaming] = useState(false)

  return (
    <div
      style={cardAccent(ACCENT)}
      className={`group relative flex h-20 w-56 flex-col items-center justify-center rounded-md border border-dashed bg-card px-3 py-3 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
        selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''
      }`}
    >
      <NodeHoverToolbar nodeId={id} onRename={() => setRenaming(true)} />
      <div className="flex items-center gap-1.5">
        <ArrowRight className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        <EditableNodeLabel
          nodeId={id}
          label={data.label}
          placeholder="Single-Agent Baseline"
          renaming={renaming}
          onRenamingChange={setRenaming}
        />
      </div>
      <NodeSummaryLine text={null} emptyLabel="Not yet wired up to agentic-core's real single_agent_baseline pattern" />
      <Handle
        type="source"
        id="architectural_pattern"
        position={Position.Top}
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
    </div>
  )
}
