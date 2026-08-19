import type { MouseEvent } from 'react'
import { Plus } from 'lucide-react'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'

// The main pipeline handle's own "+" affordance -- always visible
// (unlike ConnectorAddStub, which hides once a named slot has its one
// connection): agent<->agent wiring means "these two can interact," and
// fan-out/fan-in are both unrestricted (any number of edges), so there's
// always room for one more, the same way Tool's stub never hides either.
export function MainEdgeAddStub({ nodeId, direction }: { nodeId: string; direction: 'incoming' | 'outgoing' }) {
  const { requestMainEdgeAdd } = useProtocolCanvasActions()

  function handleClick(e: MouseEvent) {
    e.stopPropagation()
    requestMainEdgeAdd({ nodeId, direction })
  }

  const isOutgoing = direction === 'outgoing'
  return (
    <button
      type="button"
      onClick={handleClick}
      aria-label={isOutgoing ? 'Connect to another agent' : 'Connect an agent that feeds this one'}
      title="Connect to another agent"
      className={`group absolute top-1/2 flex -translate-y-1/2 cursor-pointer items-center p-1.5 ${
        isOutgoing ? '-right-11' : '-left-11 flex-row-reverse'
      }`}
    >
      <div className="h-px w-3 bg-[color:var(--card-accent)]/50" />
      <span className="flex size-3.5 items-center justify-center rounded-full border border-dashed border-[color:var(--card-accent)]/70 text-[color:var(--card-accent)] transition-colors group-hover:bg-[color:var(--card-accent)]/10">
        <Plus className="size-2.5" />
      </span>
    </button>
  )
}
