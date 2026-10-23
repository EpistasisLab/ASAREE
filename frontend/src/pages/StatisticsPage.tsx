import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity } from 'lucide-react'
import { experimentsApi, statsApi } from '@/api/client'
import { AppHeader } from '@/components/AppHeader'
import { StatisticsReport } from '@/components/stats/StatisticsReport'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

const selectClass = 'w-full rounded-md border border-input bg-background px-3 py-2 text-sm'

export function StatisticsPage() {
  const [params, setParams] = useSearchParams()
  const experiment = params.get('experiment') ?? ''
  // Key the content so selections and errors cannot carry across experiments.
  return <div className="min-h-screen"><AppHeader /><main className="mx-auto max-w-6xl space-y-6 p-6">
    <h1 className="flex items-center gap-3 text-2xl font-semibold"><Activity className="text-primary" />Statistics</h1>
    <ExperimentStatistics key={experiment} experiment={experiment} select={id => setParams(id ? { experiment: id } : {})} />
  </main></div>
}

function ExperimentStatistics({ experiment, select }: { experiment: string; select: (id: string) => void }) {
  const client = useQueryClient()
  const [selected, setSelected] = useState('')
  const experiments = useQuery({ queryKey: ['experiments'], queryFn: () => experimentsApi.list() })
  const historyKey = ['automatic-statistics', experiment]
  const history = useQuery({
    queryKey: historyKey,
    queryFn: () => statsApi.history(experiment, 'stats_sm'),
    enabled: !!experiment,
    refetchInterval: query => query.state.data?.some(a => a.status === 'pending' || a.status === 'running') ? 2500 : false,
  })
  const analyses = (history.data ?? []).filter(a => a.config.family === 'auto')
  const activeId = selected || analyses[0]?.id || ''
  const analysis = useQuery({
    queryKey: ['stats-analysis', experiment, activeId],
    queryFn: () => statsApi.get(experiment, activeId),
    enabled: !!experiment && !!activeId,
    refetchInterval: query => query.state.data?.status === 'pending' || query.state.data?.status === 'running' ? 2500 : false,
  })
  const run = useMutation({
    mutationFn: () => statsApi.automatic(experiment),
    onSuccess: results => {
      setSelected(results[0]?.id ?? '')
      void client.invalidateQueries({ queryKey: historyKey })
    },
  })
  const pending = run.isPending || history.isLoading || analyses.some(a => a.status === 'pending' || a.status === 'running')
  const error = experiments.error || history.error || analysis.error || run.error
  return <>
    <Card><CardHeader><CardTitle>Analyze your experiment</CardTitle>
      <CardDescription>ASAREE detects declared metrics, selects suitable statistical methods, and checks the results. No statistical setup is required.</CardDescription>
    </CardHeader><CardContent className="space-y-4">
      <label className="block space-y-2 text-sm">Experiment<select aria-label="Experiment" className={selectClass} value={experiment} onChange={e => select(e.target.value)}>
        <option value="">Choose an experiment</option>{experiments.data?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      <Button disabled={!experiment || pending} onClick={() => run.mutate()}>{pending ? 'Analyzing metrics…' : 'Analyze all metrics'}</Button>
      {error && <p role="alert" className="text-sm text-destructive">{error.message}</p>}
    </CardContent></Card>
    {!!analyses.length && <label className="block space-y-2 text-sm">Metric report<select aria-label="Metric report" className={selectClass} value={activeId} onChange={e => setSelected(e.target.value)}>
      {analyses.map(item => <option key={item.id} value={item.id}>{item.config.metric} · {new Date(item.created_at).toLocaleString()} · {item.status}</option>)}
    </select></label>}
    {analysis.data?.status === 'failed' && <p role="alert" className="rounded border border-destructive p-4 text-sm">{analysis.data.error || 'Analysis failed. Please rerun the experiment analysis.'}</p>}
    {(analysis.data?.status === 'pending' || analysis.data?.status === 'running') && <p role="status" className="text-sm text-muted-foreground">The selected metric is being analyzed. You can leave this page and return later.</p>}
    {analysis.data?.status === 'completed' && <StatisticsReport key={analysis.data.id} analysis={analysis.data} experiment={experiment} />}
  </>
}
