import { Variable } from 'lucide-react'

// A small "(x)" badge (lucide's Variable glyph -- same icon FactorBindableField's
// own trigger uses everywhere else) for a rectangular node with at least one
// field bound to an experimental factor -- CircleNode's own `hasFactor` prop
// renders the equivalent for a circular node. Position is per-caller via
// `className` since each rectangular node card has different existing
// corner occupants (run-status Badge, warning triangle) to avoid colliding
// with.
export function NodeFactorBadge({ className }: { className: string }) {
  return (
    <div
      className={`absolute flex items-center justify-center ${className}`}
      title="One or more fields are bound to an experimental factor"
    >
      <Variable className="size-3 text-[color:var(--chart-2)]" />
    </div>
  )
}
