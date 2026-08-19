import { useNodeConnections, type NodeProps } from '@xyflow/react'
import { ArrowRight } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { SingleAgentBaselinePatternNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'
import { CircleNode } from './CircleNode'

// The other Architectural Pattern connector node type (see
// SingleAgentBaselinePatternNodeData's own comment in types/protocols.ts).
// Rendered as a small circle-with-icon (see CircleNode) -- see
// ReasonActPatternNode.tsx's own comment for why this is NOT dashed and is
// positioned above its agent with a bottom-facing connector.
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
      handlePosition="bottom"
      factorCount={boundFactorCount(data)}
      swap={
        targetAgentId
          ? { label: 'Swap pattern', onSwap: () => requestConnectorAdd({ nodeId: targetAgentId, slot: 'architectural_pattern' }) }
          : undefined
      }
    />
  )
}
