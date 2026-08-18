import type { ComponentType } from 'react'
import { Handle, Position } from '@xyflow/react'
import { cardAccent } from '@/lib/utils'
import { NodeFactorBadge } from './NodeFactorBadge'
import { NodeHoverToolbar } from './NodeHoverToolbar'
import { WarningBadge } from './WarningBadge'

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
  factorCount = 0,
  dimmed,
  isActive,
  onToggleActive,
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
  // One message, or several if this node has more than one real issue at
  // once -- see WarningBadge's own comment for how these render.
  warning?: string | string[]
  // This node's own config.enabled is false (Tool/Dataset) -- same
  // opacity-50 dimming AgentNode/CriticGateNode already apply for their own
  // active/enabled state, so "this doesn't currently do anything" reads the
  // same way across every node type that can be switched off.
  dimmed?: boolean
  // How many of this node's fields are bound to an experimental factor -- 0
  // (the default) hides the top-right NodeFactorBadge entirely, anything
  // higher is printed in its caption. Same component and same corner the
  // rectangular nodes use; see AgentNode.tsx's own call for that side.
  factorCount?: number
  // The hover toolbar's own power icon -- toggles this same config.enabled
  // `dimmed` is already reading, so a node can be switched off from the
  // canvas directly, not only via its own inspector's Enabled switch.
  // Passed straight through to NodeHoverToolbar; both or neither, same as
  // AgentNode/CriticGateNode's own usage.
  isActive?: boolean
  onToggleActive?: () => void
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
      <NodeHoverToolbar nodeId={id} isActive={isActive} onToggleActive={onToggleActive} swap={swap} />
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
          <WarningBadge
            issues={warning}
            className="absolute -right-1 -bottom-1 flex size-4 items-center justify-center rounded-full bg-card ring-1 ring-[color:var(--card-accent)]/40"
          />
        )}
        {/* Top-right, hung off the ring -- same corner the rectangular nodes
            put it on, so "this node varies" is always the same glyph in the
            same place whatever shape the node is. Pushed a little further out
            than the warning badge below it (-2 vs -1): the factor badge is
            size-7 to that one's size-4, and clearing the ring rather than
            covering the node's own icon matters more at that size. Still clear
            of the top-CENTER source handle. */}
        {factorCount > 0 && <NodeFactorBadge count={factorCount} className="-top-2 -right-2" />}
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
