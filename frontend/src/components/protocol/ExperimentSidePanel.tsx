import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { CellsTab } from './cells/CellsTab'
import { DesignTab } from './DesignTab'
import type { ProtocolCanvasHandle } from './ProtocolCanvas'
import { ResultsTab } from './ResultsTab'
import { RunsTab } from './RunsTab'
import type { Experiment } from '@/types/experiments'

// The old fixed w-96 (384px), now only the starting width and what
// double-clicking the drag handle snaps back to.
const DEFAULT_PANEL_WIDTH = 384

// Below ~320px the tab strip wraps and every table in here is unreadable, so
// there's nothing useful on the other side of this floor -- collapsing the
// panel entirely would be a different feature (a toggle, not a drag).
const MIN_PANEL_WIDTH = 320

// Two independent ceilings: never wider than this outright, and never so wide
// that the canvas -- the thing this page is actually for -- has less than a
// couple of nodes' worth of room left. On a narrow viewport the second one
// binds, on a wide one the first does.
const MAX_PANEL_WIDTH = 1100
const MIN_CANVAS_WIDTH = 420

// Same "remember my last layout choice" convention as the node inspector's
// own opacity (see NodeInspectorDialog's OPACITY_STORAGE_KEY): one global
// value under an `asaree:` key, not per experiment -- how wide you like this
// panel is a property of how you work, not of which experiment you opened.
const PANEL_WIDTH_STORAGE_KEY = 'asaree:experiment-panel-width'

function clampPanelWidth(width: number): number {
  const viewportMax = typeof window !== 'undefined' ? window.innerWidth - MIN_CANVAS_WIDTH : MAX_PANEL_WIDTH
  const max = Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, viewportMax))
  return Math.round(Math.min(max, Math.max(MIN_PANEL_WIDTH, width)))
}

function readStoredPanelWidth(): number {
  const raw = typeof window !== 'undefined' ? window.localStorage.getItem(PANEL_WIDTH_STORAGE_KEY) : null
  const parsed = raw !== null ? Number(raw) : NaN
  return clampPanelWidth(Number.isFinite(parsed) ? parsed : DEFAULT_PANEL_WIDTH)
}

