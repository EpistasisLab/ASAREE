import { nodeAccent } from '@/lib/nodeAccent'
import { useReactFlow, type NodeProps } from '@xyflow/react'
import { Wrench } from 'lucide-react'
import type { McpToolNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { CircleNode } from './CircleNode'

// One hue for the kind, not per instance: all mcp_tool nodes share this, and
// it's a slot of its own next to mcp_client_tool's (see lib/nodeAccent.ts).
const ACCENT = nodeAccent('mcp_tool')

// Always an Agent's Tool-connector source -- one MCP server connection,
// allow-listing a subset of its tools (McpToolNodeConfig.tool_names) --
// never a standalone pipeline step: it only ever exists as a sub-node wired
// into an agent. No
// run-status badge: same reasoning as LlmNode -- the executor gives it an
// instant, inert placeholder node_run, never a real execution. The hover
// toolbar's own power icon DOES apply here though (unlike LlmNode) --
// toggles this same config.enabled _resolve_tool_config already skips a
// disabled tool node's contribution for, same as the Switch in its own
// inspector.
export function McpToolNode({ id, data, selected }: NodeProps & { data: McpToolNodeData }) {
  const toolNames = data.config?.tool_names ?? []
  const summary = toolNames.length > 0 ? `${data.config.server_name ?? '?'}: ${toolNames.join(', ')}` : null
  const { updateNodeData } = useReactFlow()
  const enabled = data.config?.enabled ?? true
  // Every MCP node already HAS its server (picked in the browser at creation
  // time), so telling its user to go pick one would send them looking for a
  // dropdown that isn't there -- only the allow-list can be empty. Not shown
  // once the allow-list is a factor: each cell brings its own, and an empty
  // base value is then expected rather than unconfigured (same call as
  // nodeConfigIssues.ts makes for the Run pre-flight).
  const allowListIsFactor = !!data.factor_bindings?.['config.tool_names']
  const warning = allowListIsFactor ? undefined : 'Not configured -- allow at least one tool'

  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={Wrench}
      label={data.label}
      placeholder="MCP Tool"
      handleId="tool"
      warning={summary ? undefined : warning}
      factorCount={boundFactorCount(data)}
      dimmed={!enabled}
      isActive={enabled}
      onToggleActive={() => updateNodeData(id, { config: { ...data.config, enabled: !enabled } })}
    />
  )
}
