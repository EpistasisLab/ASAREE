import type { MouseEvent, ReactNode } from 'react'
import { useReactFlow } from '@xyflow/react'
import { Power, PowerOff, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'

function ToolbarIconButton({
  label,
  onClick,
  children,
}: {
  label: string
  onClick: (e: MouseEvent) => void
  children: ReactNode
}) {
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      aria-label={label}
      title={label}
      onClick={(e) => {
        e.stopPropagation()
        onClick(e)
      }}
    >
      {children}
    </Button>
  )
}

// n8n's own per-node hover toolbar, minus "Execute step" (deferred --
// running one node against the last run's upstream output needs a bounded/
// partial-run entrypoint this executor doesn't have yet). Pure CSS show/hide
// (opacity-0 group-hover:opacity-100 on the parent node card, no JS state)
// so it costs nothing when not hovered. Every icon stops propagation so a
// click here never bubbles into the card's own onNodeClick/
// onNodeDoubleClick (select / open inspector) already wired in
// ProtocolCanvas.tsx. Delete goes through useReactFlow().deleteElements
// directly -- no prop drilling back to ProtocolCanvas.tsx needed, since
// that already flows through the same onNodesChange/onEdgesChange pipeline
// as this app's own controlled nodes/edges state.
export function NodeHoverToolbar({
  nodeId,
  isActive,
  onToggleActive,
}: {
  nodeId: string
  // Absent for pure config-source nodes (llm/memory/pattern) -- "deactivate"
  // has no meaning for something that isn't ever "run" in the first place,
  // so those node types only get Delete, not the power icon.
  isActive?: boolean
  onToggleActive?: () => void
}) {
  const { deleteElements } = useReactFlow()

  return (
    <div className="absolute -top-8 left-1/2 z-10 flex -translate-x-1/2 items-center gap-0.5 rounded-md border bg-card px-1 py-0.5 opacity-0 shadow-[0_0_10px_-4px_var(--primary)] ring-1 ring-primary/20 transition-opacity group-hover:opacity-100">
      {isActive !== undefined && onToggleActive && (
        <ToolbarIconButton label={isActive ? 'Deactivate' : 'Activate'} onClick={onToggleActive}>
          {isActive ? <Power className="size-3" /> : <PowerOff className="size-3" />}
        </ToolbarIconButton>
      )}
      <ToolbarIconButton label="Delete" onClick={() => void deleteElements({ nodes: [{ id: nodeId }] })}>
        <Trash2 className="size-3" />
      </ToolbarIconButton>
    </div>
  )
}
