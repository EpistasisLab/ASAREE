import { Handle, Position, useReactFlow, type NodeProps } from '@xyflow/react'
import { ShieldCheck, ShieldOff } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cardAccent, hashToChartHue } from '@/lib/utils'
import { nodeRunBadge } from '@/lib/protocolRun'
import type { CriticGateNodeData, NodeRunStatus } from '@/types/protocols'
import { hasBoundFactor } from '../bindableFields'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'
import { ConnectorAddStub } from './ConnectorAddStub'
import { ConnectorHandleLabel } from './ConnectorHandleLabel'
import { NodeFactorBadge } from './NodeFactorBadge'
import { NodeHoverToolbar } from './NodeHoverToolbar'

// A third hue, distinct from "agent"/"mcp_tool" -- real category variety
// (CLAUDE.md's hash-driven tint rule), all critic_gate nodes share this one.
const ACCENT = hashToChartHue('critic_gate')

export function CriticGateNode({ id, data, selected }: NodeProps & { data: CriticGateNodeData & { runStatus?: NodeRunStatus } }) {
  const enabled = data.config?.enabled ?? true
  const Icon = enabled ? ShieldCheck : ShieldOff
  const summary = enabled ? `Up to ${data.config?.max_revisions ?? 1} revision(s)` : 'Gate disabled'
  const badge = nodeRunBadge(data.runStatus)
  const { updateNodeData } = useReactFlow()
  const { requestMakeFactor } = useProtocolCanvasActions()

  return (
    <div
      style={cardAccent(ACCENT)}
      className={`group relative w-36 rounded-md border bg-card px-2 py-1.5 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
        selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''
      } ${enabled ? '' : 'opacity-50'}`}
    >
      <NodeHoverToolbar
        nodeId={id}
        // No separate `active` flag for critic gates -- the power icon
        // toggles this node's existing config.enabled directly (see
        // CriticGateNodeConfig's own comment on this field).
        isActive={enabled}
        onToggleActive={() => updateNodeData(id, { config: { ...data.config, enabled: !enabled } })}
        onMakeFactor={() => requestMakeFactor(id)}
      />
      {badge && (
        <Badge className={`absolute -top-2.5 right-1.5 ${badge.className}`}>{badge.label}</Badge>
      )}
      {hasBoundFactor(data) && <NodeFactorBadge className="top-1 left-1.5" />}
      {/* Main pipeline flow is left-to-right -- input on the left, output on
          the right, same convention as AgentNode. The LLM sub-connector
          stays on the bottom edge regardless. */}
      <Handle
        type="target"
        position={Position.Left}
        title="Input (the previous pipeline step's output)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <div className="flex items-center gap-1.5">
        <Icon className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        <span className="truncate text-xs font-medium" title={data.label}>
          {data.label || 'Critic Gate'}
        </span>
      </div>
      <p className="truncate font-mono text-[0.65rem] text-muted-foreground" title={summary}>
        {summary}
      </p>
      {/* Same required LLM connector an agent node has -- no Tool/Memory
          slots here, gates never use tools and are always single-pass. */}
      <Handle
        type="target"
        id="llm"
        position={Position.Bottom}
        style={{ left: '50%' }}
        title="LLM (required)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="50%">LLM</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="llm" left="50%" />
      <Handle
        type="source"
        position={Position.Right}
        title="Output (drag to the next pipeline step)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
    </div>
  )
}
