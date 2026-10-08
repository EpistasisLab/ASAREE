import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { expect, it, vi } from 'vitest'
import { llmSettingsApi } from '@/api/client'
import { useProviderModels } from './protocol/useProviderModels'
import { useConnectionCheck } from './LlmConnectionCheck'

it('refreshes the Azure model menu immediately after testing the connection', async () => {
  vi.spyOn(llmSettingsApi, 'list').mockResolvedValue([
    { provider: 'azure_foundry', api_base: 'resource', azure_project_endpoint: 'project' },
  ])
  const models = vi.spyOn(llmSettingsApi, 'listModels').mockResolvedValue({ models: [], source: 'api', note: null })
  vi.spyOn(llmSettingsApi, 'testConnection').mockResolvedValue({ provider: 'azure_foundry', status: 'ok', detail: 'Connected', endpoint: 'project' })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result, unmount } = renderHook(() => ({
    menu: useProviderModels('azure_foundry'),
    check: useConnectionCheck('azure_foundry'),
  }), { wrapper })
  await waitFor(() => expect(result.current.menu.modelsQuery.isSuccess).toBe(true))
  models.mockResolvedValue({ models: [{ id: 'new-deployment', label: null, supports_temperature: true, supports_effort: false, effort_levels: [], supports_tool_calling: null }], source: 'api', note: null })
  await act(async () => { await result.current.check.mutateAsync() })
  await waitFor(() => expect(result.current.menu.models[0]?.id).toBe('new-deployment'))
  unmount()
  client.clear()
})
