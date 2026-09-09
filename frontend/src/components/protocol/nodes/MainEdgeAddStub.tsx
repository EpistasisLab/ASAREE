import type { MouseEvent } from 'react'
import { Plus } from 'lucide-react'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'

// The main pipeline handle's own "+" affordance. Visible whenever the side it
// sits on can still take an edge, which depends on the experiment's
// coordination strategy: under Peer Collaboration or Critic Gate fan-out and
// fan-in are both unrestricted, so it never hides (the way Tool's stub never
// does either), but under Sequential the chain rule caps each side at one, and
// then it hides like a filled connector slot -- ProtocolCanvas decides which
// and passes `full`.
export function MainEdgeAddStub({
  nodeId,
  direction,
  full,
}: {
  nodeId: string
  direction: 'incoming' | 'outgoing'
  full?: boolean
}) {
  const { requestMainEdgeAdd } = useProtocolCanvasActions()

  function handleClick(e: MouseEvent) {
    e.stopPropagation()
    requestMainEdgeAdd({ nodeId, direction })
  }

  if (full) return null

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
