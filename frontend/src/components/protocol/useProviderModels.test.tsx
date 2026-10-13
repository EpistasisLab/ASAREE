import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { llmSettingsApi } from '@/api/client'
import type { LLMSettingModelsResponse } from '@/types/llmSettings'
import { modelCapabilitiesKey, useProviderModels } from './useProviderModels'
import { findNodeConfigIssues } from './nodeConfigIssues'
import type { ModelNodeData } from '@/types/protocols'

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

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

it.each([
  ['gpt-4o-2024-08-06', true, false, null],
  ['truly-unknown', false, true, 'medium'],
] as const)('uses backend capabilities for off-catalog %s and run warnings', async (model, temperature, effort, defaultEffort) => {
  vi.useFakeTimers()
  vi.spyOn(llmSettingsApi, 'list').mockResolvedValue([])
  vi.spyOn(llmSettingsApi, 'listModels').mockResolvedValue({ models: [], source: 'static', note: null })
  const metadata = { supports_temperature: temperature, supports_effort: effort, effort_levels: effort ? ['low', 'medium', 'high'] : [], default_effort: defaultEffort }
  const resolve = vi.spyOn(llmSettingsApi, 'modelCapabilities').mockResolvedValue(metadata)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result, unmount } = renderHook(() => useProviderModels('openai', model), { wrapper })
  expect(result.current.capabilities).toBeUndefined()
  expect(result.current.capabilitiesPending).toBe(true)
  await act(async () => { await vi.advanceTimersByTimeAsync(100) })
  expect(resolve).toHaveBeenCalledWith('openai', model)
  expect(result.current.capabilities).toEqual(metadata)
  expect(client.getQueryData(modelCapabilitiesKey('openai', model))).toEqual(metadata)
  const issues = findNodeConfigIssues([{
    id: 'model', type: 'model_openai', position: { x: 0, y: 0 },
    data: { label: 'Model', config: { provider: 'openai', model, max_tokens: 1000 } } as ModelNodeData,
  }], [], client)
  expect(issues.flatMap((issue) => issue.issues)).toEqual(temperature ? ['Temperature is required'] : [])
  unmount()
  client.clear()
})

it('debounces changed custom IDs and never displays the previous model capabilities', async () => {
  vi.useFakeTimers()
  vi.spyOn(llmSettingsApi, 'list').mockResolvedValue([])
  vi.spyOn(llmSettingsApi, 'listModels').mockResolvedValue({ models: [], source: 'static', note: null })
  const metadata = { supports_temperature: false, supports_effort: true, effort_levels: ['medium'], default_effort: 'medium' }
  const resolve = vi.spyOn(llmSettingsApi, 'modelCapabilities').mockResolvedValue(metadata)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result, rerender, unmount } = renderHook(({ model }) => useProviderModels('openai', model), { wrapper, initialProps: { model: 'first' } })
  await act(async () => { await vi.advanceTimersByTimeAsync(100) })
  expect(result.current.capabilities).toEqual(metadata)
  rerender({ model: 'next' })
  expect(result.current.capabilities).toBeUndefined()
  await act(async () => { await vi.advanceTimersByTimeAsync(100) })
  rerender({ model: 'next-model' })
  await act(async () => { await vi.advanceTimersByTimeAsync(400) })
  expect(resolve.mock.calls.map((call) => call[1])).toEqual(['first', 'next-model'])
  await act(async () => { await vi.advanceTimersByTimeAsync(100) })
  expect(result.current.capabilities).toEqual(metadata)
  unmount()
  client.clear()
})

it('does not resolve custom capabilities when an explicit override is present', async () => {
  vi.useFakeTimers()
  vi.spyOn(llmSettingsApi, 'list').mockResolvedValue([])
  vi.spyOn(llmSettingsApi, 'listModels').mockResolvedValue({ models: [], source: 'static', note: null })
  const resolve = vi.spyOn(llmSettingsApi, 'modelCapabilities')
  const override = { supports_temperature: true, supports_effort: false, effort_levels: [], default_effort: null }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result, unmount } = renderHook(() => useProviderModels('openai', 'custom', override), { wrapper })
  await act(async () => { await vi.advanceTimersByTimeAsync(400) })
  expect(resolve).not.toHaveBeenCalled()
  expect(result.current.capabilities).toEqual(override)
  unmount()
  client.clear()
})

it('reports a failed custom lookup instead of guessing effort', async () => {
  vi.useFakeTimers()
  vi.spyOn(llmSettingsApi, 'list').mockResolvedValue([])
  vi.spyOn(llmSettingsApi, 'listModels').mockResolvedValue({ models: [], source: 'static', note: null })
  vi.spyOn(llmSettingsApi, 'modelCapabilities').mockRejectedValue(new Error('offline'))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result, unmount } = renderHook(() => useProviderModels('openai', 'custom'), { wrapper })
  await act(async () => { await vi.advanceTimersByTimeAsync(100) })
  expect(result.current.capabilities).toBeUndefined()
  expect(result.current.capabilitiesError).toBe('Could not resolve model capabilities. Try again.')
  const issues = findNodeConfigIssues([{
    id: 'model', type: 'model_openai', position: { x: 0, y: 0 },
    data: { label: 'Model', config: { provider: 'openai', model: 'custom', max_tokens: 1000 } } as ModelNodeData,
  }], [], client)
  expect(issues[0].issues).toEqual(['Could not resolve model capabilities. Try again.'])
  unmount()
  client.clear()
})
