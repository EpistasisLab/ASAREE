import type { MouseEvent, ReactNode } from 'react'
import { useReactFlow } from '@xyflow/react'
import { Play, Power, PowerOff, Repeat, Trash2, Variable } from 'lucide-react'
import { Button } from '@/components/ui/button'

function ToolbarIconButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string
  onClick: (e: MouseEvent) => void
  disabled?: boolean
  children: ReactNode
}) {
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      aria-label={label}
      title={label}
      disabled={disabled}
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
  runAlone,
  swap,
  onMakeFactor,
}: {
  nodeId: string
  // Absent for pure config-source nodes (llm/memory/pattern) -- "deactivate"
  // has no meaning for something that isn't ever "run" in the first place,
  // so those node types only get Delete, not the power icon.
  isActive?: boolean
  onToggleActive?: () => void
  // The canvas's per-node Play icon -- only ever passed for an Agent node.
  // `canRun: false` means this node has upstream input (running one node
  // mid-pipeline against real upstream output needs a bounded/partial-run
  // entrypoint this executor doesn't have yet, same note "Execute step"
  // has always had here) -- shown disabled with an explanatory tooltip
  // rather than hidden, so a node where it doesn't apply doesn't read as
  // "this feature isn't here." Deliberately never substitutes a bound
  // field's factor level (services.protocol_execution._run_single_node's
  // own "no factor substitution" doc comment) -- this always runs the
  // node's own literal, currently-typed configuration, the same as opening
  // its inspector and reading the field verbatim. The tooltip below says so
  // explicitly rather than leaving that distinction to be discovered the
  // hard way.
  runAlone?: { canRun: boolean; onRun: () => void }
  // Replaces Delete with a Swap icon entirely -- for a node that must never
  // go to zero (today: the sole connected execution-pattern node on an
  // agent), deleting isn't a valid action at all, only replacing it with a
  // different node is. See ReasonActPatternNode.tsx's own comment for why
  // this is conditional on actually being connected to something.
  swap?: { label: string; onSwap: () => void }
  // The last button on every node type -- opens a field picker (scoped to
  // this node's own bindable fields) for turning one into an experimental
  // factor, same entry point as DesignTab's "Add factor" but pre-filtered
  // to save hunting through every node on the canvas. Always passed (every
  // node type gets this), unlike the other optional buttons above which are
  // conditional on node-type-specific capabilities.
  onMakeFactor?: () => void
}) {
  const { deleteElements } = useReactFlow()

  return (
    <div className="absolute -top-8 left-1/2 z-10 flex -translate-x-1/2 items-center gap-0.5 rounded-md border bg-card px-1 py-0.5 opacity-0 shadow-[0_0_10px_-4px_var(--primary)] ring-1 ring-primary/20 transition-opacity group-hover:opacity-100">
      {runAlone && (
        // A disabled Button gets `pointer-events-none`, which would also
        // swallow its own `title` tooltip -- wrapping it in a span that
        // carries the tooltip instead is this codebase's own established
        // fix for the same problem (see RunAllCellsButton in
        // ProtocolCanvasPage.tsx).
        <span title={runAlone.canRun ? undefined : "Has upstream input -- can't run alone yet"}>
          <ToolbarIconButton
            label="Run this node's own configuration as currently set -- never a bound factor's level"
            disabled={!runAlone.canRun}
            onClick={() => runAlone.canRun && runAlone.onRun()}
          >
            <Play className="size-3" />
          </ToolbarIconButton>
        </span>
      )}
      {isActive !== undefined && onToggleActive && (
        <ToolbarIconButton label={isActive ? 'Deactivate' : 'Activate'} onClick={onToggleActive}>
          {isActive ? <Power className="size-3" /> : <PowerOff className="size-3" />}
        </ToolbarIconButton>
      )}
      {swap ? (
        <ToolbarIconButton label={swap.label} onClick={swap.onSwap}>
          <Repeat className="size-3" />
        </ToolbarIconButton>
      ) : (
        <ToolbarIconButton label="Delete" onClick={() => void deleteElements({ nodes: [{ id: nodeId }] })}>
          <Trash2 className="size-3" />
        </ToolbarIconButton>
      )}
      {onMakeFactor && (
        <ToolbarIconButton label="Make experimental factor" onClick={onMakeFactor}>
          <Variable className="size-3" />
        </ToolbarIconButton>
      )}
    </div>
  )
}
