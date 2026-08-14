import type { NodeProps } from '@xyflow/react'
import { ArrowRight } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { SingleAgentBaselinePatternNodeData } from '@/types/protocols'
import { CircleNode } from './CircleNode'

// The other Architectural Pattern connector node type (see
// SingleAgentBaselinePatternNodeData's own comment in types/protocols.ts).
// Rendered as n8n's own small circle-with-icon (see CircleNode) -- the
// dashed ring is the "not yet wired to a real run" signal (permanently
// inert, not a missing-field warning).
const ACCENT = hashToChartHue('pattern_single_agent_baseline')

export function SingleAgentBaselinePatternNode({
  id,
  data,
  selected,
}: NodeProps & { data: SingleAgentBaselinePatternNodeData }) {
  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={ArrowRight}
      label={data.label}
      placeholder="Single-Agent Baseline"
      handleId="architectural_pattern"
      dashed
    />
  )
}
