import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Download, Maximize2, Minimize2, type LucideIcon } from 'lucide-react'
import { experimentsApi, type ResultsScope } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { sanitizeFilename } from '@/lib/utils'
import { jobActive, useResultJob } from '@/lib/resultJobs'
import { InfoTooltip } from './InfoTooltip'

export function ResultsScorecard({ label, help, value, note, icon: Icon }: { label: string; help: string; value: string; note?: string; icon: LucideIcon }) {
  return <div className="rounded-md border bg-card px-2.5 py-2 transition-colors hover:bg-muted/30">
    <div className="flex items-center justify-between gap-2 text-muted-foreground"><span className="flex items-center gap-1 text-xs">{label}<InfoTooltip>{help}</InfoTooltip></span><Icon className="size-3.5" aria-hidden="true" /></div>
    <p className="mt-1 truncate text-base font-medium tabular-nums" title={value}>{value}</p>
    {note && <p className="mt-0.5 text-[11px] text-muted-foreground">{note}</p>}
  </div>
}

/** One panel structure for both modes; data-specific content occupies these slots. */
export function ResultsPanel({ experimentId, experimentName, scope, controls, summary, scorecards, notice, comparison, cells, details }: {
  experimentId: string; experimentName: string; scope: ResultsScope
  controls?: ReactNode; summary: ReactNode; scorecards?: ReactNode; notice?: ReactNode
  comparison?: ReactNode; cells: ReactNode; details?: ReactNode
}) {
  const [maximized, setMaximized] = useState(false)
  const [jobId, setJobId] = useState<string>()
  const downloaded = useRef<string | undefined>(undefined)
  const job = useResultJob(experimentId, jobId)
  const createExport = useMutation({ mutationFn: () => experimentsApi.createResultJob(experimentId, scope,
    { kind: 'export', request_key: crypto.randomUUID() }), onSuccess: created => setJobId(created.id) })
  const downloading = createExport.isPending || (!!jobId && !job.data && !job.error) || jobActive(job.data)
  const error = createExport.error?.message ?? job.error?.message ?? job.data?.error
  useEffect(() => {
    if (job.data?.status !== 'completed' || !job.data.download_url || downloaded.current === job.data.id) return
    downloaded.current = job.data.id
    const link = document.createElement('a')
    link.href = job.data.download_url
    link.download = `${sanitizeFilename(experimentName, 'experiment')}-results.csv`
    link.click()
  }, [job.data, experimentName])
  useEffect(() => {
    if (!maximized) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setMaximized(false) }
    document.addEventListener('keydown', close)
    return () => { document.body.style.overflow = previous; document.removeEventListener('keydown', close) }
  }, [maximized])
  return <div className={maximized ? '@container fixed inset-0 z-50 space-y-4 overflow-auto bg-background p-4' : '@container space-y-4 p-3'}>
    <section className="space-y-2">
      {controls}
      <Card size="sm" className="flex-row flex-wrap items-start justify-between gap-3 border-primary/25 bg-primary/5 p-3">
        {summary}
        <div className="flex shrink-0 flex-wrap gap-1.5">
          <Button variant="outline" size="xs" onClick={() => setMaximized(value => !value)}>{maximized ? <Minimize2 className="size-3" /> : <Maximize2 className="size-3" />}{maximized ? 'Restore Results' : 'Maximize Results'}</Button>
          <Button variant="outline" size="xs" disabled={downloading} onClick={() => createExport.mutate()}>{downloading ? 'Preparing…' : <><Download className="size-3" /> Download CSV</>}</Button>
        </div>
      </Card>
      {scorecards && <div className="mt-2 grid grid-cols-2 gap-2">{scorecards}</div>}
      {notice}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      {downloading && <p role="status" className="font-mono text-xs">Preparing CSV · {job.data?.progress.bytes?.toLocaleString() ?? 0} bytes</p>}
      {job.data?.status === 'completed' && <div className="flex gap-3 text-xs"><a className="text-primary underline" href={job.data.download_url}>Download prepared CSV</a><a className="text-primary underline" href={job.data.schema_url}>CSV schema</a></div>}
    </section>
    {comparison}
    <section className="space-y-2">
      <div><h2 className="flex items-center gap-1 text-sm font-medium">Cell results<InfoTooltip>Each card groups replicates that share the same experimental factor levels. Expand one to compare individual runs.</InfoTooltip></h2><p className="text-xs text-muted-foreground">Expand a condition to inspect its replicates, metrics, usage, outputs, and node activity.</p></div>
      {cells}
    </section>
    {details}
  </div>
}
