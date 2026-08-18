import { Split } from 'lucide-react'

// The "this node varies across the design" corner badge, for any node with at
// least one field bound to an experimental factor. Shared by the rectangular
// nodes (AgentNode, CriticGateNode) and the circular ones (via CircleNode's
// own `factorCount` prop), so one lucide `Split` glyph -- the same icon
// FactorBindableField's trigger uses -- means the same thing everywhere on the
// canvas.
//
// Built as a miniature of a connector node rather than a bare glyph, because
// nothing about a 16px icon said *what* it marked: a `size-7` disc (exactly
// half CircleNode's own `size-14`, so it reads as "attached to this node",
// never as a node in its own right) with a "N factor(s)" caption under it, the
// same circle-above-label idiom every Pattern/LLM/Tool node already uses.
//
// Violet (`--chart-2`), not the host node's `--card-accent`: violet is already
// this app's factor hue (FACTOR_TRIGGER_CLASSNAME's own button, the factor
// editor dialog), and CLAUDE.md's colour rule wants a tint to mean something
// -- here it means "factor", the one thing this badge is for, so it must not
// dissolve into whatever hue the node underneath happens to be.
//
// Both parts are OPAQUE and sit above the card's content (`z-10`): the badge
// hangs on the node's top-right corner, over the icon/label row, and anything
// translucent there read as a smudge on top of the text underneath. Position
// is per-caller via `className` since each node type's corner geometry differs
// (inset for rectangles, hung off the ring for circles).
export function NodeFactorBadge({ count, className }: { count: number; className: string }) {
  const label = `${count} factor${count === 1 ? '' : 's'}`
  return (
    // Fixed to the disc's own width so `className`'s offsets anchor the CIRCLE
    // on the corner; the caption is wider and overflows symmetrically out of
    // this box instead of shifting the circle off the corner to make room.
    <div className={`absolute z-10 w-7 ${className}`} title={`${label} bound on this node`}>
      <div className="flex size-7 items-center justify-center rounded-full bg-card ring-1 ring-[color:var(--chart-2)]/60 shadow-[0_0_10px_-2px_var(--chart-2)]">
        <Split className="size-4 text-[color:var(--chart-2)]" />
      </div>
      <span className="absolute top-full left-1/2 mt-0.5 -translate-x-1/2 rounded bg-card px-1 text-[0.6rem] leading-tight font-medium whitespace-nowrap text-[color:var(--chart-2)]">
        {label}
      </span>
    </div>
  )
}
