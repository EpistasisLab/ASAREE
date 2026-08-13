import { useState } from 'react'
import { Handle, Position, useReactFlow, type NodeProps } from '@xyflow/react'
import { Wrench } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cardAccent, hashToChartHue } from '@/lib/utils'
import { nodeRunBadge } from '@/lib/protocolRun'
import type { McpToolNodeData, NodeRunStatus } from '@/types/protocols'
import { EditableNodeLabel } from './EditableNodeLabel'
import { NodeHoverToolbar } from './NodeHoverToolbar'

// A different hue from "agent" -- real category variety (CLAUDE.md's
// hash-driven tint rule), all mcp_tool nodes still share this one hue.
const ACCENT = hashToChartHue('mcp_tool')

export function McpToolNode({ id, data, selected }: NodeProps & { data: McpToolNodeData & { runStatus?: NodeRunStatus } }) {
  const summary = data.config?.tool_name
    ? `${data.config.server_name ?? '?'}.${data.config.tool_name}`
    : 'Not configured'
  const badge = nodeRunBadge(data.runStatus)
  const [renaming, setRenaming] = useState(false)
  const { updateNodeData } = useReactFlow()
  const isActive = data.active ?? true

  return (
    <div
      style={cardAccent(ACCENT)}
      className={`group relative w-36 rounded-md border bg-card px-2 py-1.5 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
        selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''
      } ${isActive ? '' : 'opacity-50'}`}
    >
      <NodeHoverToolbar
        nodeId={id}
        isActive={isActive}
        onToggleActive={() => updateNodeData(id, { active: !isActive })}
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
        <Wrench className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        <EditableNodeLabel nodeId={id} label={data.label} placeholder="MCP Tool" renaming={renaming} onRenamingChange={setRenaming} />
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