// A fixed left panel on the protocol canvas -- the primary place to build
// and monitor an experiment (Design/Cells/Runs/Results), replacing the
// previous edge-to-edge canvas with no persistent experiment context, and
// now also the only home for what used to be a separate static experiment
// detail page (the cells heatmap/table, the per-agent run tally). First
// page-level use of components/ui/tabs.tsx (previously only inside one
// node's own inspector, AgentNodeInspector.tsx) -- same tab-group idiom,
// just at the page layout level instead of a floating dialog.
//
// Drag-resizable by its right edge, because a 384px column is the wrong
// width for half of what now lives in here: the Design tab is comfortable at
// it, the Cells table (one column per factor + up to 4 metrics) is not, and
// which of those you're doing changes minute to minute. The handle lives in
// the page's existing gap-3 gutter (see the -right-3 w-3 below), so widening
// the panel is a drag on the seam you'd already aim at, and the width is
// remembered across sessions. The maximize overlays in the Cells/Results
// tabs are still the answer for "show me everything at once"; this is for
// settling on a working width.
export function ExperimentSidePanel({
  experiment,
  protocolId,
  canvasRef,
  isLoading,
}: {
  experiment: Experiment | undefined
  protocolId: string | undefined
  canvasRef: RefObject<ProtocolCanvasHandle | null>
  isLoading: boolean
}) {
  const [width, setWidth] = useState(readStoredPanelWidth)
  const [dragging, setDragging] = useState(false)
  const dragStart = useRef<{ x: number; width: number } | null>(null)

  // The stored width was clamped against the viewport it was chosen on --
  // re-clamp on resize so a panel dragged wide on a big monitor doesn't
  // squeeze the canvas out of existence on a small one.
  useEffect(() => {
    const onResize = () => setWidth((w) => clampPanelWidth(w))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // While dragging, the cursor and the no-select apply to the whole document,
  // not just the 12px handle: the pointer spends the entire drag OUTSIDE the
  // handle (that's what makes it a drag), and without this it flickers back
  // to the canvas's own grab cursor and starts selecting panel text.
  useEffect(() => {
    if (!dragging) return
    const previousCursor = document.body.style.cursor
    const previousSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    return () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousSelect
    }
  }, [dragging])

  function persist(next: number) {
    window.localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, String(next))
  }

  function beginDrag(e: ReactPointerEvent<HTMLDivElement>) {
    // Pointer capture, not window listeners: the drag crosses the React Flow
    // canvas, which otherwise swallows pointermove for its own panning.
    e.currentTarget.setPointerCapture(e.pointerId)
    dragStart.current = { x: e.clientX, width }
    setDragging(true)
  }

  function onDragMove(e: ReactPointerEvent<HTMLDivElement>) {
    if (!dragStart.current) return
    setWidth(clampPanelWidth(dragStart.current.width + e.clientX - dragStart.current.x))
  }

  function endDrag() {
    if (!dragStart.current) return
    dragStart.current = null
    setDragging(false)
    persist(width)
  }

  function nudge(delta: number) {
    setWidth((w) => {
      const next = clampPanelWidth(w + delta)
      persist(next)
      return next
    })
  }

  return (
    <div className="relative flex min-h-0 shrink-0" style={{ width }}>
      <Card className="flex min-h-0 w-full flex-col overflow-hidden p-0">
        {isLoading || !experiment ? (
          <div className="space-y-3 p-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : (
          <Tabs defaultValue="design" className="flex h-full min-h-0 flex-col">
            <TabsList className="mx-3 mt-3 shrink-0">
              <TabsTrigger value="design">Design</TabsTrigger>
              {/* Between Design and Runs, in the order the work actually happens:
                  declare the design, look at the cells it produced, watch them
                  run, then read the analysis. Deliberately NOT folded into
                  Results -- that tab is the statistical analysis OF these
                  numbers (effects, CIs, non-inferiority), not the raw grid. */}
              <TabsTrigger value="cells">Cells</TabsTrigger>
              <TabsTrigger value="runs">Runs</TabsTrigger>
              <TabsTrigger value="results">Results</TabsTrigger>
            </TabsList>

            {/* min-h-0 is load-bearing here -- without it, a flex item's
                default min-height:auto keeps this box as tall as its content,
                so on a short viewport the panel silently overflows the page
                instead of scrolling. overflow-y-auto lives directly on each
                TabsContent (the one bounded box), not on a nested div inside
                it, so there's exactly one scroll container per tab. */}
            <TabsContent value="design" className="min-h-0 flex-1 overflow-y-auto">
              <DesignTab experiment={experiment} protocolId={protocolId} canvasRef={canvasRef} />
            </TabsContent>

            <TabsContent value="cells" className="min-h-0 flex-1 overflow-y-auto">
              <CellsTab experiment={experiment} />
            </TabsContent>

            <TabsContent value="runs" className="min-h-0 flex-1 overflow-y-auto">
              {protocolId ? (
                <RunsTab experimentId={experiment.id} protocolId={protocolId} />
              ) : (
                <p className="p-3 text-sm text-muted-foreground">This experiment has no protocol yet.</p>
              )}
            </TabsContent>

            <TabsContent value="results" className="min-h-0 flex-1 overflow-y-auto">
              <ResultsTab experimentId={experiment.id} />
            </TabsContent>
          </Tabs>
        )}
      </Card>

      {/* Sits in the page's own 12px gutter between panel and canvas (-right-3
          w-3), so it costs no layout width and lands exactly where the eye
          already reads a seam. A separator role rather than a button: arrow
          keys resize it in 24px steps, which is also the only way to do this
          without a pointer. Double-click snaps back to the default. */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the experiment panel"
        aria-valuenow={width}
        aria-valuemin={MIN_PANEL_WIDTH}
        aria-valuemax={MAX_PANEL_WIDTH}
        tabIndex={0}
        onPointerDown={beginDrag}
        onPointerMove={onDragMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onDoubleClick={() => {
          const next = clampPanelWidth(DEFAULT_PANEL_WIDTH)
          setWidth(next)
          persist(next)
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowLeft') nudge(-24)
          else if (e.key === 'ArrowRight') nudge(24)
          else return
          e.preventDefault()
        }}
        title="Drag to resize — double-click to reset"
        className="group/resize absolute top-0 -right-3 z-20 flex h-full w-3 cursor-col-resize touch-none items-center justify-center focus:outline-none"
      >
        {/* The hairline itself, lit in the theme's accent while hovered,
            focused or dragging -- the same "you're pointing at this" glow the
            canvas's own edges use on hover. */}
        <div
          className={cn(
            'h-full w-px rounded-full transition-[width,background-color,box-shadow] duration-150',
            dragging
              ? 'w-0.5 bg-primary shadow-[0_0_8px_var(--primary)]'
              : 'bg-border group-hover/resize:w-0.5 group-hover/resize:bg-primary group-hover/resize:shadow-[0_0_8px_var(--primary)] group-focus-visible/resize:w-0.5 group-focus-visible/resize:bg-primary group-focus-visible/resize:shadow-[0_0_8px_var(--primary)]',
          )}
        />
      </div>
    </div>
  )
}
