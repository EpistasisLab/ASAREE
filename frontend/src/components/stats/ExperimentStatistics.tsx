import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight } from 'lucide-react'
import { statsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { StatisticsReport } from './StatisticsReport'
import type { StatsAnalysis } from '@/types/stats'

export function ExperimentStatistics({ experiment, version }: { experiment: string; version: string | null }) {
  const client = useQueryClient()
  const historyKey = ['automatic-statistics', experiment, version]
  const source = useQuery({
    queryKey: ['statistics-source', experiment, version],
    queryFn: () => statsApi.source(experiment, version!),
    enabled: !!version,
    refetchInterval: 15000,
  })
  const history = useQuery({
    queryKey: historyKey,
    queryFn: () => statsApi.history(experiment, version!),
    enabled: !!version,
    refetchInterval: query => query.state.data?.some(a => a.status === 'pending' || a.status === 'running') ? 2500 : false,
  })
  const run = useMutation({
    mutationFn: () => statsApi.automatic(experiment, version!),
    onSuccess: () => client.invalidateQueries({ queryKey: historyKey }),
  })
  const latest = new Map<string, StatsAnalysis>()
  for (const analysis of history.data ?? []) {
    if (analysis.config.family === 'auto' && !latest.has(analysis.config.metric)) {
      latest.set(analysis.config.metric, analysis)
    }
  }
  const analyses = [...latest.values()]
  const pending = run.isPending || analyses.some(a => a.status === 'pending' || a.status === 'running')
  const reports = analyses.filter(a => a.status === 'completed' && a.report_status === 'valid')
  const failed = analyses.filter(a => a.status === 'failed')
  const error = source.error || history.error || run.error
  const ready = !!version && !source.isError && (source.data?.n_measured_replicates ?? 0) > 0

  return <div className="@container space-y-3 p-3">
    <p className="text-xs text-muted-foreground">Analyze metrics from the experiment version being viewed. Methods are selected automatically. Rerunning replaces this version’s existing reports.</p>
    <Button size="sm" disabled={!ready || pending || history.isLoading} onClick={() => run.mutate()}>
      {pending ? 'Analyzing metrics…' : 'Analyze all metrics'}
    </Button>
    {!version && <p className="text-xs text-muted-foreground">Publish the experiment and run replicates to enable statistics.</p>}
    {version && !source.isLoading && !source.error && !ready && <p className="text-xs text-muted-foreground">Run replicates for this experiment version and record measurements to enable statistics.</p>}
    {error && <p role="alert" className="text-xs text-destructive">{error.message}</p>}
    {history.isLoading && <p role="status" className="text-xs text-muted-foreground">Loading statistics…</p>}
    {pending && <p role="status" className="text-xs text-muted-foreground">Metrics are being analyzed. You can leave this tab and return later.</p>}
    {failed.map(a => <p key={a.id} role="alert" className="break-words text-xs text-destructive">{a.config.metric}: {a.error || 'Analysis failed. Please rerun the analysis.'}</p>)}
    {version && !history.isLoading && !history.error && !pending && reports.length === 0 && <p className="text-xs text-muted-foreground">No statistical reports are available for this experiment version. Analyze all metrics after collecting sufficient experiment results.</p>}
    <div className="space-y-2">
      {reports.map(analysis => <ReportRow key={analysis.id} experiment={experiment} analysis={analysis} />)}
    </div>
  </div>
}

function ReportRow({ experiment, analysis }: { experiment: string; analysis: StatsAnalysis }) {
  const [open, setOpen] = useState(false)
  const report = useQuery({
    queryKey: ['stats-analysis', experiment, analysis.id],
    queryFn: () => statsApi.get(experiment, analysis.id),
    enabled: open,
    staleTime: 0,
    refetchOnMount: 'always',
  })
  return <div className="overflow-hidden rounded-md border border-border">
    <button type="button" aria-expanded={open} aria-controls={`stats-report-${analysis.id}`}
      onClick={() => setOpen(value => !value)}
      className="flex w-full cursor-pointer items-center gap-2 p-3 text-left text-xs transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
      <ChevronRight aria-hidden="true" className={`size-3.5 shrink-0 transition-transform ${open ? 'rotate-90' : ''}`} />
      <span className="min-w-0 flex-1 space-y-1">
        <span className="block break-words text-[0.95625rem] font-semibold">{analysis.config.metric}</span>
        <span className="block text-muted-foreground">{new Date(analysis.created_at).toLocaleString()}</span>
      </span>
    </button>
    <div id={`stats-report-${analysis.id}`} hidden={!open} className="border-t">
      {open && <>
        {report.isLoading && <p role="status" className="p-3 text-xs text-muted-foreground">Loading report…</p>}
        {report.error && <p role="alert" className="p-3 text-xs text-destructive">{report.error.message}</p>}
        {report.data?.output?.summary?.status === 'valid' && <StatisticsReport analysis={report.data} experiment={experiment} />}
      </>}
    </div>
  </div>
}
