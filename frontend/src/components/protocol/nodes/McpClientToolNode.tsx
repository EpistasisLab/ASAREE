import { useReactFlow, type NodeProps } from '@xyflow/react'
import { Plug } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { McpToolNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { CircleNode } from './CircleNode'

// Its own hue, distinct from McpToolNode's -- CLAUDE.md's hash-driven tint
// rule: "a server the deployment provided" and "a server this protocol
// connected to itself" are a real category difference, and the second one is
// worth spotting at a glance on someone else's canvas.
const ACCENT = hashToChartHue('mcp_client_tool')

// Behaves exactly like McpToolNode -- an Agent's Tool-connector source,
// allow-listing a subset of one server's tools, never a pipeline step of its
// own. It differs only in provenance: the server behind it was registered from
// the canvas (ConnectMcpServerDialog) rather than already being in
// GET /mcp-servers, so its inspector also shows the transport and endpoint.
export function McpClientToolNode({ id, data, selected }: NodeProps & { data: McpToolNodeData }) {
  const toolNames = data.config?.tool_names ?? []
  const summary = toolNames.length > 0 ? `${data.config.server_name ?? '?'}: ${toolNames.join(', ')}` : null
  const { updateNodeData } = useReactFlow()
  const enabled = data.config?.enabled ?? true
  const allowListIsFactor = !!data.factor_bindings?.['config.tool_names']

  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={Plug}
      label={data.label}
      placeholder="MCP Client Tool"
      handleId="tool"
      // Same wording as McpToolNode's: the server is pinned at creation here
      // too, so the allow-list is the only thing that can be empty -- and
      // same suppression once that allow-list is itself a factor.
      warning={summary || allowListIsFactor ? undefined : 'Not configured -- allow at least one tool'}
      factorCount={boundFactorCount(data)}
      dimmed={!enabled}
      isActive={enabled}
      onToggleActive={() => updateNodeData(id, { config: { ...data.config, enabled: !enabled } })}
    />
  )
}
