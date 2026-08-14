import type { NodeProps } from '@xyflow/react'
import { BrainCircuit } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { MemoryNodeData } from '@/types/protocols'
import { CircleNode } from './CircleNode'

// Visual/validation scaffolding only -- see MemoryNodeData's own comment in
// types/protocols.ts. Rendered as n8n's own small circle-with-icon (see
// CircleNode); the dashed ring is the "not yet functional" signal here
// (permanently inert, not a missing-field warning), so no separate warning
// badge on top of it.
const ACCENT = hashToChartHue('memory')

export function MemoryNode({ id, data, selected }: NodeProps & { data: MemoryNodeData }) {
  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={BrainCircuit}
      label={data.label}
      placeholder="Memory"
      handleId="memory"
      dashed
    />
  )
}
