import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Bot } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cardAccent, hashToChartHue } from '@/lib/utils'
import { nodeRunBadge } from '@/lib/protocolRun'
import type { AgentNodeData, NodeRunStatus } from '@/types/protocols'

// All "agent" nodes share one hue in V1 -- type-based coloring (CLAUDE.md's
// hash-driven tint rule for "real category variety"), not per-instance
// variety, since a node's identity is its type, not its label.
const ACCENT = hashToChartHue('agent')

export function AgentNode({ data, selected }: NodeProps & { data: AgentNodeData & { runStatus?: NodeRunStatus } }) {
  const badge = nodeRunBadge(data.runStatus)
  return (
    <div
      style={cardAccent(ACCENT)}
      className={`relative w-36 rounded-md border bg-card px-2 py-1.5 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
        selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''
      }`}
    >
      {badge && (
        <Badge className={`absolute -top-2.5 right-1.5 ${badge.className}`}>{badge.label}</Badge>
      )}
      <Handle
        type="target"
        position={Position.Top}
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <div className="flex items-center gap-1.5">
        <Bot className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        <span className="truncate text-xs font-medium" title={data.label}>
          {data.label || 'Agent'}
        </span>
      </div>
      <p className="truncate font-mono text-[0.65rem] text-muted-foreground" title={data.config?.goal || undefined}>
        {data.config?.goal || 'No goal set'}
      </p>
      <Handle
        type="source"
        position={Position.Bottom}
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
    </div>
  )
}
