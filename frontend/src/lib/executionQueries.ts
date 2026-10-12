import { useQuery, useQueryClient } from '@tanstack/react-query'
import { experimentsApi, type ResultsScope } from '@/api/client'
import type { ExecutionSummary } from '@/types/executionResults'

export const executionKeys = {
  root: (id: string) => ['experiments', id, 'execution-results'] as const,
  summary: (id: string, scope: ResultsScope) => [...executionKeys.root(id), 'summary', scope] as const,
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
