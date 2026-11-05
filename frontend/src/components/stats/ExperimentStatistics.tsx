import { useId, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight } from 'lucide-react'
import { statsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { StatisticsReport } from './StatisticsReport'
import { statsUnavailableReason } from '@/lib/statsFindings'
import type { StatsAnalysis, StatsMetricSource } from '@/types/stats'

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
    onSuccess: () => Promise.all([
      client.invalidateQueries({ queryKey: historyKey }),
      client.invalidateQueries({ queryKey: ['statistics-source', experiment, version] }),
    ]),
  })
  const latest = new Map<string, StatsAnalysis>()
  for (const analysis of history.data ?? []) {
    if (analysis.config.family === 'auto' && !latest.has(analysis.config.metric)) {
      latest.set(analysis.config.metric, analysis)
    }
  }
  const analyses = [...latest.values()]
  const metrics = source.data?.metrics.length ? source.data.metrics
    : analyses.map(a => ({ name: a.config.metric, n: 0 }))
  const changed = !!source.data && analyses.some(a =>
    a.status !== 'pending' && a.status !== 'running' && a.fingerprint !== source.data.fingerprint)
  const pending = run.isPending || analyses.some(a => a.status === 'pending' || a.status === 'running')
  const reports = analyses.filter(a => (a.status === 'completed' || a.status === 'incomplete') && a.report_status === 'valid')
  const failed = analyses.filter(a => a.status === 'failed' || a.status === 'incomplete')
  const error = source.error || history.error || run.error
  const ready = !!version && !source.isError && (source.data?.n_measured_replicates ?? 0) > 0

  return <div className="@container space-y-3 p-3">
    <p className="text-xs text-muted-foreground">Analyze metrics from the experiment version being viewed. Methods are selected automatically. Rerunning replaces this version’s existing reports.</p>
    <div className="space-y-1 rounded-md border border-border bg-muted/20 p-3 text-xs text-muted-foreground">
      <p>Whole-dataset replicates are treated as independent observations.</p>
      <p>Model fitting needs at least <strong className="text-foreground">5 usable measurements overall</strong> and <strong className="text-foreground">3 per observed cell</strong>, for each metric. These minimums do not guarantee a reliable comparison. Statistical inference is unavailable when all measured outcomes are identical.</p>
      <p>Missing measurements are excluded, not treated as zero. Each metric may use a different set of completed replicates.</p>
    </div>
    {changed && !pending && <p role="status" className="rounded-md border border-[color:var(--chart-4)]/40 p-3 text-xs text-[color:var(--chart-4)]">New or changed results are available for this version. Rerun analysis to update the reports.</p>}
    <Button size="sm" disabled={!ready || pending || history.isLoading} onClick={() => run.mutate()}>
      {pending ? 'Analyzing metrics…' : 'Analyze all metrics'}
    </Button>
    {!version && <p className="text-xs text-muted-foreground">Publish the experiment and run replicates to enable statistics.</p>}
    {version && !source.isLoading && !source.error && !ready && <p className="text-xs text-muted-foreground">Run replicates for this experiment version and record measurements to enable statistics.</p>}
    {error && <p role="alert" className="text-xs text-destructive">{error.message}</p>}
    {history.isLoading && <p role="status" className="text-xs text-muted-foreground">Loading statistics…</p>}
    {pending && <p role="status" className="text-xs text-muted-foreground">Metrics are being analyzed. You can leave this tab and return later.</p>}
    {failed.map(a => <p key={a.id} role="alert" className="break-words text-xs text-destructive">{a.config.metric}: {a.error || (a.status === 'incomplete' ? 'Analysis incomplete. Rerun to finish the requested procedures.' : 'Analysis failed. Please rerun the analysis.')}</p>)}
    {version && !history.isLoading && !history.error && !pending && reports.length === 0 && <p className="text-xs text-muted-foreground">No statistical reports are available for this experiment version. {analyses.length ? 'Open a metric below to see why.' : 'Analyze all metrics after collecting sufficient experiment results.'}</p>}
    <div className="space-y-2">
      {metrics.map(metric => <ReportRow key={metric.name} experiment={experiment} metric={metric} analysis={latest.get(metric.name)} sourceFingerprint={source.data?.fingerprint} />)}
    </div>
  </div>
}

