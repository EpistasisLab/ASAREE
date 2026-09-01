import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from 'react'
import { ListChecks, PanelLeftClose, PencilRuler } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { DesignTab } from './DesignTab'
import type { ProtocolCanvasHandle } from './ProtocolCanvas'
import { RunsTab } from './RunsTab'
import type { Experiment } from '@/types/experiments'

// The default full-panel width and what double-clicking the drag handle snaps
// back to. It includes the labeled navigation rail.
const DEFAULT_PANEL_WIDTH = 416
const PANEL_RAIL_WIDTH = 96

// Below ~320px the design controls become hard to use. Keep that much content
// width beside the labeled rail; collapse is the compact-view escape hatch.
const MIN_PANEL_WIDTH = PANEL_RAIL_WIDTH + 320

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
const PANEL_COLLAPSED_STORAGE_KEY = 'asaree:experiment-panel-collapsed'
type PanelTab = 'design' | 'runs'

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

function readStoredCollapsed(): boolean {
  return typeof window !== 'undefined' && window.localStorage.getItem(PANEL_COLLAPSED_STORAGE_KEY) === 'true'
}

// A fixed left panel on the protocol canvas -- the primary place to build
// and monitor an experiment. Design declares the factorial plan; Runs is the
// intentionally compact companion view that starts from its unique cells.
//
// Drag-resizable by its right edge, because a 384px column is the wrong
// width for every design. The handle lives in the page's existing gap-3 gutter
// (see the -right-3 w-3 below), so widening the panel is a drag on the seam
// you'd already aim at, and the width is remembered across sessions.
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
  const [collapsed, setCollapsed] = useState(readStoredCollapsed)
  const [activeTab, setActiveTab] = useState<PanelTab>('design')
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

  function setPanelCollapsed(next: boolean) {
    setCollapsed(next)
    window.localStorage.setItem(PANEL_COLLAPSED_STORAGE_KEY, String(next))
  }

  function openPanel(tab: PanelTab) {
    setActiveTab(tab)
    setPanelCollapsed(false)
  }

  return (
    <div className="relative flex h-full min-h-0 shrink-0" style={{ width: collapsed ? PANEL_RAIL_WIDTH : width }}>
      {/* The rail is always visible: these are the only panel selectors, not
          duplicate top tabs. Selecting one also restores the content area
          when it is collapsed. */}
      <Card className={cn('flex h-full w-24 shrink-0 flex-col gap-1 p-2', !collapsed && 'rounded-r-none border-r-0')}>
        <button
          type="button"
          aria-label="Open Design panel"
          aria-pressed={activeTab === 'design'}
          title="Open Design panel"
          onClick={() => openPanel('design')}
          className={`flex h-8 w-full cursor-pointer items-center gap-2 rounded-md px-2 text-xs font-medium transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
            activeTab === 'design' ? 'bg-muted text-foreground' : 'text-muted-foreground'
          }`}
        >
          <PencilRuler className="size-4" aria-hidden="true" />
          <span>Design</span>
        </button>
        <button
          type="button"
          aria-label="Open Runs panel"
          aria-pressed={activeTab === 'runs'}
          title="Open Runs panel"
          onClick={() => openPanel('runs')}
          className={`flex h-8 w-full cursor-pointer items-center gap-2 rounded-md px-2 text-xs font-medium transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
            activeTab === 'runs' ? 'bg-muted text-foreground' : 'text-muted-foreground'
          }`}
        >
          <ListChecks className="size-4" aria-hidden="true" />
          <span>Runs</span>
        </button>
      </Card>

      {!collapsed && (
        <Card className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-l-none p-0">
          <div className="flex h-11 shrink-0 items-center justify-between border-b px-3">
            <span className="text-sm font-medium">{activeTab === 'design' ? 'Design' : 'Runs'}</span>
            <button
              type="button"
              aria-label="Collapse experiment panel"
              title="Collapse experiment panel"
              onClick={() => setPanelCollapsed(true)}
              className="flex size-8 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <PanelLeftClose className="size-4" aria-hidden="true" />
            </button>
          </div>
          {isLoading || !experiment ? (
            <div className="space-y-3 p-3">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-32 w-full" />
            </div>
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto">
              {activeTab === 'design' ? (
                <DesignTab experiment={experiment} protocolId={protocolId} canvasRef={canvasRef} />
              ) : (
                <RunsTab experimentId={experiment.id} />
              )}
            </div>
          )}
        </Card>
      )}

      {/* Sits in the page's own 12px gutter between panel and canvas (-right-3
          w-3), so it costs no layout width and lands exactly where the eye
          already reads a seam. A separator role rather than a button: arrow
          keys resize it in 24px steps, which is also the only way to do this
          without a pointer. Double-click snaps back to the default. */}
      {!collapsed && <div
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
      </div>}
    </div>
  )
}
