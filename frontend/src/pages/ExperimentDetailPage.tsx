import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bot, Database, FlaskConical } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { AppHeader } from '@/components/AppHeader'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatDate, formatRelative } from '@/lib/format'
import { agentsApi, datasetsApi, experimentsApi, runsApi } from '@/api/client'
import type { Agent } from '@/types/agents'

function formatKv(values: Record<string, unknown> | null): string {
  if (!values || Object.keys(values).length === 0) return '—'
  return Object.entries(values)
    .map(([key, value]) => `${key}=${typeof value === 'number' ? value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '') : value}`)
    .join(', ')
}

function DatasetCard({ datasetId }: { datasetId: string }) {
  const { data: dataset, isLoading } = useQuery({
    queryKey: ['datasets', datasetId],
    queryFn: () => datasetsApi.get(datasetId),
  })

  if (isLoading) return <Skeleton className="h-40 w-full" />
  if (!dataset) return null

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Database className="size-4 text-primary" />
          <CardTitle className="font-mono">{dataset.name}</CardTitle>
        </div>
        <CardDescription>{dataset.description ?? 'Registered dataset for this experiment.'}</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          <div className="flex items-center justify-between sm:col-span-2">
            <dt className="text-muted-foreground">Target column</dt>
            <dd className="font-mono">{dataset.target_column ?? '—'}</dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-muted-foreground">Train split</dt>
            <dd className="flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-primary shadow-[0_0_6px_var(--primary)]" />
              <span className="font-mono text-xs text-muted-foreground">{dataset.train_sha256.slice(0, 10)}</span>
            </dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-muted-foreground">Test split</dt>
            <dd className="flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-primary shadow-[0_0_6px_var(--primary)]" />
              <span className="font-mono text-xs text-muted-foreground">{dataset.test_sha256.slice(0, 10)}</span>
            </dd>
          </div>
          {dataset.dictionary_json && (
            <div className="flex items-center justify-between sm:col-span-2">
              <dt className="text-muted-foreground">Data dictionary</dt>
              <dd>
                <Badge variant="outline" className="font-normal text-muted-foreground">
                  attached
                </Badge>
              </dd>
            </div>
          )}
          <div className="flex items-center justify-between sm:col-span-2">
            <dt className="text-muted-foreground">Registered</dt>
            <dd title={dataset.created_at ? new Date(dataset.created_at).toLocaleString() : undefined}>
              {dataset.created_at ? formatDate(dataset.created_at) : '—'}
            </dd>
          </div>
        </dl>
      </CardContent>
    </Card>
  )
}

function AgentCard({ agent, runCount, lastUsed }: { agent: Agent; runCount: number; lastUsed: string }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <Bot className="size-4 shrink-0 text-primary" />
            <CardTitle className="truncate">{agent.name}</CardTitle>
          </div>
          {agent.model_config.model && (
            <Badge variant="outline" className="shrink-0 font-mono font-normal text-muted-foreground">
              {agent.model_config.model}
            </Badge>
          )}
        </div>
        <CardDescription className="line-clamp-2 min-h-10">{agent.goal || agent.description || 'No goal set.'}</CardDescription>
      </CardHeader>
      <CardContent className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {runCount} run{runCount === 1 ? '' : 's'} in this experiment
        </span>
        <span className="font-mono" title={new Date(lastUsed).toLocaleString()}>
          {formatRelative(lastUsed)}
        </span>
      </CardContent>
    </Card>
  )
}

function AgentsSection({ experimentId }: { experimentId: string }) {
  const runsQuery = useQuery({ queryKey: ['runs'], queryFn: () => runsApi.list() })
  const agentsQuery = useQuery({ queryKey: ['agents'], queryFn: () => agentsApi.list() })

  const experimentAgents = useMemo(() => {
    if (!runsQuery.data || !agentsQuery.data) return null
    const agentsById = new Map(agentsQuery.data.map((a) => [a.id, a]))
    const stats = new Map<string, { count: number; lastUsed: string }>()
    for (const run of runsQuery.data) {
      if (run.run_metadata?.experiment_id !== experimentId) continue
      const existing = stats.get(run.agent_id)
      if (existing) {
        existing.count += 1
        if (run.created_at > existing.lastUsed) existing.lastUsed = run.created_at
      } else {
        stats.set(run.agent_id, { count: 1, lastUsed: run.created_at })
      }
    }
    return Array.from(stats, ([agentId, s]) => ({ agent: agentsById.get(agentId), ...s }))
      .filter((x): x is { agent: Agent; count: number; lastUsed: string } => !!x.agent)
      .sort((a, b) => b.count - a.count)
  }, [runsQuery.data, agentsQuery.data, experimentId])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Agents</CardTitle>
        <CardDescription>Agents that have run at least one step in this experiment.</CardDescription>
      </CardHeader>
      <CardContent>
        {runsQuery.isLoading || agentsQuery.isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-28 w-full" />
          </div>
        ) : !experimentAgents || experimentAgents.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-10 text-center">
            <Bot className="size-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">No agent runs recorded for this experiment yet.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {experimentAgents.map(({ agent, count, lastUsed }) => (
              <AgentCard key={agent.id} agent={agent} runCount={count} lastUsed={lastUsed} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
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
          <div className="flex items-center gap-2.5">
            <FlaskConical className="size-6 text-primary" />
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">{experimentQuery.data.name}</h1>
              {experimentQuery.data.description && (
                <p className="text-sm text-muted-foreground">{experimentQuery.data.description}</p>
              )}
            </div>
          </div>
        )}

        {experimentQuery.data?.dataset_id ? (
          <DatasetCard datasetId={experimentQuery.data.dataset_id} />
        ) : experimentQuery.data ? (
          <Card>
            <CardContent className="flex items-center gap-3 text-sm text-muted-foreground">
              <Database className="size-4 shrink-0" />
              No dataset attached to this experiment.
            </CardContent>
          </Card>
        ) : null}

        {experimentId && <AgentsSection experimentId={experimentId} />}

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
