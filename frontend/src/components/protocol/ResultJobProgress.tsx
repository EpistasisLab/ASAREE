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
  const checkpoint = job.data ? JSON.stringify([job.data.status, job.data.progress.created, job.data.progress.cancelled]) : undefined
  useEffect(() => {
    if (checkpoint) void client.invalidateQueries({ queryKey: executionKeys.root(experimentId) })
  }, [checkpoint, client, experimentId])
  const cancel = useMutation({ mutationFn: () => experimentsApi.cancelResultJob(experimentId, jobId!), onSuccess: () => { void job.refetch() } })
  if (!jobId) return null
  return <div className="space-y-1 text-xs" role="status">
    <p className="font-mono">{job.data?.kind === 'cancel' ? 'Stopping run attempts' : 'Batch'} · {job.data?.status.replaceAll('_', ' ') ?? 'loading'} · {job.data?.progress.created ?? 0} queued · {job.data?.progress.skipped ?? 0} skipped{job.data?.progress.cancelled != null && ` · ${job.data.progress.cancelled} stopped`}</p>
    {job.data?.kind === 'plan' && jobActive(job.data) && <Button variant="outline" size="xs" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel remaining planning</Button>}
    {(job.data?.error || job.error || cancel.error) && <p role="alert" className="text-destructive">{job.data?.error ?? job.error?.message ?? cancel.error?.message}</p>}
  </div>
}
