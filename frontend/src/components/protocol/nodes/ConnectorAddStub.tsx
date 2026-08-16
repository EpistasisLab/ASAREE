import type { MouseEvent } from 'react'
import { useNodeConnections } from '@xyflow/react'
import { Plus } from 'lucide-react'
import { useProtocolCanvasActions, type ConnectorSlot } from '../ProtocolCanvasContext'

// n8n's own affordance for an empty sub-connector: a short stub line ending
// in a small "+" circle. Clicking it opens the same "+" side panel the
// canvas's own toolbar button does (via ProtocolCanvasContext), filtered to
// this slot's allowed node type(s) -- matching n8n's picker rather than
// instant-creating a node, so this keeps working the same way once a slot
// ever offers more than one kind of node. Hidden once the slot has a
// connection, unless `alwaysVisible` -- Tool is genuinely unlimited (n8n
// keeps its own "+" visible there even after the first connection);
// Architectural Pattern is capped at one but must never go to zero either
// (see AgentNode.tsx's own comment), so its stub also never hides, but
// picking a new node there REPLACES the existing one instead of adding a
// second (addNode()'s own pendingConnectorAdd branch, not this component).
export function ConnectorAddStub({
  nodeId,
  slot,
  left,
  side = 'bottom',
  alwaysVisible = false,
}: {
  nodeId: string
  slot: ConnectorSlot
  left?: string
  side?: 'bottom' | 'right' | 'top'
  alwaysVisible?: boolean
}) {
  const connections = useNodeConnections({ id: nodeId, handleType: 'target', handleId: slot })
  const { requestConnectorAdd } = useProtocolCanvasActions()

  if (connections.length > 0 && !alwaysVisible) return null

  function handleClick(e: MouseEvent) {
    e.stopPropagation()
    requestConnectorAdd({ nodeId, slot })
  }

  const circle = (
    <span className="flex size-3.5 items-center justify-center rounded-full border border-dashed border-[color:var(--card-accent)]/70 text-[color:var(--card-accent)] transition-colors group-hover:bg-[color:var(--card-accent)]/10">
      <Plus className="size-2.5" />
    </span>
  )
  const verticalLine = <div className="h-3 w-px bg-[color:var(--card-accent)]/50" />

  // The whole stub (line + circle) is one <button>, not just the innermost
  // circle -- that circle alone is a ~14px target, easy to miss and land on
  // the node/pane behind it instead (which shows xyflow's own grab cursor),
  // making the pointer cursor feel like it never kicks in. Padding widens
  // the actual hoverable/clickable area without changing the visible glyph.
  //
  // side="top" mirrors side="bottom" (line connects to the node edge,
  // circle sits furthest away) but with the child order flipped: bottom's
  // container starts right below the node so its first child (the line)
  // renders closest to the node, but top's container ends right above the
  // node, so the line needs to be its LAST child to land in the same
  // "closest to the node" spot.
  return (
    <button
      type="button"
      onClick={handleClick}
      aria-label={`Add ${slot}`}
      title={`Add ${slot}`}
      className={
        side === 'right'
          ? 'group absolute top-1/2 -right-11 flex -translate-y-1/2 cursor-pointer items-center p-1.5'
          : side === 'top'
            ? 'group absolute -top-11 flex -translate-x-1/2 cursor-pointer flex-col items-center p-1.5'
            : 'group absolute -bottom-11 flex -translate-x-1/2 cursor-pointer flex-col items-center p-1.5'
      }
      style={side !== 'right' ? { left } : undefined}
    >
      {side === 'right' ? (
        <>
          <div className="h-px w-3 bg-[color:var(--card-accent)]/50" />
          {circle}
        </>
      ) : side === 'top' ? (
        <>
          {circle}
          {verticalLine}
        </>
      ) : (
        <>
          {verticalLine}
          {circle}
        </>
      )}
    </button>
  )
}
