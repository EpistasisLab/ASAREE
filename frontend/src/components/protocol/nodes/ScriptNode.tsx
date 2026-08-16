import type { NodeProps } from '@xyflow/react'
import { Code2 } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { ScriptNodeData } from '@/types/protocols'
import { CircleNode } from './CircleNode'

// Carries a fixed piece of code an Agent passes verbatim as some tool's own
// code-shaped argument -- a real runtime effect once wired (see
// ScriptNodeData's own comment in types/protocols.ts), so NOT dashed.
// Rendered as n8n's own small circle-with-icon (see CircleNode), same as
// every other connector source.
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
      handleId="script"
      warning={data.config?.code ? undefined : 'No code set'}
    />
  )
}
