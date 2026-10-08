import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { llmSettingsApi } from '@/api/client'
import type { LLMSettingModelsResponse } from '@/types/llmSettings'
import { useProviderModels } from './useProviderModels'

afterEach(() => vi.useRealTimers())

it('automatically refreshes a mounted provider model list without navigation', async () => {
  vi.useFakeTimers()
  vi.spyOn(llmSettingsApi, 'list').mockResolvedValue([])
  const response = (id: string): LLMSettingModelsResponse => ({
    models: [{ id, label: id, supports_temperature: true, supports_effort: false, effort_levels: [], supports_tool_calling: true }],
    source: 'api',
    note: null,
  })
  const listModels = vi.spyOn(llmSettingsApi, 'listModels')
    .mockResolvedValueOnce(response('original-model'))
    .mockResolvedValue(response('new-model'))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result, unmount } = renderHook(() => useProviderModels('local'), { wrapper })
  await act(async () => { await vi.advanceTimersByTimeAsync(1) })
  expect(result.current.models[0]?.id).toBe('original-model')
  await act(async () => { await vi.advanceTimersByTimeAsync(20 * 60 * 1000) })
  expect(listModels.mock.calls.length).toBeGreaterThan(1)
  expect(result.current.models[0]?.id).toBe('new-model')
  unmount()
  client.clear()
})
