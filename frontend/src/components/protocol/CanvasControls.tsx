import { Maximize2, RotateCcw, ZoomIn, ZoomOut } from 'lucide-react'
import { useReactFlow, useViewport } from '@xyflow/react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { DEFAULT_ZOOM } from './constants'

// Replaces xyflow's built-in <Controls /> -- its plain unstyled buttons read
// as barely-visible against this theme's dark surface, and its native
// `title` attributes are real tooltips but slow/inconsistent browser
// chrome, not the themed Tooltip every other control in the app uses.
function ControlIconButton({
  label,
  onClick,
  children,
}: {
  label: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger render={<Button variant="outline" size="icon-sm" aria-label={label} onClick={onClick} />}>
        {children}
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  )
}

export function CanvasControls() {
  const { zoomIn, zoomOut, fitView, zoomTo } = useReactFlow()
  const { zoom } = useViewport()
  // A "reset zoom" button that only appears once the user has actually
  // zoomed away from the canvas's default level.
  const isDefaultZoom = Math.abs(zoom - DEFAULT_ZOOM) < 0.01

  return (
    <TooltipProvider delay={200}>
      <div className="absolute bottom-3 left-3 z-10 flex flex-col gap-1 rounded-lg border bg-card p-1 shadow-[0_0_16px_-6px_var(--primary)] ring-1 ring-primary/20">
        {/* This conditional button must stay FIRST in the list -- the
            container is bottom-anchored (`bottom-3`), so a child growing/
            shrinking the stack only shifts whatever's ABOVE it; everything
            at or after this slot (Zoom in/Zoom out/Fit view below) stays
            pinned to the same screen position whether this row is present
            or not. It used to sit between Zoom out and Fit view, which
            shifted Zoom in/out every time it appeared. */}
        {!isDefaultZoom && (
          // zoomTo (not fitView) keeps the current pan/center fixed --
          // this resets scale only, not "recenter on all nodes" too.
          <ControlIconButton label="Reset zoom" onClick={() => zoomTo(DEFAULT_ZOOM, { duration: 200 })}>
            <RotateCcw className="size-4" />
          </ControlIconButton>
        )}
        <ControlIconButton label="Zoom in" onClick={() => zoomIn()}>
          <ZoomIn className="size-4" />
        </ControlIconButton>
        <ControlIconButton label="Zoom out" onClick={() => zoomOut()}>
          <ZoomOut className="size-4" />
        </ControlIconButton>
        <ControlIconButton label="Fit view" onClick={() => fitView({ maxZoom: DEFAULT_ZOOM })}>
          <Maximize2 className="size-4" />
        </ControlIconButton>
      </div>
    </TooltipProvider>
  )
}
