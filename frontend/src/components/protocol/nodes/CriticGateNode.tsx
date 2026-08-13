import { useState } from 'react'
import { Handle, Position, useReactFlow, type NodeProps } from '@xyflow/react'
import { ShieldCheck, ShieldOff } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cardAccent, hashToChartHue } from '@/lib/utils'
import { nodeRunBadge } from '@/lib/protocolRun'
import type { CriticGateNodeData, NodeRunStatus } from '@/types/protocols'
import { EditableNodeLabel } from './EditableNodeLabel'
import { NodeHoverToolbar } from './NodeHoverToolbar'

// A third hue, distinct from "agent"/"mcp_tool" -- real category variety
// (CLAUDE.md's hash-driven tint rule), all critic_gate nodes share this one.
const ACCENT = hashToChartHue('critic_gate')

export function CriticGateNode({ id, data, selected }: NodeProps & { data: CriticGateNodeData & { runStatus?: NodeRunStatus } }) {
  const enabled = data.config?.enabled ?? true
  const Icon = enabled ? ShieldCheck : ShieldOff
  const summary = enabled ? `Up to ${data.config?.max_revisions ?? 1} revision(s)` : 'Gate disabled'
  const badge = nodeRunBadge(data.runStatus)
  const [renaming, setRenaming] = useState(false)
  const { updateNodeData } = useReactFlow()

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
        onRename={() => setRenaming(true)}
      />
      {badge && (
        <Badge className={`absolute -top-2.5 right-1.5 ${badge.className}`}>{badge.label}</Badge>
      )}
      <Handle
        type="target"
        position={Position.Top}
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <div className="flex items-center gap-1.5">
        <Icon className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        <EditableNodeLabel nodeId={id} label={data.label} placeholder="Critic Gate" renaming={renaming} onRenamingChange={setRenaming} />
      </div>
      <p className="truncate font-mono text-[0.65rem] text-muted-foreground" title={summary}>
        {summary}
      </p>
      <Handle
        type="source"
        position={Position.Bottom}
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
    </div>
  )
}
