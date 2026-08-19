import { useState, type CSSProperties } from 'react'
import { BaseEdge, EdgeToolbar, getBezierPath, useReactFlow, type EdgeProps } from '@xyflow/react'
// Trash2, not an X -- the same glyph NodeHoverToolbar's own Delete button
// uses, so "remove this thing" looks identical whether the thing is a node or
// an edge. An X here also collided with the two other X's on the canvas
// (dismissing a panel, unbinding a factor), neither of which deletes anything.
import { Plus, Trash2 } from 'lucide-react'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'

// Every edge's look is decided here and nowhere else -- no edge in a persisted
// graph carries its own `style`, and index.css overrides none of xyflow's
// --xy-edge-* variables, so this function IS the canvas's wiring style.
//
// Heavier than xyflow's own 1px hairline but muted rather than full-strength:
// at 2px in a dimmed grey the wiring reads as structure you can trace without
// competing with the nodes for attention. Hover then swaps in the cyan accent
// and a glow -- this app's standard "you're pointing at this" cue (see the
// AppHeader nav links' own drop-shadow) -- so picking one edge out of a bundle
// is a matter of moving the mouse, not squinting.
//
// Solid vs. dashed splits the graph the way it actually reads: solid is the
// main left-to-right pipeline between agents and critic gates (the flow of
// work), dashed is everything hanging off a typed connector handle -- LLM,
// Memory, Tool, Dataset, Script, Architectural Pattern -- which supplies
// config to a node rather than passing work along. `isMainEdge` (no
// source/target handle) is exactly that distinction already.
//
// Note the dashes are NOT the same statement as MemoryNode's dashed ring,
// which means "not yet functional"; here they only mean "connector, not
// pipeline". Nothing currently renders both, but don't add a third meaning.
const EDGE_STROKE = 'color-mix(in oklch, var(--muted-foreground), transparent 30%)'

function edgeStyle(isMainEdge: boolean, hovered: boolean): CSSProperties {
  return {
    stroke: hovered ? 'var(--primary)' : EDGE_STROKE,
    strokeWidth: hovered ? 2.5 : 2,
    strokeDasharray: isMainEdge ? undefined : '6 4',
    filter: hovered ? 'drop-shadow(0 0 5px var(--primary))' : undefined,
    transition: 'stroke 120ms ease, stroke-width 120ms ease',
  }
}

// Every edge in this app renders through here (edgeTypes={{ default:
// InteractEdge }} on <ReactFlow>) -- the hover affordance is a small
// floating button group at the edge's midpoint, hidden until hovered.
// EdgeToolbar renders via a fixed-position portal (like NodeToolbar), so
// (unlike NodeHoverToolbar's CSS-only group-hover) this needs real hover
// state -- there's no shared DOM ancestor for a group-hover to key off of.
//
// Delete works for any edge except one into an agent's architectural_pattern
// handle -- an agent's execution pattern must never go to zero (see
// ProtocolCanvas.tsx's nonDeletablePatternNodeIds/edgesWithDeletable), so
// removing just the EDGE (leaving the pattern node orphaned but the agent
// unwired) would strand it exactly the same way removing the node directly
// would. Swapping (which removes both atomically) is still the only way to
// change it. "+" (insert a node in the middle) only shows for a plain
// "main" edge (no source/targetHandle) -- inserting an arbitrary node into
// a typed connector edge (LLM/Tool/Memory/Pattern) would violate that
// connector's own required shape, so it's hidden there too.
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
  const isPatternEdge = targetHandleId === 'architectural_pattern'

  return (
    <>
      {/* A persisted per-edge `style` still wins, but none currently sets one
          -- edgeStyle() is the whole story in practice. */}
      <BaseEdge
        id={id}
        path={edgePath}
        style={{ ...edgeStyle(isMainEdge, hovered), ...style }}
        markerEnd={markerEnd}
        interactionWidth={24}
      />
      {/* A second, purely invisible copy of the same path just to track
          hover -- BaseEdge's own interaction path exists for click/select,
          not exposed to us for mouse events. Pattern edges get this too, even
          though they have no toolbar below: the hover HIGHLIGHT applies to
          every edge, it's only the buttons that some edges don't offer. */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={24}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      />
      {/* Nothing to put in it for a pattern edge -- no delete (see above) and
          no insert -- so it's skipped entirely rather than rendered empty. */}
      <EdgeToolbar edgeId={id} x={labelX} y={labelY} isVisible={hovered && !isPatternEdge}>
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
            <Trash2 className="size-3" />
          </button>
        </div>
      </EdgeToolbar>
    </>
  )
}
