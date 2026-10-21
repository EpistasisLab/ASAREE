import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { experimentsApi, type ResultsScope } from '@/api/client'
import type { ExecutionSummary } from '@/types/executionResults'
import { EXECUTION_PAGE_SIZE } from './executionPagination'

export const executionKeys = {
  root: (id: string) => ['experiments', id, 'execution-results'] as const,
  summary: (id: string, scope: ResultsScope) => [...executionKeys.root(id), 'summary', scope] as const,
}

export async function preloadExecutionCell(client: QueryClient, experimentId: string, scope: ResultsScope, cellId: string) {
  const summary = client.getQueryData<ExecutionSummary>(executionKeys.summary(experimentId, scope))
  const params = { cell_id: cellId, cursor: undefined, limit: EXECUTION_PAGE_SIZE, search: undefined, status: undefined }
  const root = executionKeys.root(experimentId)
  await Promise.all([
    client.fetchQuery({ queryKey: [...root, 'cell', scope, cellId], staleTime: 5000,
      queryFn: ({ signal }) => experimentsApi.getExecutionCell(experimentId, scope, { cell_id: cellId }, signal) }),
    client.fetchQuery({ queryKey: [...root, 'executions', scope, summary?.source_row_count, params], staleTime: 5000,
      queryFn: ({ signal }) => experimentsApi.listExecutions(experimentId, scope, params, signal) }),
    client.fetchQuery({ queryKey: [...root, 'usage-distribution', scope, cellId, 'total_tokens'], staleTime: 5000,
      queryFn: ({ signal }) => experimentsApi.getUsageDistribution(experimentId, scope, cellId, 'total_tokens', signal) }),
  ])
}

export async function preloadExecutionSlot(client: QueryClient, experimentId: string, scope: ResultsScope, slotId: string) {
  const root = executionKeys.root(experimentId)
  const history = await client.fetchQuery({ queryKey: [...root, 'attempts', scope, slotId, undefined], staleTime: 5000,
    queryFn: ({ signal }) => experimentsApi.listExecutionAttempts(experimentId, slotId, scope, undefined, signal) })
  const runId = history.slot.run_id ?? history.items[0]?.run_id
  if (runId) await client.fetchQuery({ queryKey: [...root, 'attempt', scope, runId], staleTime: 5000,
    queryFn: ({ signal }) => experimentsApi.getExecutionAttempt(experimentId, runId, scope, signal) })
}

export function useExecutionSummary(experimentId: string, scope: ResultsScope, enabled = true) {
  const client = useQueryClient()
  const queryKey = executionKeys.summary(experimentId, scope)
  return useQuery({
    queryKey,
    queryFn: ({ signal }) => experimentsApi.getExecutionSummary(experimentId, scope, signal, client.getQueryData<ExecutionSummary>(queryKey)),
    enabled,
    refetchInterval: query => {
      const data = query.state.data
      return !data || !data.ready || data.refreshing || data.has_active ? 5000 : false
    },
    retry: 1,
  })
}
