import { useState } from 'react'
import { BaseEdge, EdgeToolbar, getBezierPath, useReactFlow, type EdgeProps } from '@xyflow/react'
import { Plus, X } from 'lucide-react'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'

// Every edge in this app renders through here (edgeTypes={{ default:
// InteractEdge }} on <ReactFlow>) -- n8n's own hover affordance: a small
// floating button group at the edge's midpoint, hidden until hovered.
// EdgeToolbar renders via a fixed-position portal (like NodeToolbar), so
// (unlike NodeHoverToolbar's CSS-only group-hover) this needs real hover
// state -- there's no shared DOM ancestor for a group-hover to key off of.
//
// Delete works for any edge. "+" (insert a node in the middle) only shows
// for a plain "main" edge (no source/targetHandle) -- inserting an
// arbitrary node into a typed connector edge (LLM/Tool/Memory/Pattern)
// would violate that connector's own required shape, so it's hidden there.
export function InteractEdge({
  id,
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  sourceHandleId,
  targetHandleId,
  style,
  markerEnd,
}: EdgeProps) {
  const [hovered, setHovered] = useState(false)
  const { setEdges } = useReactFlow()
  const { requestEdgeInsert } = useProtocolCanvasActions()
  const [edgePath, labelX, labelY] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition })
  const isMainEdge = !sourceHandleId && !targetHandleId

  return (
    <>
      <BaseEdge id={id} path={edgePath} style={style} markerEnd={markerEnd} interactionWidth={24} />
      {/* A second, purely invisible copy of the same path just to track
          hover -- BaseEdge's own interaction path exists for click/select,
          not exposed to us for mouse events. */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={24}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      />
      <EdgeToolbar edgeId={id} x={labelX} y={labelY} isVisible={hovered}>
        <div
          className="flex items-center gap-1 rounded-md border bg-card px-1 py-0.5 shadow-[0_0_10px_-4px_var(--primary)] ring-1 ring-primary/20"
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
        >
          {isMainEdge && (
            <button
              type="button"
              aria-label="Add node on this connection"
              title="Add node on this connection"
              className="flex size-5 cursor-pointer items-center justify-center rounded-full text-primary hover:bg-primary/10"
              onClick={() => requestEdgeInsert({ edgeId: id, source, target })}
            >
              <Plus className="size-3" />
            </button>
          )}
          <button
            type="button"
            aria-label="Delete connection"
            title="Delete connection"
            className="flex size-5 cursor-pointer items-center justify-center rounded-full text-destructive hover:bg-destructive/10"
            onClick={() => setEdges((eds) => eds.filter((e) => e.id !== id))}
          >
            <X className="size-3" />
          </button>
        </div>
      </EdgeToolbar>
    </>
  )
}
