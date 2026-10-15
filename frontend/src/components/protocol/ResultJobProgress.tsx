import { useMutation } from '@tanstack/react-query'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { experimentsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { jobActive, useResultJob } from '@/lib/resultJobs'
import { executionKeys } from '@/lib/executionQueries'

export function ResultJobProgress({ experimentId, jobId }: { experimentId: string; jobId?: string }) {
  const job = useResultJob(experimentId, jobId)
  const client = useQueryClient()
  const checkpoint = job.data ? JSON.stringify([job.data.status, job.data.progress.created, job.data.progress.cancelled, job.data.execution_counts]) : undefined
  useEffect(() => {
    if (checkpoint) void client.invalidateQueries({ queryKey: executionKeys.root(experimentId) })
  }, [checkpoint, client, experimentId])
  const cancel = useMutation({ mutationFn: () => experimentsApi.cancelResultJob(experimentId, jobId!), onSuccess: () => { void job.refetch() } })
  if (!jobId) return null
  const counts = job.data?.execution_counts
  const running = ['pending', 'running', 'finalizing'].some(status => (counts?.[status] ?? 0) > 0)
  const status = job.data?.kind === 'plan' && job.data.status === 'completed'
    ? running ? 'Running' : (counts?.failed ?? 0) > 0 ? 'Finished with failures' : 'Finished'
    : job.data?.status.replaceAll('_', ' ') ?? 'loading'
  return <div className="space-y-1 text-xs" role="status">
    <p className="font-mono">{job.data?.kind === 'cancel' ? 'Stopping executions' : 'Batch'} · {status} · {job.data?.progress.created ?? 0} attempts created · {job.data?.progress.skipped ?? 0} skipped{job.data?.progress.cancelled != null && ` · ${job.data.progress.cancelled} stopped`}</p>
    {counts && <p>{counts.pending ?? 0} queued · {(counts.running ?? 0) + (counts.finalizing ?? 0)} running · {counts.completed ?? 0} completed · {counts.failed ?? 0} failed · {counts.limit_reached ?? 0} limited · {counts.scoring_failed ?? 0} scoring failed · {counts.cancelled ?? 0} stopped</p>}
    {job.data?.kind === 'plan' && jobActive(job.data) && <Button variant="outline" size="xs" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel remaining planning</Button>}
    {(job.data?.error || job.error || cancel.error) && <p role="alert" className="text-destructive">{job.data?.error ?? job.error?.message ?? cancel.error?.message}</p>}
  </div>
}
