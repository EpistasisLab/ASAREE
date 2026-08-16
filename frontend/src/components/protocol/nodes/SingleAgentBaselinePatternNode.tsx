import { useNodeConnections, type NodeProps } from '@xyflow/react'
import { ArrowRight } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { SingleAgentBaselinePatternNodeData } from '@/types/protocols'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'
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
  // See ReasonActPatternNode.tsx's own comment -- same swap-instead-of-
  // delete treatment, since an agent's execution pattern must never go to
  // zero.
  const connections = useNodeConnections({ id, handleType: 'source', handleId: 'architectural_pattern' })
  const { requestConnectorAdd } = useProtocolCanvasActions()
  const targetAgentId = connections[0]?.target

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
      swap={
        targetAgentId
          ? { label: 'Swap pattern', onSwap: () => requestConnectorAdd({ nodeId: targetAgentId, slot: 'architectural_pattern' }) }
          : undefined
      }
    />
  )
}
