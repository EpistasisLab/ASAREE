import { useNodeConnections, type NodeProps } from '@xyflow/react'
import { Repeat2 } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { ReasonActPatternNodeData } from '@/types/protocols'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'
import { CircleNode } from './CircleNode'

// One of two Architectural Pattern connector node types (see
// ReasonActPatternNodeData's own comment in types/protocols.ts). Rendered
// as n8n's own small circle-with-icon (see CircleNode) -- the dashed ring
// is the "not yet wired to a real run" signal (permanently inert, not a
// missing-field warning).
const ACCENT = hashToChartHue('pattern_reason_act')

export function ReasonActPatternNode({ id, data, selected }: NodeProps & { data: ReasonActPatternNodeData }) {
  // An agent's execution pattern must never go to zero (see
  // ProtocolCanvas.tsx's nonDeletablePatternNodeIds), so once this is
  // actually wired into an agent, its hover toolbar offers Swap instead of
  // Delete -- reusing the exact same requestConnectorAdd flow the agent's
  // own connector "+" uses, just requested from the pattern node's own
  // toolbar instead. An unconnected/orphaned pattern node (dragged onto the
  // canvas but never wired) has nothing to swap, so it keeps plain Delete.
  const connections = useNodeConnections({ id, handleType: 'source', handleId: 'architectural_pattern' })
  const { requestConnectorAdd } = useProtocolCanvasActions()
  const targetAgentId = connections[0]?.target

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
      swap={
        targetAgentId
          ? { label: 'Swap pattern', onSwap: () => requestConnectorAdd({ nodeId: targetAgentId, slot: 'architectural_pattern' }) }
          : undefined
      }
    />
  )
}