function ReportRow({ experiment, metric, analysis, sourceFingerprint }: {
  experiment: string; metric: StatsMetricSource; analysis?: StatsAnalysis; sourceFingerprint?: string
}) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const finished = analysis?.status === 'completed' || analysis?.status === 'incomplete'
  const report = useQuery({
    queryKey: ['stats-analysis', experiment, analysis?.id],
    queryFn: () => statsApi.get(experiment, analysis!.id),
    enabled: open && finished,
    staleTime: 0,
    refetchOnMount: 'always',
  })
  const reportSummary = report.data?.output?.summary
  const unavailableReason = reportSummary ? statsUnavailableReason(reportSummary) : null
  const valid = reportSummary ? !unavailableReason : analysis?.report_status === 'valid'
  const status = !analysis ? (metric.availability_reason?.startsWith('Not enough') ? 'Needs measurements' : 'Not analyzed')
    : analysis.status === 'pending' ? 'Queued' : analysis.status === 'running' ? 'Analyzing'
    : analysis.status === 'failed' ? 'Analysis failed' : analysis.status === 'incomplete' ? 'Analysis incomplete'
    : valid ? 'Report ready' : 'Report unavailable'
  const statusColor = {
    'Needs measurements': 'text-[color:var(--chart-4)]',
    'Not analyzed': 'text-muted-foreground',
    'Queued': 'text-[color:var(--chart-4)]',
    'Analyzing': 'text-primary',
    'Analysis failed': 'text-destructive',
    'Analysis incomplete': 'text-[color:var(--chart-4)]',
    'Report ready': 'text-[color:var(--chart-3)]',
    'Report unavailable': 'text-[color:var(--chart-4)]',
  }[status]
  const outdated = finished && sourceFingerprint && analysis.fingerprint !== sourceFingerprint
  const reason = analysis?.error || unavailableReason || (!valid && analysis?.report_warnings?.find(Boolean))
    || (finished && !valid && !outdated && metric.availability_reason)
    || (!analysis && metric.availability_reason)
  return <div className="overflow-hidden rounded-md border border-border">
    <Button variant="ghost" aria-expanded={open} aria-controls={id}
      onClick={() => setOpen(value => !value)}
      className="h-auto w-full justify-start whitespace-normal p-3 text-left text-xs">
      <ChevronRight aria-hidden="true" className={`size-3.5 shrink-0 transition-transform ${open ? 'rotate-90' : ''}`} />
      <span className="min-w-0 flex-1 space-y-1">
        <span className="block break-words text-[0.95625rem] font-semibold">{metric.name}</span>
        <span className="flex flex-wrap gap-1.5">
          <Badge variant="outline" className={`border-current ${statusColor}`}>{status}</Badge>
          {outdated && <Badge variant="outline" className="border-[color:var(--chart-4)]/40 text-[color:var(--chart-4)]">Results changed</Badge>}
        </span>
        {metric.n_usable != null && <span className="block font-mono text-muted-foreground">Current: {metric.n_usable} usable measurements · least-measured observed cell: {metric.minimum_per_cell ?? 0}</span>}
        {metric.n_usable == null && <span className="block font-mono text-muted-foreground">{metric.n} recorded measurements</span>}
        {reason && !open && <span className="block break-words text-muted-foreground">{reason}</span>}
        {analysis && <span className="block text-muted-foreground">{new Date(analysis.created_at).toLocaleString()}</span>}
      </span>
    </Button>
    <div id={id} hidden={!open} className="border-t">
      {open && <>
        {!analysis && <p className="p-3 text-xs text-muted-foreground">{metric.availability_reason || 'No report has been generated. Select Analyze all metrics to create one.'}</p>}
        {analysis?.status === 'failed' && <p className="p-3 text-xs text-destructive">{analysis.error || 'Analysis failed. Rerun analysis to try again.'}</p>}
        {(analysis?.status === 'pending' || analysis?.status === 'running') && <p role="status" className="p-3 text-xs text-muted-foreground">This metric is being analyzed. You can return later.</p>}
        {report.isLoading && <p role="status" className="p-3 text-xs text-muted-foreground">Loading report…</p>}
        {report.error && <p role="alert" className="p-3 text-xs text-destructive">{report.error.message}</p>}
        {report.data?.output?.summary && <StatisticsReport analysis={report.data} experiment={experiment} />}
      </>}
    </div>
  </div>
}
