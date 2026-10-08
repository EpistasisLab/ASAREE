import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { expect, it, vi } from 'vitest'
import { llmSettingsApi } from '@/api/client'
import { providerModelsKey, useProviderModels } from './protocol/useProviderModels'
import { useConnectionCheck } from './LlmConnectionCheck'

it.each([false, true])('uses all four checked deployments with an old request in flight: %s', async (inFlight) => {
  vi.spyOn(llmSettingsApi, 'list').mockResolvedValue([
    { provider: 'azure_foundry', api_base: 'resource', azure_project_endpoint: 'project' },
  ])
  const deployments = ['FW-GLM-5.3', 'claude-sonnet-5', 'claude-sonnet-5-5', 'new-deployment'].map((id) => ({
    id, label: id, supports_temperature: true, supports_effort: false, effort_levels: [], supports_tool_calling: null,
  }))
  const models = vi.spyOn(llmSettingsApi, 'listModels').mockResolvedValue({ models: deployments.slice(0, 3), source: 'api', note: null })
  vi.spyOn(llmSettingsApi, 'testConnection').mockResolvedValue({ provider: 'azure_foundry', status: 'ok', detail: 'Listed 4 deployments', endpoint: 'project', models: deployments })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result, unmount } = renderHook(() => ({
    menu: useProviderModels('azure_foundry'),
    check: useConnectionCheck('azure_foundry'),
  }), { wrapper })
  await waitFor(() => expect(result.current.menu.modelsQuery.isSuccess).toBe(true))
  expect(result.current.menu.models).toHaveLength(3)
  const oldResponse = { models: deployments.slice(0, 3), source: 'api' as const, note: null }
  let finishOldRequest: (() => void) | undefined
  let oldRequest: Promise<void> | undefined
  if (inFlight) {
    models.mockImplementationOnce(() => new Promise((resolve) => { finishOldRequest = () => resolve(oldResponse) }))
    act(() => { oldRequest = client.refetchQueries({ queryKey: providerModelsKey('azure_foundry') }) })
  }
  await act(async () => { await result.current.check.mutateAsync() })
  await act(async () => { finishOldRequest?.(); await oldRequest })
  await waitFor(() => expect(result.current.menu.models.map((model) => model.id)).toEqual(deployments.map((model) => model.id)))
  expect(models).toHaveBeenCalledTimes(inFlight ? 2 : 1)
  unmount()
  client.clear()
})
