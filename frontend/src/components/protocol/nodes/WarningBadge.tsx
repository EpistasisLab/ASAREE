import { TriangleAlert } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

// The small warning triangle a misconfigured node shows (CircleNode's own
// corner badge, NodeSummaryLine's rectangular-node equivalent) -- a themed
// Tooltip, not the browser's native `title` (same reasoning as
// FactorBindableField's own trigger: slow, inconsistent chrome), listing
// every real issue under an "Issue:"/"Issues:" header rather than showing
// only the first if a node ever has more than one at once. `className`
// carries each caller's own positioning (CircleNode's circular corner
// badge vs. NodeSummaryLine's bare bottom-right icon).
export function WarningBadge({ issues, className }: { issues: string | string[]; className: string }) {
  const list = Array.isArray(issues) ? issues : [issues]
  if (list.length === 0) return null

  return (
    <TooltipProvider delay={200}>
      <Tooltip>
        <TooltipTrigger render={<div className={className} />}>
          <TriangleAlert className="size-3 text-[color:var(--chart-4)]" />
        </TooltipTrigger>
        <TooltipContent className="flex-col items-start gap-1 text-left">
          {list.length === 1 ? (
            <span>Issue: {list[0]}</span>
          ) : (
            <div>
              <p>Issues:</p>
              <ul className="list-disc pl-3">
                {list.map((issue, i) => (
                  <li key={i}>{issue}</li>
                ))}
              </ul>
            </div>
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
