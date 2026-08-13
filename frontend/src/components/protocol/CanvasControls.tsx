import { Maximize2, ZoomIn, ZoomOut } from 'lucide-react'
import { useReactFlow } from '@xyflow/react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

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
  const { zoomIn, zoomOut, fitView } = useReactFlow()

  return (
    <TooltipProvider delay={200}>
      <div className="absolute bottom-3 left-3 z-10 flex flex-col gap-1 rounded-lg border bg-card p-1 shadow-[0_0_16px_-6px_var(--primary)] ring-1 ring-primary/20">
        <ControlIconButton label="Zoom in" onClick={() => zoomIn()}>
          <ZoomIn className="size-4" />
        </ControlIconButton>
        <ControlIconButton label="Zoom out" onClick={() => zoomOut()}>
          <ZoomOut className="size-4" />
        </ControlIconButton>
        <ControlIconButton label="Fit view" onClick={() => fitView()}>
          <Maximize2 className="size-4" />
        </ControlIconButton>
      </div>
    </TooltipProvider>
  )
}
