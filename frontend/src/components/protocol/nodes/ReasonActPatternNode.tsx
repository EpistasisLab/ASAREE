import type { NodeProps } from '@xyflow/react'
import { Repeat2 } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { ReasonActPatternNodeData } from '@/types/protocols'
import { CircleNode } from './CircleNode'

// One of two Architectural Pattern connector node types (see
// ReasonActPatternNodeData's own comment in types/protocols.ts). Rendered
// as n8n's own small circle-with-icon (see CircleNode) -- the dashed ring
// is the "not yet wired to a real run" signal (permanently inert, not a
// missing-field warning).
const ACCENT = hashToChartHue('pattern_reason_act')

export function ReasonActPatternNode({ id, data, selected }: NodeProps & { data: ReasonActPatternNodeData }) {
  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={Repeat2}
      label={data.label}
      placeholder="Reason + Act"
      handleId="architectural_pattern"
      dashed
    />
  )
}
