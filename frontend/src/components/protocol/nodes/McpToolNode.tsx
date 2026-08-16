import type { NodeProps } from '@xyflow/react'
import { Wrench } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { McpToolNodeData } from '@/types/protocols'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'
import { CircleNode } from './CircleNode'

// A different hue from "agent" -- real category variety (CLAUDE.md's
// hash-driven tint rule), all mcp_tool nodes still share this one hue.
const ACCENT = hashToChartHue('mcp_tool')

// Always an Agent's Tool-connector source -- one MCP server connection,
// allow-listing a subset of its tools (McpToolNodeConfig.tool_names) --
// never a standalone pipeline step. Matches n8n's own MCP Client Tool node,
// which likewise only ever exists as a sub-node wired into an agent. No
// run-status badge or "deactivate": same reasoning as LlmNode -- the
// executor gives it an instant, inert placeholder node_run, never a real
// execution.
export function McpToolNode({ id, data, selected }: NodeProps & { data: McpToolNodeData }) {
  const toolNames = data.config?.tool_names ?? []
  const summary = toolNames.length > 0 ? `${data.config.server_name ?? '?'}: ${toolNames.join(', ')}` : null
  const { requestMakeFactor } = useProtocolCanvasActions()

  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={Wrench}
      label={data.label}
      placeholder="MCP Tool"
      handleId="tool"
      warning={summary ? undefined : 'Not configured -- pick a server and at least one tool'}
      onMakeFactor={() => requestMakeFactor(id)}
    />
  )
}
