import type { NodeProps } from '@xyflow/react'
import { Code2 } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { ScriptNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { CircleNode } from './CircleNode'

// Carries a fixed piece of code an Agent passes verbatim as some tool's own
// code-shaped argument -- a real runtime effect once wired (see
// ScriptNodeData's own comment in types/protocols.ts), so NOT dashed.
// Rendered as a small circle-with-icon (see CircleNode), same as
// every other connector source. Wires into the Agent's shared Tool
// connector (handleId="tool", alongside mcp_tool -- see AgentNode.tsx's own
// comment), not a dedicated "script" handle.
const ACCENT = hashToChartHue('script')

export function ScriptNode({ id, data, selected }: NodeProps & { data: ScriptNodeData }) {
  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={Code2}
      label={data.label}
      placeholder="Script"
      handleId="tool"
      warning={data.config?.code ? undefined : 'No code set'}
      factorCount={boundFactorCount(data)}
    />
  )
}
