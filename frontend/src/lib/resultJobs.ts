import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { experimentsApi, type ResultsScope } from '@/api/client'
import type { BatchSelection, ResultJob } from '@/types/executionResults'
import { newRequestKey } from './requestKey'

export const jobActive = (job?: ResultJob) => !!job && ['pending', 'queued', 'running'].includes(job.status)

export function useResultJob(experimentId: string, id?: string) {
  return useQuery({ queryKey: ['experiments', experimentId, 'result-job', id],
    queryFn: ({ signal }) => experimentsApi.getResultJob(experimentId, id!, signal), enabled: !!id,
    refetchInterval: query => jobActive(query.state.data) || ['pending', 'running', 'finalizing'].some(status =>
      (query.state.data?.execution_counts?.[status] ?? 0) > 0) ? 1000 : false })
}

export function useBatchPreview(experimentId: string, scope: ResultsScope, selection: BatchSelection, enabled: boolean) {
  const key = JSON.stringify({ scope, selection, enabled })
  const request = useRef({ key, id: newRequestKey() })
  if (request.current.key !== key) request.current = { key, id: newRequestKey() }
  const requestKey = request.current.id
  const prepare = useQuery({ queryKey: ['experiments', experimentId, 'batch-preview', requestKey],
    queryFn: ({ signal }) => experimentsApi.createResultJob(experimentId, scope, { kind: 'plan', request_key: requestKey, ...selection }, signal),
    enabled: enabled && !!scope.protocol_revision_id, staleTime: Infinity })
  const job = useResultJob(experimentId, enabled ? prepare.data?.id : undefined)
  useEffect(() => {
    const id = prepare.data?.id
    return () => { if (id) void experimentsApi.cancelResultJob(experimentId, id, true).catch(() => {}) }
  }, [experimentId, prepare.data?.id, enabled])
  return { data: job.data ?? prepare.data, error: job.error ?? prepare.error, isFetching: prepare.isFetching || job.isFetching }
}
