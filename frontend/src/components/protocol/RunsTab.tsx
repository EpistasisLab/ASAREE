import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { experimentsApi } from '@/api/client'
import { displayFactorValue, groupReplicatesIntoCells } from '@/lib/experiment'

// This view deliberately begins at the design's natural unit: a cell is one
// unique factor combination, regardless of how many independently-run
// replicates it contains. Detailed replicate execution belongs to a later
// drill-in, not to this high-level Runs panel.
export function RunsTab({ experimentId }: { experimentId: string }) {
  const replicatesQuery = useQuery({
    queryKey: ['experiments', experimentId, 'replicates'],
    queryFn: () => experimentsApi.listReplicates(experimentId),
  })

  if (replicatesQuery.isLoading) {
    return (
      <div className="space-y-3 p-3">
        <Skeleton className="h-5 w-20" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    )
  }

  if (replicatesQuery.isError || !replicatesQuery.data) {
    return <p className="p-3 text-sm text-muted-foreground">Could not load this experiment’s cells.</p>
  }

  const cells = groupReplicatesIntoCells(replicatesQuery.data)
  if (cells.length === 0) {
    return <p className="p-3 text-sm text-muted-foreground">No cells yet — generate this experiment’s design first.</p>
  }

  return (
    <section className="space-y-1.5 p-3" aria-labelledby="run-cells-heading">
      <div className="flex items-center justify-between gap-2">
        <h2 id="run-cells-heading" className="text-sm font-medium">Cells</h2>
        <span className="font-mono text-xs text-muted-foreground">{cells.length}</span>
      </div>
      <div className="divide-y overflow-hidden rounded-md border">
        {cells.map((cell) => (
          <div key={cell.label} className="flex items-start gap-2 px-2.5 py-2">
            <div className="min-w-0 flex-1">
              <p className="truncate font-mono text-xs font-medium" title={cell.label}>{cell.label}</p>
              {Object.keys(cell.factorValues).length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {Object.entries(cell.factorValues).map(([name, value]) => (
                    <Badge key={name} variant="outline" className="max-w-full font-mono text-[0.65rem] font-normal">
                      <span className="truncate">{name}={displayFactorValue(value)}</span>
                    </Badge>
                  ))}
                </div>
              )}
            </div>
            <span className="shrink-0 font-mono text-xs text-muted-foreground">
              {cell.replicates.length} {cell.replicates.length === 1 ? 'replicate' : 'replicates'}
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}
