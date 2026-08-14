import { Handle, Position, useNodeConnections, useReactFlow, type NodeProps } from '@xyflow/react'
import { Wrench } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cardAccent, hashToChartHue } from '@/lib/utils'
import { nodeRunBadge } from '@/lib/protocolRun'
import type { McpToolNodeData, NodeRunStatus } from '@/types/protocols'
import { CircleNode } from './CircleNode'
import { ConnectorHandleLabel } from './ConnectorHandleLabel'
import { NodeHoverToolbar } from './NodeHoverToolbar'
import { NodeSummaryLine } from './NodeSummaryLine'

// A different hue from "agent" -- real category variety (CLAUDE.md's
// hash-driven tint rule), all mcp_tool nodes still share this one hue.
const ACCENT = hashToChartHue('mcp_tool')

export function McpToolNode({ id, data, selected }: NodeProps & { data: McpToolNodeData & { runStatus?: NodeRunStatus } }) {
  const summary = data.config?.tool_name ? `${data.config.server_name ?? '?'}.${data.config.tool_name}` : null
  const badge = nodeRunBadge(data.runStatus)
  const { updateNodeData } = useReactFlow()
  const isActive = data.active ?? true
  // Dual-purpose (see the Handle comment below): wired via this node's own
  // "tool" handle into an Agent's Tool connector, it plays the same role
  // n8n's dedicated MCP Client Tool node does, so it gets that node's own
  // n8n-style circle rendering instead of today's rounded-rectangle
  // pipeline-step card -- topological_order's dual-role-exclusivity rule
  // means it's never both at once, so this is a clean either/or, not a
  // partial overlay.
  const toolConnections = useNodeConnections({ id, handleType: 'source', handleId: 'tool' })
  const isToolSource = toolConnections.length > 0

  if (isToolSource) {
    return (
      <CircleNode
        id={id}
        selected={selected}
        accent={ACCENT}
        icon={Wrench}
        label={data.label}
        placeholder="MCP Tool"
        handleId="tool"
        warning={summary ? undefined : 'Not configured -- pick a server and tool'}
      />
    )
  }

  return (
    <div
      style={cardAccent(ACCENT)}
      className={`group relative w-36 rounded-md border bg-card px-2 py-3 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
        selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''
      } ${isActive ? '' : 'opacity-50'}`}
    >
      <NodeHoverToolbar nodeId={id} isActive={isActive} onToggleActive={() => updateNodeData(id, { active: !isActive })} />
      {badge && (
        <Badge className={`absolute -top-2.5 right-1.5 ${badge.className}`}>{badge.label}</Badge>
      )}
      <Handle
        type="target"
        position={Position.Left}
        title="Input (the previous pipeline step's output)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <div className="flex items-center gap-1.5">
        <Wrench className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        <span className="truncate text-xs font-medium" title={data.label}>
          {data.label || 'MCP Tool'}
        </span>
      </div>
      <NodeSummaryLine text={summary} emptyLabel="Not configured -- pick a server and tool" />
      {/* Two right-side handles share the edge, so each needs its own
          vertical slot (ConnectorHandleLabel's `top`) instead of both
          defaulting to dead center: plain pipeline output above, the
          dual-purpose Tool connector below. */}
      <Handle
        type="source"
        position={Position.Right}
        style={{ top: '30%' }}
        title="Output (drag to the next pipeline step)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      {/* Dual-purpose: wired here (a normal main edge, above) this node is
          still today's standalone pipeline step; wired via THIS handle
          into an Agent's Tool connector instead, it becomes one of that
          agent's own callable tools and isn't executed as its own step
          (services.protocol_execution's _resolve_tool_config/
          _tool_source_node_ids). A node can only be used one way at a
          time -- topological_order rejects mixing both on one instance. */}
      <Handle
        type="source"
        id="tool"
        position={Position.Right}
        style={{ top: '70%' }}
        title="Tool (plug into an Agent's Tool connector)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel side="right" top="70%">Tool</ConnectorHandleLabel>
    </div>
  )
}
