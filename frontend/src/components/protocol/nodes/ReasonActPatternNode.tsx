import { useNodeConnections, type NodeProps } from '@xyflow/react'
import { Repeat2 } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { ReasonActPatternNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'
import { CircleNode } from './CircleNode'

// One of two Architectural Pattern connector node types (see
// ReasonActPatternNodeData's own comment in types/protocols.ts). Rendered
// as a small circle-with-icon (see CircleNode) -- NOT dashed, unlike
// Memory: _resolve_pattern_config reads this node's own config into a real
// agentic-core PatternConfig on every run (protocol_execution.py's
// create_agent/update_agent calls), so it has a genuine runtime effect.
// Positioned ABOVE its agent by default (agentDefaultPattern in
// ProtocolCanvas.tsx) with its own connector on the BOTTOM edge
// (handlePosition="bottom" below) so the edge runs straight down into the
// agent's own top connector.
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
