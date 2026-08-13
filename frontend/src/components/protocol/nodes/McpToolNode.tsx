import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Wrench } from 'lucide-react'
import { cardAccent, hashToChartHue } from '@/lib/utils'
import type { McpToolNodeData } from '@/types/protocols'

// A different hue from "agent" -- real category variety (CLAUDE.md's
// hash-driven tint rule), all mcp_tool nodes still share this one hue.
const ACCENT = hashToChartHue('mcp_tool')

export function McpToolNode({ data, selected }: NodeProps & { data: McpToolNodeData }) {
  const summary = data.config?.tool_name
    ? `${data.config.server_name ?? '?'}.${data.config.tool_name}`
    : 'Not configured'

  return (
    <div
      style={cardAccent(ACCENT)}
      className={`relative w-36 rounded-md border bg-card px-2 py-1.5 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
        selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''
      }`}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <div className="flex items-center gap-1.5">
        <Wrench className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        <span className="truncate text-xs font-medium" title={data.label}>
          {data.label || 'MCP Tool'}
        </span>
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
