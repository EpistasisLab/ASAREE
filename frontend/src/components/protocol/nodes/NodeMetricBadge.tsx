import { Gauge } from 'lucide-react'

// Companion to NodeFactorBadge: a persistent canvas marker for custom
// metrics whose producer binding points at this node. Metrics use their own
// top-left corner and the Custom badge's --chart-1 hue, leaving the factor
// marker's top-right violet identity intact when a node has both.
export function NodeMetricBadge({ count, className }: { count: number; className: string }) {
  const label = `${count} metric${count === 1 ? '' : 's'}`
  return (
    <div className={`absolute z-10 w-7 ${className}`} title={`${label} produced by this node`}>
      <div className="flex size-7 items-center justify-center rounded-full bg-card ring-1 ring-[color:var(--chart-1)]/60 shadow-[0_0_10px_-2px_var(--chart-1)]">
        <Gauge className="size-4 text-[color:var(--chart-1)]" />
      </div>
      <span className="absolute top-full left-1/2 mt-0.5 -translate-x-1/2 rounded bg-card px-1 text-[0.6rem] leading-tight font-medium whitespace-nowrap text-[color:var(--chart-1)]">
        {label}
      </span>
    </div>
  )
}
