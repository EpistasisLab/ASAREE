import { nodeAccent } from '@/lib/nodeAccent'
import { isUnderIterated } from '@/lib/reasonActIterations'
import { useNodeConnections, type NodeProps } from '@xyflow/react'
import { Repeat2 } from 'lucide-react'
import type { NodeRunState, ReasonActPatternNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'
import { CircleNode } from './CircleNode'

// One of two Architectural Pattern connector node types (see
// ReasonActPatternNodeData's own comment in types/protocols.ts). Rendered
// as a small circle-with-icon (see CircleNode) -- NOT dashed, unlike
// Memory: _resolve_pattern_config reads this node's own config into a real
// Motoro PatternConfig on every run (protocol_execution.py's
// create_agent/update_agent calls), so it has a genuine runtime effect.
// Positioned ABOVE its agent by default (agentDefaultPattern in
// ProtocolCanvas.tsx) with its own connector on the BOTTOM edge
// (handlePosition="bottom" below) so the edge runs straight down into the
// agent's own top connector.
const ACCENT = nodeAccent('pattern_reason_act')

export function ReasonActPatternNode({
  id,
  data,
  selected,
}: NodeProps & {
  data: ReasonActPatternNodeData & {
    hostHasNoTools?: boolean
    suggestedIterations?: number | null
    hostTruncation?: NodeRunState['truncation']
  }
}) {
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

  // scratchpad_window only matters -- and so is only required -- while
  // include_scratchpad is on; see nodeConfigIssues.ts's matching check.
  const warnings: string[] = []
  if (data.config.max_iterations == null) warnings.push('Max iterations is required')
  if (data.config.include_scratchpad && data.config.scratchpad_window == null) warnings.push('Scratchpad window is required')
  // The cap is set, but lower than the driven agent's own wiring needs
  // (lib/reasonActIterations.ts; computed in ProtocolCanvas.tsx because it
  // depends on the AGENT's connectors, not this node's config). A warning
  // rather than a silent default, because exhausting the cap does not fail the
  // run: Motoro keeps the last tool result and reports `completed`, so the
  // symptom the user actually sees is an Output Parser full of nulls, several
  // steps removed from the number that caused it.
  if (data.config.max_iterations != null && isUnderIterated(data.config.max_iterations, data.suggestedIterations ?? null))
    warnings.push(
      `Max iterations (${data.config.max_iterations}) is below what this agent's wiring needs (about ${data.suggestedIterations}) -- the loop will be cut off before the agent writes its answer`,
    )
  // Not an estimate -- the last run's own loop reported hitting the ceiling
  // (protocol_execution.py's _truncation_fields), which is why this is stated
  // as fact where the warning above hedges. Carried here from the AGENT's
  // node_run by ProtocolCanvas.tsx, because the cap that caused it is
  // configured on this node, not the one showing the "Hit iteration limit"
  // badge. Deliberately NOT part of findNodeConfigIssues: a past run's outcome
  // would keep blocking the pre-run dialog after the cap was already raised,
  // right up until the next run replaced it. Once the cap IS above what died,
  // the fix is made and only a re-run is missing, so this stops.
  const truncatedAt = data.hostTruncation?.max_iterations
  if (truncatedAt != null && data.config.max_iterations != null && data.config.max_iterations <= truncatedAt)
    warnings.push(
      `The last run stopped at this iteration limit (${truncatedAt}) with its answer unwritten -- raise Max iterations and run it again`,
    )
  // Not a misconfiguration -- the run succeeds. It just doesn't LOOP: with
  // nothing callable bound, motoro's reason_act ends on turn one (its own
  // `implicit_final_answer` path), so the arm is a single LLM call wearing a
  // ReAct label. Computed in ProtocolCanvas.tsx, since it depends on the
  // AGENT's wiring rather than this node's own config -- including, under Peer
  // Collaboration, a connected peer, which is callable in its own right (see
  // that flag's comment) and so keeps the loop going with no tools at all.
  if (data.hostHasNoTools)
    warnings.push('Nothing callable on the agent (no tools, skills, knowledge or peers) -- the loop ends after one turn')

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
      warning={warnings.length > 0 ? warnings : undefined}
      swap={
        targetAgentId
          ? { label: 'Swap pattern', onSwap: () => requestConnectorAdd({ nodeId: targetAgentId, slot: 'architectural_pattern' }) }
          : undefined
      }
    />
  )
}
