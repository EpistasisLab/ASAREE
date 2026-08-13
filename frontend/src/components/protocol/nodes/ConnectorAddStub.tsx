import type { MouseEvent } from 'react'
import { useNodeConnections, useReactFlow } from '@xyflow/react'
import { Plus } from 'lucide-react'
import { newNodeId } from '@/lib/nodeId'
import { defaultLlmNodeData, defaultMcpToolNodeData, defaultMemoryNodeData } from '@/types/protocols'
import { findFreePosition } from '../layout'

type ConnectorSlot = 'llm' | 'tool' | 'memory'

// Same node type + starting config every "+" for a given slot creates --
// there's exactly one kind of thing that can plug into each slot today
// (unlike n8n, which offers several Memory/Tool backends and needs a
// picker), so clicking "+" creates and connects in one step, no picker.
const SLOT_NODE_FACTORY: Record<ConnectorSlot, () => { type: string; data: ReturnType<typeof defaultLlmNodeData> | ReturnType<typeof defaultMcpToolNodeData> | ReturnType<typeof defaultMemoryNodeData> }> = {
  llm: () => ({ type: 'llm', data: defaultLlmNodeData('New LLM') }),
  tool: () => ({ type: 'mcp_tool', data: defaultMcpToolNodeData('New MCP Tool') }),
  memory: () => ({ type: 'memory', data: defaultMemoryNodeData('New Memory') }),
}

// n8n's own affordance for an empty sub-connector: a short stub line ending
// in a small "+" circle, inviting a click to create and wire up the right
// kind of node -- rather than requiring a manual drag from the tiny handle
// dot. Hidden once the slot has a connection, unless `allowMultiple` (Tool
// is unlimited -- n8n keeps its own "+" visible there even after the first
// connection, so you can add more).
export function ConnectorAddStub({
  nodeId,
  slot,
  left,
  side = 'bottom',
  allowMultiple = false,
}: {
  nodeId: string
  slot: ConnectorSlot
  left?: string
  side?: 'bottom' | 'right'
  allowMultiple?: boolean
}) {
  const connections = useNodeConnections({ id: nodeId, handleType: 'target', handleId: slot })
  const { getNode, getNodes, addNodes, addEdges } = useReactFlow()

  if (connections.length > 0 && !allowMultiple) return null

  function handleClick(e: MouseEvent) {
    e.stopPropagation()
    const current = getNode(nodeId)
    if (!current) return
    const desired =
      side === 'right'
        ? { x: current.position.x + 220, y: current.position.y }
        : { x: current.position.x, y: current.position.y + 140 }
    const position = findFreePosition(getNodes().map((n) => n.position), desired)
    const { type, data } = SLOT_NODE_FACTORY[slot]()
    const newId = newNodeId()
    addNodes({ id: newId, type, position, data })
    addEdges({ id: newNodeId(), source: newId, sourceHandle: slot, target: nodeId, targetHandle: slot })
  }

  return (
    <div
      className={
        side === 'right'
          ? 'absolute top-1/2 -right-11 flex -translate-y-1/2 items-center'
          : 'absolute -bottom-11 flex -translate-x-1/2 flex-col items-center'
      }
      style={side === 'bottom' ? { left } : undefined}
    >
      {side === 'right' ? (
        <div className="h-px w-3 bg-[color:var(--card-accent)]/50" />
      ) : (
        <div className="h-3 w-px bg-[color:var(--card-accent)]/50" />
      )}
      <button
        type="button"
        onClick={handleClick}
        aria-label={`Add ${slot}`}
        title={`Add ${slot}`}
        className="flex size-3.5 items-center justify-center rounded-full border border-dashed border-[color:var(--card-accent)]/70 text-[color:var(--card-accent)] transition-colors hover:bg-[color:var(--card-accent)]/10"
      >
        <Plus className="size-2.5" />
      </button>
    </div>
  )
}
