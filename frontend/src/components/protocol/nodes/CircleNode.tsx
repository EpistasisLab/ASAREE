import type { ComponentType } from 'react'
import { Handle, Position } from '@xyflow/react'
import { TriangleAlert, Variable } from 'lucide-react'
import { cardAccent } from '@/lib/utils'
import { NodeHoverToolbar } from './NodeHoverToolbar'

// n8n's own rendering for an AI sub-connector's source node (Chat Model/
// Memory/Tool): a small circle with just the icon inside, no label -- the
// label sits below the circle instead. Shared by every node whose only job
// is to feed a connector slot (LLM/Memory/Architectural Pattern, and
// McpToolNode when it's currently playing its Tool-connector role -- see
// that file) rather than n8n's own "rounded rectangle, icon+label both
// inside" for a real pipeline step. One source handle, top-center, feeding
// up into whichever agent/critic_gate row it's wired into.
export function CircleNode({
  id,
  selected,
  accent,
  icon: Icon,
  label,
  placeholder,
  handleId,
  dashed,
  warning,
  hasFactor,
  dimmed,
  swap,
  handlePosition = 'top',
}: {
  id: string
  selected?: boolean
  accent: string
  icon: ComponentType<{ className?: string }>
  label: string
  placeholder: string
  handleId: string
  // Dashed ring signals "not yet functional" scaffolding (Memory) --
  // distinct from `warning`, which flags a genuinely missing required field
  // (e.g. McpToolNode-as-Tool with no server/tool picked yet) rather than a
  // permanent non-implementation.
  dashed?: boolean
  warning?: string
  // This node's own config.enabled is false (Tool/Dataset) -- same
  // opacity-50 dimming AgentNode/CriticGateNode already apply for their own
  // active/enabled state, so "this doesn't currently do anything" reads the
  // same way across every node type that can be switched off.
  dimmed?: boolean
  // Small "x" badge, opposite corner from `warning` -- this node has at
  // least one field bound to an experimental factor (see AgentNode.tsx's
  // own hasFactor for the matching convention on a rectangular node).
  hasFactor?: boolean
  // Passed straight through to NodeHoverToolbar -- see its own comment.
  swap?: { label: string; onSwap: () => void }
  // Which edge of the circle the connector handle sits on -- 'top' (default)
  // for every source node positioned BELOW its target agent (LLM/Memory/
  // Tool/Dataset/Script), so the edge exits upward into the agent's own
  // bottom row. Architectural Pattern nodes are positioned ABOVE their
  // agent instead (see agentDefaultPattern in ProtocolCanvas.tsx) and use
  // 'bottom' here, so the edge runs straight down into the agent's own top
  // connector rather than looping around.
  handlePosition?: 'top' | 'bottom'
}) {
  return (
    <div className={`group relative flex flex-col items-center ${dimmed ? 'opacity-50' : ''}`}>
      <NodeHoverToolbar nodeId={id} swap={swap} />
      <div className="relative">
        <div
          style={cardAccent(accent)}
          className={`flex size-14 items-center justify-center rounded-full border-2 bg-card shadow-[0_0_12px_-4px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 border-[color:var(--card-accent)] ${
            dashed ? 'border-dashed' : ''
          } ${selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''}`}
        >
          <Icon className="size-6 text-[color:var(--card-accent)]" />
        </div>
        {warning && (
          <div
            className="absolute -right-1 -bottom-1 flex size-4 items-center justify-center rounded-full bg-card ring-1 ring-[color:var(--card-accent)]/40"
            title={warning}
          >
            <TriangleAlert className="size-3 text-[color:var(--chart-4)]" />
          </div>
        )}
        {hasFactor && (
          <div
            className="absolute -bottom-1 -left-1 flex size-4 items-center justify-center rounded-full bg-card ring-1 ring-[color:var(--card-accent)]/40"
            title="One or more fields are bound to an experimental factor"
          >
            <Variable className="size-3 text-[color:var(--chart-2)]" />
          </div>
        )}
        <Handle
          type="source"
          id={handleId}
          position={handlePosition === 'bottom' ? Position.Bottom : Position.Top}
          className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
        />
      </div>
      <span className="mt-1.5 max-w-24 truncate text-center text-xs font-medium" title={label}>
        {label || placeholder}
      </span>
    </div>
  )
}
