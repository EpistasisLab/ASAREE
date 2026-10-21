import type { ResultsScope } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { useExecutionSummary } from '@/lib/executionQueries'
import { experimentResultStatus } from '@/lib/resultUsage'

export function ResultsStatusBadge({ experimentId, scope }: { experimentId: string; scope: ResultsScope }) {
  // Observe the Results browser's summary without starting another polling loop.
  const summary = useExecutionSummary(experimentId, scope, false)
  if (!summary.data?.ready) return null
  const status = experimentResultStatus(summary.data)
  const accent = status === 'Completed' ? 'text-[color:var(--chart-3)]'
    : status === 'In progress' ? 'text-primary'
      : status === 'Needs attention' ? 'text-[color:var(--chart-4)]' : 'text-muted-foreground'
  return <Badge variant="outline" className={`shrink-0 border-current ${accent}`}>{status}</Badge>
}
