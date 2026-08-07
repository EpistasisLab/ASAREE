import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { AppHeader } from '@/components/AppHeader'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { experimentsApi } from '@/api/client'

function formatKv(values: Record<string, unknown> | null): string {
  if (!values || Object.keys(values).length === 0) return '—'
  return Object.entries(values)
    .map(([key, value]) => `${key}=${typeof value === 'number' ? value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '') : value}`)
    .join(', ')
}

export function ExperimentDetailPage() {
  const { experimentId } = useParams<{ experimentId: string }>()

  const experimentQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId!),
    enabled: !!experimentId,
  })
  const cellsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'cells'],
    queryFn: () => experimentsApi.listCells(experimentId!),
    enabled: !!experimentId,
  })

  return (
    <div className="min-h-svh bg-muted/30">
      <AppHeader />

      <main className="mx-auto max-w-5xl space-y-6 px-6 py-10">
        <div>
          <Link to="/experiments" className="text-sm text-muted-foreground hover:underline">
            ← Experiments
          </Link>
        </div>

        {experimentQuery.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : experimentQuery.isError || !experimentQuery.data ? (
          <p className="text-sm text-muted-foreground">Could not load this experiment.</p>
        ) : (
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{experimentQuery.data.name}</h1>
            {experimentQuery.data.description && (
              <p className="text-sm text-muted-foreground">{experimentQuery.data.description}</p>
            )}
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Cells</CardTitle>
            <CardDescription>One row per design point in this experiment's factorial grid.</CardDescription>
          </CardHeader>
          <CardContent>
            {cellsQuery.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
              </div>
            ) : !cellsQuery.data || cellsQuery.data.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No cells yet — this experiment's design hasn't been generated.
              </p>
            ) : (
              <Table>
                <TableHeader className="bg-muted/40">
                  <TableRow>
                    <TableHead className="h-11 text-xs font-semibold tracking-wide uppercase">Cell</TableHead>
                    <TableHead className="h-11 text-xs font-semibold tracking-wide uppercase">Factors</TableHead>
                    <TableHead className="h-11 text-xs font-semibold tracking-wide uppercase">Metrics</TableHead>
                    <TableHead className="h-11 text-xs font-semibold tracking-wide uppercase">Status</TableHead>
                    <TableHead className="h-11 text-xs font-semibold tracking-wide uppercase">Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {cellsQuery.data.map((cell) => (
                    <TableRow key={cell.id} className="even:bg-muted/15">
                      <TableCell className="py-3.5 font-mono text-sm font-medium">{cell.cell_label}</TableCell>
                      <TableCell className="py-3.5 font-mono text-xs text-muted-foreground">
                        {formatKv(cell.factor_values)}
                      </TableCell>
                      <TableCell className="py-3.5 font-mono text-xs text-muted-foreground">
                        {formatKv(cell.metric_values)}
                      </TableCell>
                      <TableCell className="py-3.5">
                        <Badge variant={cell.metric_values ? 'default' : 'secondary'}>
                          {cell.metric_values ? 'Scored' : 'Pending'}
                        </Badge>
                      </TableCell>
                      <TableCell className="py-3.5 text-muted-foreground">
                        {new Date(cell.updated_at).toLocaleString()}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  )
}
